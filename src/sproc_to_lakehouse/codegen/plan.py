"""Turn an analysed procedure into an ordered, target-neutral conversion plan.

A :class:`Plan` is a list of :class:`Step` objects whose SQL is already Databricks-ready
(variables substituted, temp tables folded into CTEs, rewrites applied). The dbt and
notebook generators only decide *how to package* each step.

Anything the analyzer rated ``needs_review`` becomes a ``MANUAL`` step carrying the
original T-SQL and the reasons, so nothing is silently dropped or guessed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from sqlglot import exp

from sproc_to_lakehouse.analyzer import rewrites as rw
from sproc_to_lakehouse.analyzer.registry import is_temp_cleanup, parameter_default
from sproc_to_lakehouse.analyzer.rules.state import assess_transient, transient_usage
from sproc_to_lakehouse.catalog import Catalog
from sproc_to_lakehouse.codegen.sql_rewrites import (
    RewriteError,
    apply_expression_rewrites,
    needs_merge_rewrite,
    update_from_to_merge,
)
from sproc_to_lakehouse.codegen.variables import Unresolved, VariableEnv
from sproc_to_lakehouse.models import (
    Block,
    Finding,
    IfNode,
    Node,
    ProcedureAnalysis,
    Severity,
    Statement,
    StatementKind,
    TableKind,
    TableRef,
    TryCatchNode,
    WhileNode,
)
from sproc_to_lakehouse.parser.lexer import TokenKind
from sproc_to_lakehouse.parser.tables import table_ref
from sproc_to_lakehouse.sqltypes import TypeResolver, transient_schemas
from sproc_to_lakehouse.typed_rewrites import bit_to_boolean, changed, compact_date_literal

K = StatementKind
_DML = {K.INSERT, K.UPDATE, K.DELETE, K.MERGE, K.TRUNCATE, K.SELECT_INTO}
_CURSOR_KINDS = {
    K.DECLARE_CURSOR,
    K.OPEN_CURSOR,
    K.FETCH_CURSOR,
    K.CLOSE_CURSOR,
    K.DEALLOCATE_CURSOR,
}
_ALWAYS_MANUAL = {
    *_CURSOR_KINDS, K.EXEC_PROCEDURE, K.EXEC_DYNAMIC, K.BEGIN_TRANSACTION, K.COMMIT,
    K.ROLLBACK, K.THROW, K.RAISERROR, K.BREAK, K.CONTINUE, K.OTHER, K.CREATE_INDEX,
}  # fmt: skip


_VOLATILE = (exp.CurrentTimestamp, exp.CurrentDate, exp.CurrentDatetime, exp.CurrentTime)


class StepKind(str, Enum):
    WRITE = "write"  # DML against a permanent table
    RESULT = "result"  # SELECT returned to the caller
    TEMP_VIEW = "temp_view"  # temp table / table variable read by several steps
    SCALAR = "scalar"  # variable loaded from a query
    PRINT = "print"
    MANUAL = "manual"


class TransientMode(str, Enum):
    INLINE = "inline"  # becomes a CTE in its single consumer
    SHARED = "shared"  # becomes a temp view (notebook) / ephemeral model (dbt)
    MANUAL = "manual"  # mutated across steps: needs a human


@dataclass
class Transient:
    ref: TableRef
    mode: TransientMode
    cte_name: str
    columns: list[str] = field(default_factory=list)
    queries: list[exp.Expression] = field(default_factory=list)  # one per INSERT
    depends_on: list[str] = field(default_factory=list)  # other transients' names

    @property
    def query(self) -> exp.Expression:
        """The defining query; several INSERTs are combined with UNION ALL."""
        result = self.queries[0].copy()
        for extra in self.queries[1:]:
            result = exp.Union(this=result, expression=extra.copy(), distinct=False)
        return result


@dataclass
class Step:
    kind: StepKind
    title: str
    line: int
    source_sql: str
    sql: exp.Expression | None = None
    target: TableRef | None = None
    operation: str | None = None  # insert | merge | update | delete | truncate
    name: str | None = None  # SCALAR variable / TEMP_VIEW name
    reasons: list[str] = field(default_factory=list)  # MANUAL: why
    rewrites: set[str] = field(default_factory=set)


@dataclass
class DroppedStatement:
    line: int
    source_sql: str
    reason: str


@dataclass
class Plan:
    analysis: ProcedureAnalysis
    env: VariableEnv
    steps: list[Step]
    dropped: list[DroppedStatement]
    transients: dict[str, Transient]

    @property
    def name(self) -> str:
        return self.analysis.procedure.name

    @property
    def manual_steps(self) -> list[Step]:
        return [s for s in self.steps if s.kind is StepKind.MANUAL]

    @property
    def is_fully_converted(self) -> bool:
        return not self.manual_steps

    def rewrites_used(self) -> set[str]:
        return {r for s in self.steps for r in s.rewrites}


def _transient_name(ref: TableRef) -> str:
    return "tmp_" + ref.name.lstrip("#@")


class PlanBuilder:
    def __init__(
        self, analysis: ProcedureAnalysis, catalog: Catalog | None = None, *, fixes: bool = True
    ) -> None:
        self.analysis = analysis
        self.fixes = fixes
        self.procedure = analysis.procedure
        self.resolver = (
            TypeResolver(catalog, transient_schemas(self.procedure.statements()))
            if catalog is not None
            else None
        )
        self.env = VariableEnv.for_parameters(self.procedure.parameters)
        self.steps: list[Step] = []
        self.dropped: list[DroppedStatement] = []
        self.transients = self._classify_transients()
        self.review = [f for f in analysis.findings if f.severity is Severity.NEEDS_REVIEW]

    # -- set-up ----------------------------------------------------------------------
    def _classify_transients(self) -> dict[str, Transient]:
        transients: dict[str, Transient] = {}
        for name, usage in transient_usage(self.procedure).items():
            if not usage.writes:
                continue
            severity, _, _ = assess_transient(usage)
            if severity is Severity.NEEDS_REVIEW:
                mode = TransientMode.MANUAL
            elif len(usage.reads) > 1:
                mode = TransientMode.SHARED
            else:
                mode = TransientMode.INLINE
            transients[name] = Transient(usage.table, mode, _transient_name(usage.table))
        return transients

    def _review_findings(self, first_line: int, last_line: int) -> list[Finding]:
        return [f for f in self.review if first_line <= f.line <= last_line]

    # -- walking ---------------------------------------------------------------------
    def build(self) -> Plan:
        for node in self.procedure.body:
            self._node(node)
        return Plan(
            self.analysis, self.env, _merge_manual(self.steps), self.dropped, self.transients
        )

    def _node(self, node: Node) -> None:
        if is_temp_cleanup(node):
            self._drop(node, "temp table cleanup is not needed (CTEs/views are scoped to the run)")
        elif isinstance(node, Statement):
            self._statement(node)
        elif isinstance(node, Block):
            for child in node.body:
                self._node(child)
        elif isinstance(node, IfNode) and (default := parameter_default(node)) is not None:
            name, value = default
            self.env.assign_default(name, value, node.line)
            self._freeze_if_volatile(name, node.line, node.sql)
        else:
            self._manual_node(node)

    def _drop(self, node: Node, reason: str) -> None:
        sql = node.sql if isinstance(node, (Statement, IfNode)) else ""
        self.dropped.append(DroppedStatement(node.line, sql, reason))

    def _manual_node(self, node: IfNode | WhileNode | TryCatchNode) -> None:
        last_line = max((s.end_line for s in _statements_in(node)), default=node.line)
        reasons = [
            f"{f.rule_id} (line {f.line}): {f.message}"
            for f in self._review_findings(node.line, last_line)
        ]
        if not reasons:
            reasons = ["procedural control flow is not converted automatically"]
        kind = type(node).__name__.replace("Node", "").upper()
        self.steps.append(
            Step(StepKind.MANUAL, f"{kind} block", node.line, node.sql, reasons=reasons)
        )
        for stmt in _statements_in(node):
            for name in _assigned_variables(stmt):
                self.env.mark_unresolved(
                    name, f"assigned inside a {kind} block (line {stmt.line})", stmt.line
                )

    def _manual(self, stmt: Statement, reasons: list[str], title: str | None = None) -> None:
        self.steps.append(
            Step(
                StepKind.MANUAL,
                title or f"{stmt.kind.value} statement",
                stmt.line,
                stmt.sql,
                reasons=reasons,
            )
        )
        for name in _assigned_variables(stmt):
            self.env.mark_unresolved(
                name, f"assigned by a manual step (line {stmt.line})", stmt.line
            )

    # -- statements ------------------------------------------------------------------
    def _statement(self, stmt: Statement) -> None:
        findings = self._review_findings(stmt.line, stmt.end_line)
        if findings or stmt.kind in _ALWAYS_MANUAL or stmt.parse_error:
            reasons = [f"{f.rule_id}: {f.message}" for f in findings]
            if stmt.parse_error and not reasons:
                reasons.append(stmt.parse_error)
            if not reasons:
                reasons.append(f"{stmt.kind.value.replace('_', ' ')} has no automatic conversion")
            self._manual(stmt, reasons)
            return

        transient = self._transient_target(stmt)
        if transient is not None and transient.mode is TransientMode.MANUAL:
            self._manual(stmt, [f"{transient.ref.name} is modified across steps (see ST001/ST002)"])
            return

        kind = stmt.kind
        if kind is K.SET_OPTION:
            self._drop(stmt, "session option with no Databricks equivalent")
        elif (
            kind in (K.CREATE_TABLE, K.DECLARE_TABLE_VARIABLE, K.DROP_TABLE)
            and transient is not None
        ):
            self._drop(stmt, f"{transient.ref.name} is defined by its query instead")
        elif kind is K.DECLARE_VARIABLE:
            self._declare(stmt)
        elif kind is K.SET_VARIABLE:
            self._set(stmt)
        elif kind is K.SELECT_ASSIGN:
            self._select_assign(stmt)
        elif kind is K.RETURN and len(stmt.tokens) == 1:
            self._drop(stmt, "RETURN without a value ends the procedure; nothing to convert")
        elif kind is K.PRINT:
            self.steps.append(
                Step(StepKind.PRINT, "print", stmt.line, stmt.sql, sql=_print_value(stmt))
            )
        elif transient is not None:
            self._populate_transient(stmt, transient)
        elif kind in _DML:
            self._write(stmt)
        elif kind is K.SELECT:
            self._result(stmt)
        else:
            self._manual(stmt, [f"{kind.value.replace('_', ' ')} has no automatic conversion"])

    def _transient_target(self, stmt: Statement) -> Transient | None:
        refs = [*stmt.writes]
        if stmt.ddl_target is not None:
            refs.append(stmt.ddl_target)
        if stmt.kind is K.DECLARE_TABLE_VARIABLE and len(stmt.tokens) > 1:
            refs.append(TableRef(stmt.tokens[1].value.lower(), TableKind.TABLE_VARIABLE))
        for ref in refs:
            if ref.is_transient and ref.name in self.transients:
                return self.transients[ref.name]
        for ref in stmt.reads:
            if (
                ref.name in self.transients
                and self.transients[ref.name].mode is TransientMode.MANUAL
            ):
                return self.transients[ref.name]
        return None

    def _declare(self, stmt: Statement) -> None:
        for item in stmt.expression.expressions:
            target = item.this[0] if isinstance(item.this, list) else item.this
            default = item.args.get("default") or None
            self._bind(target.name, default, stmt)

    def _set(self, stmt: Statement) -> None:
        assignment = stmt.expression.expressions[0].this
        self._bind(assignment.this.name, assignment.expression, stmt)

    def _bind(self, name: str, value: exp.Expression | None, stmt: Statement) -> None:
        if value is not None and any(t.kind is TokenKind.SYSTEM_VARIABLE for t in stmt.tokens):
            self.env.mark_unresolved(
                name, f"assigned from a system variable (line {stmt.line})", stmt.line
            )
            return
        if isinstance(value, exp.Subquery) and isinstance(value.this, exp.Select):
            self._scalar(name, value.this, stmt)
        elif value is None:
            self.env.declare(name, None, stmt.line)
        else:
            self.env.assign(name, value, stmt.line)
            self._freeze_if_volatile(name, stmt.line, stmt.sql)

    def _freeze_if_volatile(self, name: str, line: int, source_sql: str) -> None:
        """A variable set from the current time is evaluated once in T-SQL. Substituting the
        expression into every later statement would re-evaluate it each time, so it is
        loaded once instead, like a variable loaded from a query."""
        binding = self.env.bindings.get(name.lower())
        if not isinstance(binding, exp.Expression) or not any(binding.find_all(*_VOLATILE)):
            return
        query = exp.select(exp.alias_(binding.copy(), "value"))
        self.env.assign_scalar(name, query, line)
        self.steps.append(
            Step(StepKind.SCALAR, f"load @{name}", line, source_sql, sql=query, name=name.lower())
        )

    def _select_assign(self, stmt: Statement) -> None:
        select = stmt.expression
        items = select.expressions
        if len(items) != 1 or not isinstance(items[0], exp.EQ):
            self._manual(stmt, ["assigns several variables in one SELECT"])
            return
        name, value = items[0].this.name, items[0].expression
        if select.args.get("from_") is None:
            self._bind(name, value, stmt)
            return
        query = select.copy()
        query.set("expressions", [exp.alias_(value.copy(), "value")])
        self._scalar(name, query, stmt)

    def _scalar(self, name: str, query: exp.Select, stmt: Statement) -> None:
        prepared = self._prepare(query, stmt)
        if isinstance(prepared, Unresolved):
            self._manual(stmt, [prepared.reason])
            return
        prepared, _ = self._typed_rewrites(prepared)
        self.env.assign_scalar(name, prepared, stmt.line)
        scalar = self.env.scalars[name.lower()]
        self.steps.append(
            Step(
                StepKind.SCALAR,
                f"load @{name}",
                stmt.line,
                stmt.sql,
                sql=scalar.query,
                name=name.lower(),
            )
        )

    def _populate_transient(self, stmt: Statement, transient: Transient) -> None:
        expression = stmt.expression
        if isinstance(expression, exp.Select):  # SELECT ... INTO #t
            query = expression.copy()
            query.set("into", None)
        elif isinstance(expression, exp.Insert) and isinstance(expression.expression, exp.Query):
            query = expression.expression.copy()
            with_ = expression.args.get("with_")
            if with_ is not None:
                query.set("with_", with_.copy())
            if isinstance(expression.this, exp.Schema):
                transient.columns = [c.name.lower() for c in expression.this.expressions]
        else:
            self._manual(
                stmt, [f"{transient.ref.name} is populated by an unsupported statement shape"]
            )
            return
        prepared = self._prepare(query, stmt)
        if isinstance(prepared, Unresolved):
            self._manual(stmt, [prepared.reason])
            return
        transient.queries.append(prepared)
        transient.depends_on = sorted(
            {
                r.name
                for r in stmt.reads
                if r.name in self.transients and r.name != transient.ref.name
            }
        )
        if transient.mode is TransientMode.SHARED:
            view_sql, typed = self._typed_rewrites(transient.query)
            self.steps.append(
                Step(
                    StepKind.TEMP_VIEW,
                    f"intermediate {transient.cte_name}",
                    stmt.line,
                    stmt.sql,
                    sql=view_sql,
                    name=transient.cte_name,
                    rewrites={rw.TRANSIENT_TABLE_TO_CTE} | typed,
                )
            )

    def _write(self, stmt: Statement) -> None:
        if not stmt.writes:
            self._manual(stmt, ["could not determine the target table"])
            return
        prepared = self._prepare(stmt.expression, stmt)
        if isinstance(prepared, Unresolved):
            self._manual(stmt, [prepared.reason])
            return
        rewrites = _expression_rewrites_used(stmt.expression) if self.fixes else set()
        if self.fixes and needs_merge_rewrite(stmt.expression):
            try:
                prepared = update_from_to_merge(prepared)
            except RewriteError as exc:
                self._manual(stmt, [f"UPDATE/DELETE with JOIN could not be rewritten: {exc}"])
                return
            rewrites.add(rw.UPDATE_FROM_TO_MERGE)
        prepared, typed = self._typed_rewrites(prepared)
        rewrites |= typed
        operation = {
            K.INSERT: "insert", K.SELECT_INTO: "insert", K.UPDATE: "update",
            K.DELETE: "delete", K.MERGE: "merge", K.TRUNCATE: "truncate",
        }[stmt.kind]  # fmt: skip
        target = stmt.writes[0]
        if self.fixes and stmt.kind is K.TRUNCATE:
            # Open-source Delta Lake rejects TRUNCATE TABLE; DELETE without a predicate
            # empties the table the same way on every Delta runtime.
            prepared = exp.Delete(this=stmt.expression.expressions[0].copy())
        rewrites |= self._transient_rewrites(stmt)
        self.steps.append(
            Step(
                StepKind.WRITE,
                f"{operation} {target.qualified_name}",
                stmt.line,
                stmt.sql,
                sql=prepared,
                target=target,
                operation=operation,
                rewrites=rewrites,
            )
        )

    def _result(self, stmt: Statement) -> None:
        prepared = self._prepare(stmt.expression, stmt)
        if isinstance(prepared, Unresolved):
            self._manual(stmt, [prepared.reason])
            return
        rewrites = self._transient_rewrites(stmt)
        if self.fixes:
            rewrites |= _expression_rewrites_used(stmt.expression)
        prepared, typed = self._typed_rewrites(prepared)
        rewrites |= typed
        self.steps.append(
            Step(
                StepKind.RESULT, "result set", stmt.line, stmt.sql, sql=prepared, rewrites=rewrites
            )
        )

    def _transient_rewrites(self, stmt: Statement) -> set[str]:
        return (
            {rw.TRANSIENT_TABLE_TO_CTE}
            if any(r.name in self.transients for r in stmt.reads)
            else set()
        )

    # -- SQL preparation -------------------------------------------------------------
    def _typed_rewrites(self, expression: exp.Expression) -> tuple[exp.Expression, set[str]]:
        """Apply the DDL-dependent rewrites (no-ops without a catalog)."""
        used: set[str] = set()
        if self.resolver is None or not self.fixes:
            return expression, used
        for name, rewrite in (
            (rw.BIT_TO_BOOLEAN, bit_to_boolean),
            (rw.COMPACT_DATE_LITERAL, compact_date_literal),
        ):
            rewritten = rewrite(expression, self.resolver)
            if changed(expression, rewritten):
                used.add(name)
                expression = rewritten
        return expression, used

    def _prepare(self, expression: exp.Expression, stmt: Statement) -> exp.Expression | Unresolved:
        """Substitute variables, fold transients into CTEs and apply expression rewrites."""
        resolved = self.env.resolve(expression, stmt.line)
        if isinstance(resolved, Unresolved):
            return Unresolved(
                f"uses a variable that cannot be resolved: {resolved.reason}", stmt.line
            )
        resolved = self._fold_transients(resolved)
        return apply_expression_rewrites(resolved) if self.fixes else resolved

    def _fold_transients(self, expression: exp.Expression) -> exp.Expression:
        used: list[Transient] = []

        def rename(node: exp.Expression) -> exp.Expression:
            if isinstance(node, exp.Table):
                ref = table_ref(node)
                transient = self.transients.get(ref.name) if ref.is_transient else None
                if transient is not None:
                    if transient not in used:
                        used.append(transient)
                    alias = node.alias or ref.name.lstrip("#@")
                    return exp.Table(
                        this=exp.to_identifier(transient.cte_name),
                        alias=exp.TableAlias(this=exp.to_identifier(alias)),
                    )
            return node

        result = expression.transform(rename)
        inline = self._inline_closure([t for t in used if t.mode is TransientMode.INLINE])
        if not inline:
            return result
        ctes = [_cte(t) for t in inline]
        existing = result.args.get("with_")
        if existing is not None:
            ctes.extend(e.copy() for e in existing.expressions)
        result.set("with_", exp.With(expressions=ctes))
        return result

    def _inline_closure(self, transients: list[Transient]) -> list[Transient]:
        """Inline transients plus the inline transients they depend on, dependencies first."""
        ordered: list[Transient] = []

        def visit(t: Transient) -> None:
            for dep in t.depends_on:
                dep_t = self.transients.get(dep)
                if dep_t is not None and dep_t.mode is TransientMode.INLINE:
                    visit(dep_t)
            if t not in ordered:
                ordered.append(t)

        for t in transients:
            visit(t)
        return ordered


def _cte(transient: Transient) -> exp.CTE:
    query = transient.query
    # A defining query with its own WITH would nest CTEs; hoist them alongside.
    alias = exp.TableAlias(
        this=exp.to_identifier(transient.cte_name),
        columns=[exp.to_identifier(c) for c in transient.columns] or None,
    )
    return exp.CTE(this=query, alias=alias)


def _expression_rewrites_used(expression: exp.Expression) -> set[str]:
    from sproc_to_lakehouse.sqlutil import is_string_concat

    used: set[str] = set()
    if any(is_string_concat(n) for n in expression.find_all(exp.Add)):
        used.add(rw.PLUS_TO_CONCAT)
    if any(
        c.args.get("safe") and c.args.get("style") is not None
        for c in expression.find_all(exp.Convert)
    ):
        used.add(rw.TRY_CONVERT_SAFE_PARSE)
    if any(e.text("this").upper() == "WEEKISO" for e in expression.find_all(exp.Extract)):
        used.add(rw.ISO_WEEK_EXTRACT)
    return used


def _print_value(stmt: Statement) -> exp.Expression | None:
    tokens = stmt.tokens[1:]
    if len(tokens) == 1 and tokens[0].kind is TokenKind.STRING:
        return exp.Literal.string(tokens[0].value.lstrip("Nn").strip("'").replace("''", "'"))
    return None


def _statements_in(node: Node) -> list[Statement]:
    from sproc_to_lakehouse.models import iter_nodes

    return [n for n in iter_nodes([node]) if isinstance(n, Statement)]


def _assigned_variables(stmt: Statement) -> list[str]:
    """Variables a statement assigns (SET @x, SELECT @x = ..., FETCH ... INTO @x, @y)."""
    tokens = stmt.tokens
    if stmt.kind is K.SET_VARIABLE:
        return [tokens[1].value.lstrip("@").lower()]
    if stmt.kind is K.SELECT_ASSIGN:
        return [
            t.value.lstrip("@").lower()
            for i, t in enumerate(tokens[:-1])
            if t.kind is TokenKind.VARIABLE and tokens[i + 1].is_punct("=")
        ]
    if stmt.kind is K.FETCH_CURSOR:
        into = next((i for i, t in enumerate(tokens) if t.is_word("INTO")), len(tokens))
        return [t.value.lstrip("@").lower() for t in tokens[into:] if t.kind is TokenKind.VARIABLE]
    return []


def _merge_manual(steps: list[Step]) -> list[Step]:
    """Fold consecutive MANUAL steps into one, so a cursor loop reads as one block."""
    merged: list[Step] = []
    for step in steps:
        if step.kind is StepKind.MANUAL and merged and merged[-1].kind is StepKind.MANUAL:
            prev = merged[-1]
            prev.source_sql = f"{prev.source_sql}\n{step.source_sql}"
            prev.reasons.extend(r for r in step.reasons if r not in prev.reasons)
            prev.title = "manual block"
        else:
            merged.append(step)
    return merged


def build_plan(
    analysis: ProcedureAnalysis, catalog: Catalog | None = None, *, fixes: bool = True
) -> Plan:
    """Build the conversion plan. ``fixes=False`` turns off the targeted rewrites (plain
    sqlglot output, same pipeline); it exists to measure what the fixes are worth."""
    return PlanBuilder(analysis, catalog, fixes=fixes).build()
