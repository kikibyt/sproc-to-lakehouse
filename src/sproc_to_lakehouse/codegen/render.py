"""Render plan SQL to Databricks SQL text with target-specific names and parameters.

Plan expressions refer to tables by their T-SQL names and to values through placeholders
(``:__p__name`` for parameters, ``:__s__name`` for loaded variables). A
:class:`SqlRenderer` turns those into what a target needs: ``:name`` markers for
``spark.sql(..., args=...)`` in a notebook, or Jinja such as ``{{ ref('x') }}`` and
``{{ var('p') }}`` in dbt.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from sqlglot import exp

from sproc_to_lakehouse.codegen.variables import PARAM_PREFIX, SCALAR_PREFIX
from sproc_to_lakehouse.models import TableRef
from sproc_to_lakehouse.parser.tables import table_ref

_TABLE_MARK = "__t{}__"
_TABLE_MARK_RE = re.compile(r"__t(\d+)__")
_PARAM_RE = re.compile(rf":{PARAM_PREFIX}(\w+)")
_SCALAR_RE = re.compile(rf":{SCALAR_PREFIX}(\w+)")
_NOW_MARK = "__now__"


@dataclass
class SqlRenderer:
    table: Callable[[TableRef], str]
    """Text for a permanent table reference."""
    param: Callable[[str], str]
    """Text for a procedure parameter."""
    scalar: Callable[[str], str]
    """Text for a variable loaded by a SCALAR step."""
    now: Callable[[], str] | None = None
    """Optional replacement for CURRENT_TIMESTAMP() (e.g. dbt's run_started_at)."""

    def render(self, expression: exp.Expression, pretty: bool = True) -> str:
        expression = expression.copy()
        local_names = _local_relation_names(expression)
        tables: list[TableRef] = []

        def mark(node: exp.Expression) -> exp.Expression:
            if isinstance(node, exp.Table) and isinstance(node.this, exp.Identifier):
                ref = table_ref(node)
                if not ref.schema and ref.name in local_names:
                    return node  # CTE or temp view reference
                tables.append(ref)
                marked = exp.Table(this=exp.to_identifier(_TABLE_MARK.format(len(tables) - 1)))
                if node.alias:
                    marked.set("alias", exp.TableAlias(this=exp.to_identifier(node.alias)))
                return marked
            if self.now is not None and isinstance(node, exp.CurrentTimestamp):
                return exp.column(_NOW_MARK)
            if self.now is not None and isinstance(node, exp.CurrentDate):
                return exp.Cast(this=exp.column(_NOW_MARK), to=exp.DataType.build("DATE"))
            return node

        text = expression.transform(mark).sql(dialect="databricks", pretty=pretty)
        text = _TABLE_MARK_RE.sub(lambda m: self.table(tables[int(m.group(1))]), text)
        text = _PARAM_RE.sub(lambda m: self.param(m.group(1)), text)
        text = _SCALAR_RE.sub(lambda m: self.scalar(m.group(1)), text)
        if self.now is not None:
            text = text.replace(_NOW_MARK, self.now())
        return text


def _local_relation_names(expression: exp.Expression) -> set[str]:
    names = {cte.alias_or_name.lower() for cte in expression.find_all(exp.CTE)}
    names |= {
        t.name.lower()
        for t in expression.find_all(exp.Table)
        if not t.db and t.name.lower().startswith("tmp_")
    }
    return names


def params_used(expression: exp.Expression) -> list[str]:
    """Parameter names referenced by an expression (each once)."""
    return _placeholders(expression, PARAM_PREFIX)


def scalars_used(expression: exp.Expression) -> list[str]:
    return _placeholders(expression, SCALAR_PREFIX)


def _placeholders(expression: exp.Expression, prefix: str) -> list[str]:
    names: list[str] = []
    for node in expression.find_all(exp.Placeholder):
        name = str(node.this or "")
        if name.startswith(prefix) and name[len(prefix) :] not in names:
            names.append(name[len(prefix) :])
    return names
