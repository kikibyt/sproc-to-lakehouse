"""Procedure state: temp tables, table variables, scalar variables, session settings."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from sqlglot import exp

from sproc_to_lakehouse.analyzer import rewrites as rw
from sproc_to_lakehouse.analyzer.registry import (
    Rule,
    function_calls,
    is_temp_cleanup,
    iter_units,
    rule,
    snippet,
)
from sproc_to_lakehouse.models import (
    Finding,
    IfNode,
    Procedure,
    Severity,
    Statement,
    StatementKind,
    TableKind,
    TableRef,
    WhileNode,
    iter_nodes,
)
from sproc_to_lakehouse.parser.lexer import TokenKind

K = StatementKind
_MUTATIONS = {K.UPDATE, K.DELETE, K.MERGE, K.TRUNCATE}


@dataclass
class _Usage:
    """How one temp table / table variable is used, in source order."""

    table: TableRef
    writes: list[tuple[int, Statement]] = field(default_factory=list)
    reads: list[tuple[int, int]] = field(default_factory=list)  # (order, line)
    written_in_loop: bool = False


def _transient_usage(procedure: Procedure) -> dict[str, _Usage]:
    in_loop: set[int] = set()
    for node in procedure.iter_nodes():
        if isinstance(node, WhileNode):
            in_loop.update(id(n) for n in iter_nodes([node.body]))

    usages: dict[str, _Usage] = {}

    def usage(ref: TableRef) -> _Usage:
        return usages.setdefault(ref.name, _Usage(ref))

    for order, node in enumerate(procedure.iter_nodes()):
        if isinstance(node, Statement):
            for ref in node.writes:
                if ref.is_transient:
                    usage(ref).writes.append((order, node))
                    usage(ref).written_in_loop |= id(node) in in_loop
            for ref in node.reads:
                if ref.is_transient:
                    usage(ref).reads.append((order, node.line))
        elif isinstance(node, (IfNode, WhileNode)):
            for ref in node.condition.reads:
                if ref.is_transient:
                    usage(ref).reads.append((order, node.condition.line))
    return usages


def _assess(u: _Usage) -> tuple[Severity, str, str | None]:
    name = u.table.name
    mutations = [s for _, s in u.writes if s.kind in _MUTATIONS]
    if mutations:
        lines = ", ".join(str(s.line) for s in mutations)
        return (
            Severity.NEEDS_REVIEW,
            f"{name} is modified after it is populated (UPDATE/DELETE at line {lines}). "
            "Step-by-step mutation of intermediate state has to be re-expressed as a chain "
            "of CTEs/models, each deriving the next version of the data.",
            None,
        )
    if u.written_in_loop:
        return (
            Severity.NEEDS_REVIEW,
            f"{name} is written inside a loop; its final contents depend on iteration order.",
            None,
        )
    last_write = max(order for order, _ in u.writes)
    early_reads = [line for order, line in u.reads if order < last_write]
    if early_reads:
        return (
            Severity.NEEDS_REVIEW,
            f"{name} is read (line {early_reads[0]}) before its last INSERT, so readers "
            "see different contents at different points.",
            None,
        )
    if len(u.writes) > 1:
        return (
            Severity.AUTO_WITH_NOTE,
            f"{name} is populated by {len(u.writes)} INSERT statements; they are combined "
            "with UNION ALL into one CTE/model. Check for duplicate handling.",
            rw.TRANSIENT_TABLE_TO_CTE,
        )
    if len(u.reads) > 1:
        return (
            Severity.AUTO_WITH_NOTE,
            f"{name} is populated once and read by {len(u.reads)} statements; it becomes a "
            "shared intermediate model (dbt) / temporary view (notebook).",
            rw.TRANSIENT_TABLE_TO_CTE,
        )
    return (
        Severity.AUTO,
        f"{name} is populated once and read once; it is inlined as a CTE.",
        rw.TRANSIENT_TABLE_TO_CTE,
    )


def _transient_rule(r: Rule, procedure: Procedure, kinds: set[TableKind]) -> Iterator[Finding]:
    for u in _transient_usage(procedure).values():
        if u.table.kind not in kinds or not u.writes:
            continue
        severity, message, rewrite = _assess(u)
        first = min(u.writes, key=lambda w: w[0])[1]
        yield r.finding(severity, message, first.line, snippet(first.sql), rewrite)


@rule("ST001", "temp_table", "Temp table (#name) used as intermediate storage")
def temp_table(r: Rule, procedure: Procedure) -> Iterator[Finding]:
    yield from _transient_rule(r, procedure, {TableKind.TEMP, TableKind.GLOBAL_TEMP})


@rule("ST002", "table_variable", "Table variable (@name TABLE) used as intermediate storage")
def table_variable(r: Rule, procedure: Procedure) -> Iterator[Finding]:
    yield from _transient_rule(r, procedure, {TableKind.TABLE_VARIABLE})


@rule("ST003", "scalar_from_query", "Scalar variable assigned from a query")
def scalar_from_query(r: Rule, procedure: Procedure) -> Iterator[Finding]:
    for stmt in procedure.statements():
        from_select = stmt.kind is K.SELECT_ASSIGN and stmt.reads
        from_subquery = (
            stmt.kind in (K.SET_VARIABLE, K.DECLARE_VARIABLE)
            and stmt.expression is not None
            and stmt.expression.find(exp.Select) is not None
        )
        if from_select or from_subquery:
            yield r.finding(
                Severity.AUTO_WITH_NOTE,
                "Variable is loaded from a query. Becomes a collected scalar in the "
                "notebook, and an inline subquery or run_query() in dbt.",
                stmt.line,
                snippet(stmt.sql),
            )


@rule("ST004", "parameters", "Procedure parameters")
def parameters(r: Rule, procedure: Procedure) -> Iterator[Finding]:
    inputs = [p for p in procedure.parameters if not p.is_output]
    if inputs:
        names = ", ".join(p.name for p in inputs)
        yield r.finding(
            Severity.AUTO,
            f"{len(inputs)} input parameter(s) become dbt vars and notebook widgets.",
            procedure.line,
            names,
        )


_SYSTEM_VARIABLE_NOTES = {
    "@@ROWCOUNT": "Rows affected by the previous statement. On Delta this comes from the "
    "operation metrics (DataFrame counts or DESCRIBE HISTORY), not a session variable.",
    "@@FETCH_STATUS": "Cursor loop state; it disappears once the cursor is rewritten set-based.",
    "@@TRANCOUNT": "Open-transaction counter; there is no equivalent session state.",
    "@@ERROR": "Error code of the previous statement; use exceptions instead.",
}
_IDENTITY_SYSTEM_VARIABLES = {"@@IDENTITY"}  # reported by ST007


@rule("ST005", "system_variable", "@@ system variables")
def system_variable(r: Rule, procedure: Procedure) -> Iterator[Finding]:
    for unit in iter_units(procedure):
        seen: set[str] = set()
        for tok in unit.tokens:
            if tok.kind is not TokenKind.SYSTEM_VARIABLE or tok.value in seen:
                continue
            if tok.value in _IDENTITY_SYSTEM_VARIABLES:
                continue
            seen.add(tok.value)
            note = _SYSTEM_VARIABLE_NOTES.get(
                tok.value, "SQL Server session state with no direct equivalent."
            )
            yield r.finding(
                Severity.NEEDS_REVIEW, f"{tok.value}: {note}", tok.line, snippet(unit.sql)
            )


@rule("ST006", "session_option", "SET session options")
def session_option(r: Rule, procedure: Procedure) -> Iterator[Finding]:
    for stmt in procedure.statements():
        if stmt.kind is not K.SET_OPTION:
            continue
        option = stmt.tokens[1].value if len(stmt.tokens) > 1 else ""
        if option == "XACT_ABORT":
            yield r.finding(
                Severity.AUTO_WITH_NOTE,
                "XACT_ABORT changes how errors end transactions; dropped, so the "
                "transaction/error-handling review must account for it.",
                stmt.line,
                snippet(stmt.sql),
            )
        else:
            yield r.finding(
                Severity.AUTO,
                f"SET {option} is a SQL Server session setting with no Databricks "
                "equivalent; dropped.",
                stmt.line,
                snippet(stmt.sql),
            )


@rule("ST007", "identity_capture", "SCOPE_IDENTITY / @@IDENTITY")
def identity_capture(r: Rule, procedure: Procedure) -> Iterator[Finding]:
    for unit in iter_units(procedure):
        calls = function_calls(unit.tokens, "SCOPE_IDENTITY", "IDENT_CURRENT")
        sysvars = [
            t
            for t in unit.tokens
            if t.kind is TokenKind.SYSTEM_VARIABLE and t.value == "@@IDENTITY"
        ]
        if calls or sysvars:
            yield r.finding(
                Severity.NEEDS_REVIEW,
                "Reads the identity value generated by an earlier INSERT. Delta identity "
                "columns do not return the generated value; generate the key up front "
                "(e.g. a run id) or read it back with a query.",
                unit.line,
                snippet(unit.sql),
            )


@rule("ST008", "temp_cleanup", "Temp table cleanup (DROP TABLE #t)")
def temp_cleanup(r: Rule, procedure: Procedure) -> Iterator[Finding]:
    nested: set[int] = set()
    for node in procedure.iter_nodes():
        if id(node) in nested or not is_temp_cleanup(node):
            continue
        if isinstance(node, IfNode):
            nested.add(id(node.then_branch))
            line, text = node.line, "IF " + node.condition.sql + " DROP TABLE ..."
        else:
            line, text = node.line, node.sql
        yield r.finding(
            Severity.AUTO,
            "Temp table cleanup has no equivalent (CTEs and views are scoped to the run); removed.",
            line,
            snippet(text),
        )


RULES: list[Rule] = [
    temp_table,
    table_variable,
    scalar_from_query,
    parameters,
    system_variable,
    session_option,
    identity_capture,
    temp_cleanup,
]
