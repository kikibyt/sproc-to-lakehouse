"""Symbolic resolution of T-SQL parameters and local variables.

Instead of executing the procedure, we track what each ``@variable`` *means* as a SQL
expression and substitute that expression wherever the variable is used:

* parameters become placeholders, rendered per target (``:name`` in a notebook,
  a ``var()`` lookup in dbt);
* ``DECLARE @x DATE = EOMONTH(@p)`` binds ``@x`` to ``LAST_DAY(<@p>)``;
* ``IF @p IS NULL SET @p = <e>`` (parameter defaulting) binds ``@p`` to
  ``COALESCE(<@p>, <e>)``;
* ``SELECT @x = <agg> FROM ...`` binds ``@x`` to a scalar placeholder that the target
  resolves (collected value in a notebook, inline scalar subquery in dbt);
* anything else, such as assignment inside a loop or from ``@@ROWCOUNT``, leaves the
  variable *unresolved*, and every statement that uses it becomes a manual step.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlglot import exp

from sproc_to_lakehouse.models import Parameter

PARAM_PREFIX = "__p__"
SCALAR_PREFIX = "__s__"


@dataclass(frozen=True)
class Unresolved:
    reason: str
    line: int


@dataclass
class ScalarQuery:
    """A variable loaded from a single-row query (``SELECT @x = MAX(a) FROM t``)."""

    name: str  # without @
    query: exp.Select
    line: int


@dataclass
class ParamInfo:
    name: str  # without @
    tsql_type: str
    databricks_type: str
    is_text: bool
    default: str | None  # T-SQL literal text, e.g. "NULL", "7", "N'all'"


def _is_variable(node: exp.Expression) -> bool:
    """``@x`` used as a value; ``FROM @t`` (a table variable) is a table, not a value."""
    return isinstance(node, exp.Parameter) and not isinstance(node.parent, exp.Table)


def param_info(param: Parameter) -> ParamInfo:
    dtype = exp.DataType.build(param.data_type, dialect="tsql")
    if dtype.is_type(*exp.DataType.TEMPORAL_TYPES):
        dtype.set("expressions", [])  # DATETIME2(0) -> TIMESTAMP; Databricks takes no precision
    return ParamInfo(
        name=param.name.lstrip("@").lower(),
        tsql_type=param.data_type,
        databricks_type=dtype.sql(dialect="databricks"),
        is_text=dtype.is_type(*exp.DataType.TEXT_TYPES),
        default=param.default,
    )


def placeholder(prefix: str, name: str) -> exp.Placeholder:
    return exp.Placeholder(this=f"{prefix}{name}")


@dataclass
class VariableEnv:
    params: dict[str, ParamInfo] = field(default_factory=dict)
    bindings: dict[str, exp.Expression | Unresolved] = field(default_factory=dict)
    scalars: dict[str, ScalarQuery] = field(default_factory=dict)

    @classmethod
    def for_parameters(cls, parameters: list[Parameter]) -> VariableEnv:
        env = cls()
        for param in parameters:
            info = param_info(param)
            env.params[info.name] = info
            ph: exp.Expression = placeholder(PARAM_PREFIX, info.name)
            if not info.is_text:
                ph = exp.Cast(
                    this=ph, to=exp.DataType.build(info.databricks_type, dialect="databricks")
                )
            if param.is_output:
                env.bindings[info.name] = Unresolved(f"@{info.name} is an OUTPUT parameter", 0)
            else:
                env.bindings[info.name] = ph
        return env

    # -- binding -------------------------------------------------------------------
    def declare(self, name: str, default: exp.Expression | None, line: int) -> None:
        self.assign(name, default if default is not None else exp.Null(), line)

    def assign(self, name: str, value: exp.Expression, line: int) -> None:
        self.bindings[name.lower()] = self.resolve(value, line)

    def assign_default(self, name: str, value: exp.Expression, line: int) -> None:
        """``IF @x IS NULL SET @x = value``."""
        current = self.bindings.get(name.lower())
        resolved = self.resolve(value, line)
        if isinstance(current, Unresolved) or isinstance(resolved, Unresolved):
            self.bindings[name.lower()] = current if isinstance(current, Unresolved) else resolved
            return
        current_expr = current if current is not None else exp.Null()
        self.bindings[name.lower()] = exp.Coalesce(this=current_expr.copy(), expressions=[resolved])

    def assign_scalar(self, name: str, query: exp.Select, line: int) -> None:
        resolved = self.resolve(query, line)
        if isinstance(resolved, Unresolved):
            self.bindings[name.lower()] = resolved
            return
        self.scalars[name.lower()] = ScalarQuery(name.lower(), resolved, line)
        self.bindings[name.lower()] = placeholder(SCALAR_PREFIX, name.lower())

    def mark_unresolved(self, name: str, reason: str, line: int) -> None:
        self.bindings[name.lower()] = Unresolved(reason, line)

    # -- substitution --------------------------------------------------------------
    def unresolved_in(self, expression: exp.Expression) -> Unresolved | None:
        for node in expression.find_all(exp.Parameter):
            if not _is_variable(node):
                continue
            binding = self.bindings.get(node.name.lower())
            if binding is None:
                return Unresolved(f"@{node.name} is never declared", 0)
            if isinstance(binding, Unresolved):
                return binding
        return None

    def resolve(self, expression: exp.Expression, line: int) -> exp.Expression | Unresolved:
        """Copy ``expression`` with every ``@variable`` replaced by its binding."""
        problem = self.unresolved_in(expression)
        if problem is not None:
            return problem

        def substitute(node: exp.Expression) -> exp.Expression:
            if _is_variable(node):
                binding = self.bindings[node.name.lower()]
                assert not isinstance(binding, Unresolved)
                replacement = binding.copy()
                needs_parens = not isinstance(
                    replacement, (exp.Placeholder, exp.Literal, exp.Null, exp.Func, exp.Cast)
                )
                return exp.Paren(this=replacement) if needs_parens else replacement
            return node

        return expression.copy().transform(substitute)
