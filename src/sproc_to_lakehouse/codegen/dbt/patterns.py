"""Match plan steps against the write patterns that map cleanly onto dbt models.

dbt builds each model as one table or view, in dependency order, so it only fits
procedures whose writes follow a recognisable shape:

========================================  =========================================
T-SQL steps on one target table           dbt model
========================================  =========================================
INSERT ... SELECT                         incremental (append)
TRUNCATE; INSERT ... SELECT               table
DELETE ... WHERE <slice>; INSERT          incremental, ``replace_where`` the slice
MERGE (update-if-changed + insert)        incremental ``merge`` with ``unique_key``
... followed by UPDATE/MERGE/DELETE       the above plus ``post_hook`` statements
SELECT returned to the caller             view
========================================  =========================================

A procedure with any step outside these patterns (or any manual step) is not split
into models at all: it becomes one ordered dbt macro, so statement order is never
silently changed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlglot import exp

from sproc_to_lakehouse.codegen.plan import Plan, Step, StepKind
from sproc_to_lakehouse.models import TableRef


@dataclass
class MergeShape:
    """A MERGE that dbt's ``merge`` strategy can express."""

    source: exp.Expression  # the USING query (as a SELECT)
    source_alias: str
    target_alias: str
    unique_key: list[str]
    on: exp.Expression
    columns: list[tuple[str, exp.Expression]]  # (target column, value from INSERT)
    exclude_from_update: list[str]  # insert-only columns
    matched_condition: exp.Expression | None
    has_matched_update: bool


@dataclass
class ModelSpec:
    name: str
    kind: str  # "insert" | "merge" | "view" | "ephemeral"
    procedure: str
    target: TableRef | None
    primary: Step
    materialized: str  # table | incremental | view | ephemeral
    strategy: str | None = None  # append | replace_where | merge
    replace_where: exp.Expression | None = None
    merge: MergeShape | None = None
    post_hooks: list[Step] = field(default_factory=list)


@dataclass
class ProcedureMapping:
    plan: Plan
    models: list[ModelSpec]
    as_macro: bool
    reason: str | None = None  # why the procedure is a macro rather than models


def map_procedure(plan: Plan) -> ProcedureMapping:
    problem = _blocking_problem(plan)
    if problem is not None:
        return ProcedureMapping(plan, [], as_macro=True, reason=problem)

    models: list[ModelSpec] = []
    groups: dict[str, list[Step]] = {}
    for step in plan.steps:
        if step.kind is StepKind.WRITE:
            groups.setdefault(step.target.qualified_name, []).append(step)
    for steps in groups.values():
        spec = _model_for_target(plan, steps)
        if isinstance(spec, str):
            return ProcedureMapping(plan, [], as_macro=True, reason=spec)
        models.append(spec)

    results = [s for s in plan.steps if s.kind is StepKind.RESULT]
    for i, step in enumerate(results):
        base = plan.name.removeprefix("usp_")
        name = base if len(results) == 1 else f"{base}_{i + 1}"
        models.append(ModelSpec(name, "view", plan.name, None, step, materialized="view"))
    for step in plan.steps:
        if step.kind is StepKind.TEMP_VIEW:
            models.append(
                ModelSpec(
                    ephemeral_name(plan, step.name), "ephemeral", plan.name, None, step, "ephemeral"
                )
            )
    return ProcedureMapping(plan, models, as_macro=False)


def ephemeral_name(plan: Plan, view_name: str) -> str:
    return f"int_{plan.name.removeprefix('usp_')}__{view_name}"


def _blocking_problem(plan: Plan) -> str | None:
    if plan.manual_steps:
        return f"{len(plan.manual_steps)} step(s) need manual conversion"
    for step in plan.steps:
        if step.kind is StepKind.SCALAR and step.sql.args.get("from_") is not None:
            return f"@{step.name} is loaded from a query at run time (line {step.line})"
    return None


def _model_for_target(plan: Plan, steps: list[Step]) -> ModelSpec | str:
    target = steps[0].target
    primary_index = next(
        (i for i, s in enumerate(steps) if s.operation in ("insert", "merge")), None
    )
    if primary_index is None:
        return f"{target.qualified_name} is only modified in place (no INSERT/MERGE builds it)"
    pre, primary, post = steps[:primary_index], steps[primary_index], steps[primary_index + 1 :]

    spec = ModelSpec(target.name, "insert", plan.name, target, primary, "incremental", "append")
    if primary.operation == "merge":
        shape = merge_shape(primary.sql)
        if isinstance(shape, str):
            return f"MERGE into {target.qualified_name} (line {primary.line}) {shape}"
        spec.kind, spec.strategy, spec.merge = "merge", "merge", shape
        if pre:
            return f"{target.qualified_name} is modified before its MERGE (line {pre[0].line})"
    elif not isinstance(primary.sql, exp.Insert) or not isinstance(
        primary.sql.expression, exp.Query
    ):
        return f"INSERT into {target.qualified_name} (line {primary.line}) is not INSERT ... SELECT"
    elif [s.operation for s in pre] == ["truncate"]:
        spec.materialized, spec.strategy = "table", None
    elif [s.operation for s in pre] == ["delete"]:
        predicate = _slice_predicate(pre[0].sql)
        if predicate is None:
            return f"DELETE before the INSERT (line {pre[0].line}) is not a simple slice predicate"
        spec.strategy, spec.replace_where = "replace_where", predicate
    elif pre:
        return (
            f"{target.qualified_name} has unsupported steps before its INSERT (line {pre[0].line})"
        )
    spec.post_hooks = post
    return spec


