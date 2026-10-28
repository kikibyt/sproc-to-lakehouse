"""Rewrites that need column types from the DDL catalog.

Shared by the analyzer (to detect the construct: "would this rewrite change anything?")
and the generator (to apply it), so the two can never disagree.

* ``bit_to_boolean``: T-SQL BIT columns arrive in Databricks as BOOLEAN (the default of
  Spark's SQL Server reader and of sqlglot). ``flag = 1`` then fails under ANSI mode, and
  writing ``CASE ... THEN 1 ELSE 0 END`` into the column cannot be cast implicitly.
* ``compact_date_literal``: SQL Server reads ``'19000101'`` as a date when compared with
  a date; Spark cannot cast that string to DATE and fails at run time.
"""

from __future__ import annotations

import re
from datetime import date

from sqlglot import exp

from sproc_to_lakehouse.sqltypes import TypeResolver, is_bit, is_date_like

_COMPACT_DATE = re.compile(r"(\d{4})(\d{2})(\d{2})")
_COMPARISONS = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)


def _zero_one(node: exp.Expression) -> bool | None:
    if isinstance(node, exp.Literal) and not node.is_string and node.this in ("0", "1"):
        return node.this == "1"
    return None


def _as_boolean(value: exp.Expression, resolver: TypeResolver) -> exp.Expression | None:
    """The BOOLEAN form of a value written into a BIT column, or None if already boolean."""
    inner = value.unalias() if isinstance(value, exp.Alias) else value
    flag = _zero_one(inner)
    if flag is not None:
        return exp.Boolean(this=flag)
    if is_bit(resolver.type_of(inner)) or isinstance(inner, (exp.Boolean, exp.Null)):
        return None
    return exp.Cast(this=inner.copy(), to=exp.DataType.build("BOOLEAN"))


def _replace_value(value: exp.Expression, replacement: exp.Expression) -> None:
    if isinstance(value, exp.Alias):
        value.set("this", replacement)
    else:
        value.replace(replacement)


def bit_to_boolean(expression: exp.Expression, resolver: TypeResolver) -> exp.Expression:
    result = expression.copy()
    changes: list[tuple[exp.Expression, exp.Expression]] = []

    for cmp in result.find_all(exp.EQ, exp.NEQ):
        for literal, other in ((cmp.expression, cmp.this), (cmp.this, cmp.expression)):
            flag = _zero_one(literal)
            if flag is not None and is_bit(resolver.type_of(other)):
                changes.append((literal, exp.Boolean(this=flag)))

    for target_table, column, value in _written_values(result):
        if is_bit(resolver.column_type_in_table(target_table, column)):
            replacement = _as_boolean(value, resolver)
            if replacement is not None:
                changes.append((value, replacement))

    for node, replacement in changes:
        _replace_value(node, replacement)
    return result


def _written_values(expression: exp.Expression):
    """(target table, column name, value expression) for every value a DML writes."""
    if isinstance(expression, exp.Insert) and isinstance(expression.this, exp.Schema):
        table = expression.this.this
        columns = [c.name.lower() for c in expression.this.expressions]
        source = expression.expression
        rows: list[list[exp.Expression]] = []
        if isinstance(source, exp.Select):
            rows = [source.expressions]
        elif isinstance(source, exp.Values):
            rows = [t.expressions for t in source.expressions]
        for row in rows:
            for column, value in zip(columns, row, strict=False):
                yield table, column, value
    elif isinstance(expression, exp.Update):
        for eq in expression.expressions:
            yield expression.this, eq.this.name.lower(), eq.expression
    elif isinstance(expression, exp.Merge):
        for when in expression.args["whens"].expressions:
            then = when.args["then"]
            if isinstance(then, exp.Update):
                for eq in then.expressions:
                    yield expression.this, eq.this.name.lower(), eq.expression
            elif isinstance(then, exp.Insert) and isinstance(then.this, exp.Tuple):
                columns = [c.name.lower() for c in then.this.expressions]
                for column, value in zip(columns, then.expression.expressions, strict=False):
                    yield expression.this, column, value


def _date_literal(node: exp.Expression) -> exp.Expression | None:
    if not (isinstance(node, exp.Literal) and node.is_string):
        return None
    match = _COMPACT_DATE.fullmatch(node.this)
    if match is None:
        return None
    try:
        value = date(*(int(g) for g in match.groups()))
    except ValueError:
        return None
    return exp.Cast(this=exp.Literal.string(value.isoformat()), to=exp.DataType.build("DATE"))


def compact_date_literal(expression: exp.Expression, resolver: TypeResolver) -> exp.Expression:
    result = expression.copy()
    changes: list[tuple[exp.Expression, exp.Expression]] = []
    groups: list[list[exp.Expression]] = []
    for node in result.find_all(*_COMPARISONS):
        groups.append([node.this, node.expression])
    for node in result.find_all(exp.Coalesce):
        groups.append([node.this, *node.expressions])
    for node in result.find_all(exp.Between):
        groups.append([node.this, node.args["low"], node.args["high"]])
    for node in result.find_all(exp.In):
        groups.append([node.this, *node.expressions])
    for operands in groups:
        if any(is_date_like(resolver.type_of(o)) for o in operands):
            for operand in operands:
                replacement = _date_literal(operand)
                if replacement is not None:
                    changes.append((operand, replacement))
    for node, replacement in changes:
        node.replace(replacement)
    return result


def changed(before: exp.Expression, after: exp.Expression) -> bool:
    return before.sql(dialect="databricks") != after.sql(dialect="databricks")
