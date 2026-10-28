"""Render generated dbt model files with plain Jinja, without installing dbt.

dbt's own functions are replaced by small stand-ins (``config``, ``var``, ``ref``,
``source``, ``this``, ``is_incremental``, ``run_started_at``, ``sql_literal``) so the
rendered SQL can be checked with Spark. This validates the generated templates and the
SQL they produce; it does not replace a real ``dbt build`` against Databricks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import jinja2

from sproc_to_lakehouse.codegen.dbt.project import DbtProject

_MISSING = object()
RUN_STARTED_AT = datetime(2024, 1, 31, 18, 0, 0)


def sql_literal(value: Any) -> str:
    """Python twin of the generated ``sql_literal`` macro."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


@dataclass
class RenderedModel:
    name: str
    sql: str
    config: dict[str, Any] = field(default_factory=dict)


class _CompilerError(Exception):
    pass


def render_model(
    template: str,
    *,
    this: str,
    variables: dict[str, Any],
    refs: dict[str, str],
    incremental: bool,
) -> RenderedModel:
    config: dict[str, Any] = {}

    def var(name: str, default: Any = _MISSING) -> Any:
        if name in variables:
            return variables[name]
        if default is _MISSING:
            raise _CompilerError(f"required var '{name}' not provided")
        return default

    def capture_config(**kwargs: Any) -> str:
        config.update(kwargs)
        return ""

    class _Exceptions:
        @staticmethod
        def raise_compiler_error(message: str) -> None:
            raise _CompilerError(message)

    env = jinja2.Environment(undefined=jinja2.StrictUndefined, extensions=["jinja2.ext.do"])
    context = {
        "config": capture_config,
        "var": var,
        "sql_literal": sql_literal,
        "source": lambda schema, table: f"{schema}.{table}",
        "ref": lambda name: refs[name],
        "this": this,
        "is_incremental": lambda: incremental,
        "run_started_at": RUN_STARTED_AT,
        "exceptions": _Exceptions,
    }
    sql = env.from_string(template).render(**context)
    hooks = config.get("post_hook") or []
    config["post_hook"] = [env.from_string(h).render(**context) for h in hooks]
    return RenderedModel(name="", sql=sql.strip(), config=config)


def model_files(project: DbtProject) -> dict[str, str]:
    """Model name -> template text for every generated model."""
    return {
        path.rsplit("/", 1)[-1].removesuffix(".sql"): text
        for path, text in project.files.items()
        if path.startswith("models/") and path.endswith(".sql")
    }
