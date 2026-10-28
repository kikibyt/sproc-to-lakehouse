"""AST rewrites that fix what plain sqlglot transpilation gets wrong for Databricks.

Each function implements one rewrite named in :mod:`sproc_to_lakehouse.analyzer.rewrites`
(``transient_table_to_cte`` is structural and lives in :mod:`.plan`). Functions take a
T-SQL-read sqlglot expression and return a new expression; inputs are never mutated.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.dialects.databricks import Databricks
from sqlglot.tokens import TokenType

from sproc_to_lakehouse.analyzer import rewrites as rw
from sproc_to_lakehouse.sqlutil import add_chain_leaves, and_all, conjuncts, is_string_concat

_DATETIME_TYPES = (
    exp.DataType.Type.DATE,
    exp.DataType.Type.DATETIME,
    exp.DataType.Type.DATETIME2,
    exp.DataType.Type.SMALLDATETIME,
    exp.DataType.Type.TIMESTAMP,
)


class RewriteError(ValueError):
    """The statement has a shape a rewrite cannot handle safely."""


def plus_to_concat(expression: exp.Expression) -> exp.Expression:
    """``'a' + b + 'c'`` -> ``CONCAT('a', b, 'c')`` when any operand is known text."""

    def fix(node: exp.Expression) -> exp.Expression:
        if is_string_concat(node):
            return exp.Concat(expressions=[leaf.copy() for leaf in add_chain_leaves(node)])
        return node

    return expression.copy().transform(fix)


def try_convert_safe_parse(expression: exp.Expression) -> exp.Expression:
    """``TRY_CONVERT(DATE, x, 101)`` -> ``CAST(TRY_TO_TIMESTAMP(x, 'M/d/yyyy') AS DATE)``.

    sqlglot emits ``TO_DATE(x, fmt)`` for TRY_CONVERT, which raises on bad input under
    ANSI mode. ``TRY_TO_TIMESTAMP`` returns NULL instead, matching TRY_CONVERT.
    """

    def fix(node: exp.Expression) -> exp.Expression:
        if not (
            isinstance(node, exp.Convert)
            and node.args.get("safe")
            and node.args.get("style") is not None
            and node.this.is_type(*_DATETIME_TYPES)
        ):
            return node
        unsafe = node.copy()
        unsafe.set("safe", None)
        # sqlglot maps the style code to a Databricks pattern when rendering, e.g.
        # TO_DATE(x, 'M/d/yyyy'); the pattern is the last string literal of that SQL.
        rendered = unsafe.sql(dialect="databricks")
        strings = [
            t.text for t in Databricks().tokenize(rendered) if t.token_type == TokenType.STRING
        ]
        if not strings:
            raise RewriteError(f"cannot derive a format string from {unsafe.sql('tsql')}")
        fmt = exp.Literal.string(strings[-1])
        parse = exp.Anonymous(this="TRY_TO_TIMESTAMP", expressions=[node.expression.copy(), fmt])
        return exp.Cast(this=parse, to=node.this.copy())

    return expression.copy().transform(fix)


def iso_week_extract(expression: exp.Expression) -> exp.Expression:
    """``DATEPART(ISO_WEEK, d)``: sqlglot emits the invalid field WEEKISO.

    Databricks' ``WEEK`` field already follows ISO 8601.
    """

    def fix(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Extract) and node.text("this").upper() == "WEEKISO":
            fixed = node.copy()
            fixed.set("this", exp.var("WEEK"))
            return fixed
        return node

    return expression.copy().transform(fix)


# --- UPDATE/DELETE ... FROM ... JOIN  ->  MERGE INTO ---------------------------------


def _alias(table: exp.Expression) -> str:
    return (table.alias or table.name).lower()


def _qualifiers(node: exp.Expression) -> set[str]:
    return {c.table.lower() for c in node.find_all(exp.Column) if c.table}


def update_from_to_merge(expression: exp.Expression) -> exp.Expression:
    """Rewrite ``UPDATE t ... FROM t JOIN s ON ...`` (or DELETE) as ``MERGE INTO``.

    Supported shape: the target is the first relation in FROM and is INNER JOINed to
    exactly one source relation. Source-only WHERE predicates move into the USING
    subquery; the rest become the ``WHEN MATCHED AND`` condition. When neither the SET
    values nor that condition use source columns, the source is reduced to the DISTINCT
    join keys, so several matching source rows cannot make Delta's MERGE fail.
    """
    if isinstance(expression, exp.Update):
        from_ = expression.args.get("from_")
        sets = expression.expressions
    elif isinstance(expression, exp.Delete):
        from_ = None
        sets = []
    else:
        raise RewriteError(f"not an UPDATE/DELETE: {type(expression).__name__}")

    target_ref = expression.this
    base = from_.this if from_ is not None else target_ref
    joins = list(expression.args.get("joins") or []) + list(base.args.get("joins") or [])
    if len(joins) != 1:
        raise RewriteError(f"expected exactly one JOIN, found {len(joins)}")
    join = joins[0]
    if join.side or (join.kind and join.kind.upper() not in ("INNER", "")):
        raise RewriteError("only INNER JOIN can be rewritten without changing semantics")
    if _alias(base) != _alias(target_ref) and target_ref.name.lower() != base.name.lower():
        raise RewriteError("the UPDATE target must be the first relation in FROM")

    target_alias = _alias(base)
    target = base.copy()
    target.set("joins", None)
    source = join.this.copy()
    source_alias = _alias(source)

    where = expression.args.get("where")
    source_only, matched = [], []
    for part in conjuncts(where.this if where is not None else None):
        quals = _qualifiers(part)
        (source_only if quals and quals <= {source_alias} else matched).append(part.copy())
    on_parts = conjuncts(join.args.get("on"))

    uses_source = any(source_alias in _qualifiers(s) for s in sets) or any(
        source_alias in _qualifiers(m) for m in matched
    )
    if source_only or not uses_source:
        source = _source_subquery(source, source_alias, source_only, on_parts, uses_source)

    # Spark accepts a WITH clause in front of MERGE, so the UPDATE's CTEs stay put.
    with_ = expression.args.get("with_")
    prefix = f"{with_.sql(dialect='databricks')} " if with_ is not None else ""
    sql = prefix + (
        f"MERGE INTO {target.sql(dialect='databricks')} "
        f"USING {source.sql(dialect='databricks')} "
        f"ON {and_all([p.copy() for p in on_parts]).sql(dialect='databricks')} "
        "WHEN MATCHED"
    )
    condition = and_all(matched)
    if condition is not None:
        sql += f" AND {condition.sql(dialect='databricks')}"
    if sets:
        assignments = ", ".join(_set_clause(s, target_alias) for s in sets)
        sql += f" THEN UPDATE SET {assignments}"
    else:
        sql += " THEN DELETE"
    return sqlglot.parse_one(sql, read="databricks")


def _source_subquery(
    source: exp.Expression,
    alias: str,
    filters: list[exp.Expression],
    on_parts: list[exp.Expression],
    uses_source: bool,
) -> exp.Expression:
    if uses_source:
        columns = "*"
    else:
        keys = sorted(
            {
                c.name
                for part in on_parts
                for c in part.find_all(exp.Column)
                if c.table.lower() == alias
            }
        )
        columns = "DISTINCT " + ", ".join(f"{alias}.{k}" for k in keys)
    inner = source.copy()
    sql = f"SELECT {columns} FROM {inner.sql(dialect='databricks')}"
    where = and_all(filters)
    if where is not None:
        sql += f" WHERE {where.sql(dialect='databricks')}"
    return (
        sqlglot.parse_one(f"SELECT * FROM ({sql}) AS {alias}", read="databricks").args["from_"].this
    )


def _set_clause(assignment: exp.Expression, target_alias: str) -> str:
    if not isinstance(assignment, exp.EQ) or not isinstance(assignment.this, exp.Column):
        raise RewriteError(f"unsupported SET item: {assignment.sql('tsql')}")
    column = assignment.this.copy()
    column.set("table", exp.to_identifier(target_alias))
    return f"{column.sql(dialect='databricks')} = {assignment.expression.sql(dialect='databricks')}"


# Rewrites that apply to any statement, in application order.
EXPRESSION_REWRITES = {
    rw.PLUS_TO_CONCAT: plus_to_concat,
    rw.TRY_CONVERT_SAFE_PARSE: try_convert_safe_parse,
    rw.ISO_WEEK_EXTRACT: iso_week_extract,
}
STATEMENT_REWRITES = {rw.UPDATE_FROM_TO_MERGE: update_from_to_merge}


def apply_expression_rewrites(expression: exp.Expression) -> exp.Expression:
    for rewrite in EXPRESSION_REWRITES.values():
        expression = rewrite(expression)
    return expression


def needs_merge_rewrite(expression: exp.Expression) -> bool:
    if isinstance(expression, exp.Update):
        return expression.args.get("from_") is not None
    if isinstance(expression, exp.Delete):
        return bool(expression.args.get("tables")) or bool(
            expression.this is not None and expression.this.args.get("joins")
        )
    return False