def _slice_predicate(delete: exp.Expression) -> exp.Expression | None:
    """``DELETE FROM t WHERE <p>`` -> ``<p>`` without table qualifiers, if it is simple."""
    if not isinstance(delete, exp.Delete) or delete.args.get("tables"):
        return None
    where = delete.args.get("where")
    if where is None or any(where.find_all(exp.Subquery, exp.Select, exp.Exists)):
        return None
    predicate = where.this.copy()
    for column in predicate.find_all(exp.Column):
        column.set("table", None)
    return predicate


def _column_name(node: exp.Expression, alias: str | None = None) -> str | None:
    if isinstance(node, exp.Column) and (alias is None or node.table.lower() == alias):
        return node.name.lower()
    return None


def merge_shape(merge: exp.Expression) -> MergeShape | str:
    """Describe a MERGE in dbt terms, or explain (as a string) why it does not fit."""
    if not isinstance(merge, exp.Merge):
        return "is not a plain MERGE statement"
    target_alias = (merge.this.alias or merge.this.name).lower()
    using = merge.args["using"]
    source_alias = (using.alias or using.name).lower()
    if isinstance(using, exp.Subquery):
        source = using.this.copy()
    elif isinstance(using, exp.Table):
        source = exp.select("*").from_(using.copy())
    else:
        return "has an unsupported USING clause"

    unique_key: list[str] = []
    on = merge.args["on"]
    for part in _conjuncts(on):
        if not isinstance(part, exp.EQ):
            return "joins on something other than column equality"
        left, right = part.this, part.expression
        key = _column_name(left, target_alias) or _column_name(right, target_alias)
        other = _column_name(right, source_alias) or _column_name(left, source_alias)
        if key is None or other is None:
            return "joins on something other than target = source columns"
        unique_key.append(key)

    whens = merge.args["whens"].expressions
    matched = [w for w in whens if w.args.get("matched")]
    inserts = [w for w in whens if not w.args.get("matched") and not w.args.get("source")]
    if any(not w.args.get("matched") and w.args.get("source") for w in whens):
        return "uses WHEN NOT MATCHED BY SOURCE, which dbt's merge strategy does not cover"
    if len(matched) > 1 or len(inserts) != 1:
        return "needs exactly one WHEN NOT MATCHED INSERT and at most one WHEN MATCHED clause"
    if matched and not isinstance(matched[0].args["then"], exp.Update):
        return "deletes matched rows"

    insert = inserts[0].args["then"]
    if inserts[0].args.get("condition") is not None:
        return "has a conditional WHEN NOT MATCHED clause"
    names = [c.name.lower() for c in insert.this.expressions]
    columns = list(zip(names, [v.copy() for v in insert.expression.expressions], strict=True))
    if any(target_alias in {c.table.lower() for c in v.find_all(exp.Column)} for _, v in columns):
        return "inserts values computed from target columns"

    exclude: list[str] = []
    condition = None
    if matched:
        updates = {}
        for eq in matched[0].args["then"].expressions:
            col = _column_name(eq.this)
            if col is None:
                return "has an unsupported SET target"
            updates[col] = eq.expression
        for col, value in updates.items():
            if col not in names:
                return f"updates {col}, which the INSERT does not set"
            if value.sql("databricks") != dict(columns)[col].sql("databricks"):
                return f"sets {col} differently on update and insert"
        exclude = [c for c in names if c not in updates and c not in unique_key]
        condition = matched[0].args.get("condition")

    return MergeShape(
        source=source,
        source_alias=source_alias,
        target_alias=target_alias,
        unique_key=unique_key,
        on=on.copy(),
        columns=columns,
        exclude_from_update=exclude,
        matched_condition=condition.copy() if condition is not None else None,
        has_matched_update=bool(matched),
    )


def _conjuncts(node: exp.Expression) -> list[exp.Expression]:
    from sproc_to_lakehouse.sqlutil import conjuncts

    return conjuncts(node)
