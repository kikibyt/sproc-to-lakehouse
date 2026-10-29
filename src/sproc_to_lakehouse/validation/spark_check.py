"""Check generated SQL against a local Apache Spark session (no Databricks needed).

Every source table from the DDL is created empty, then each plan step is checked the
way the generated notebook would run it:

* ``executed``: queries, INSERT ... SELECT, TRUNCATE, scalar loads and temp views run
  for real against the empty tables, so names, functions and types must all resolve;
* ``analyzed``: MERGE / UPDATE / DELETE need Delta Lake, which this harness does not
  load, so they are parsed by Spark's own parser and their expressions are resolved
  through an equivalent SELECT;
* manual steps are skipped.

This proves the SQL is valid Spark SQL that resolves against the schema. It does not
prove the results equal SQL Server's; that is the parity harness's job (Phase 4).
Requires the optional ``pyspark`` dependency and a Java runtime.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlglot import exp

from sproc_to_lakehouse.catalog import Catalog
from sproc_to_lakehouse.codegen.notebook import scalar_name
from sproc_to_lakehouse.codegen.plan import Plan, Step, StepKind
from sproc_to_lakehouse.codegen.render import SqlRenderer
from sproc_to_lakehouse.codegen.variables import ParamInfo

_DELTA_ONLY = ("update", "delete", "merge", "truncate")  # need Delta Lake to execute
SAMPLE_VALUES = {
    "DATE": "2024-01-31",
    "TIMESTAMP": "2024-01-31 18:00:00",
}


@dataclass
class CheckResult:
    procedure: str
    step: str
    line: int
    level: str  # executed | analyzed | skipped
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def spark_session():  # pragma: no cover - exercised by the optional Spark tests
    from pyspark.sql import SparkSession

    warehouse = tempfile.mkdtemp(prefix="sproc2lh-warehouse-")
    return (
        SparkSession.builder.master("local[1]")
        .appName("sproc-to-lakehouse-check")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.shuffle.partitions", "1")
        .config("spark.sql.warehouse.dir", warehouse)
        .config("spark.sql.session.timeZone", "UTC")
        .getOrCreate()
    )


def create_tables(spark, catalog: Catalog) -> None:
    """Create every catalog table, empty, as a Parquet table."""
    for table in catalog._tables.values():
        schema = table.schema or "default"
        spark.sql(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        columns = ", ".join(f"{c.name} {c.databricks_type}" for c in table.columns)
        spark.sql(f"DROP TABLE IF EXISTS {schema}.{table.name}")
        spark.sql(f"CREATE TABLE {schema}.{table.name} ({columns}) USING parquet")


def sample_value(info: ParamInfo) -> str:
    if info.is_text:
        return "X"
    return SAMPLE_VALUES.get(info.databricks_type.split("(")[0].upper(), "1")


def _notebook_renderer() -> SqlRenderer:
    return SqlRenderer(
        table=lambda ref: ref.qualified_name,
        param=lambda name: f":{name}",
        scalar=lambda name: f":{scalar_name(name)}",
    )


def check_plan(spark, plan: Plan) -> list[CheckResult]:
    renderer = _notebook_renderer()
    args: dict[str, Any] = {name: sample_value(info) for name, info in plan.env.params.items()}
    results = []
    for step in plan.steps:
        result = CheckResult(plan.name, step.title, step.line, "skipped")
        if step.kind in (StepKind.MANUAL, StepKind.PRINT):
            results.append(result)
            continue
        sql = renderer.render(step.sql)
        try:
            result.level = _check_step(spark, step, sql, args)
        except Exception as exc:  # report Spark's message, keep checking other steps
            result.level = "analyzed" if step.operation in _DELTA_ONLY else "executed"
            result.error = _first_line(exc)
        results.append(result)
    return results


def _check_step(spark, step: Step, sql: str, args: dict[str, Any]) -> str:
    if step.kind is StepKind.SCALAR:
        row = spark.sql(sql, args=args).first()
        args[scalar_name(step.name)] = row["value"] if row is not None else None
        return "executed"
    if step.kind is StepKind.TEMP_VIEW:
        spark.sql(sql, args=args).createOrReplaceTempView(step.name)
        return "executed"
    if step.operation in _DELTA_ONLY:
        analyze_dml(spark, sql, args)
        return "analyzed"
    spark.sql(sql, args=args).collect()
    return "executed"


def analyze_dml(spark, sql: str, args: dict[str, Any]) -> None:
    """Parse a Delta-only statement with Spark, then resolve its expressions via SELECT."""
    spark._jsparkSession.sessionState().sqlParser().parsePlan(sql)
    spark.sql(equivalent_select(sql), args=args).collect()


def equivalent_select(sql: str) -> str:
    """A SELECT that references every expression of an UPDATE / DELETE / MERGE."""
    statement = sqlglot.parse_one(sql, read="databricks")
    with_ = statement.args.get("with_")
    if isinstance(statement, exp.Merge):
        values: list[exp.Expression] = []
        conditions: list[exp.Expression] = []
        for when in statement.args["whens"].expressions:
            if when.args.get("condition") is not None:
                conditions.append(when.args["condition"])
            then = when.args["then"]
            if isinstance(then, exp.Update):
                values.extend(eq.expression for eq in then.expressions)
            elif isinstance(then, exp.Insert):
                values.extend(then.expression.expressions)
        projections = [v.copy() for v in values + conditions] or [exp.Literal.number(1)]
        select = exp.select(*projections)
        select = select.from_(statement.this.copy()).join(
            statement.args["using"].copy(), on=statement.args["on"].copy()
        )
    elif isinstance(statement, exp.Update):
        select = exp.select(*(eq.expression.copy() for eq in statement.expressions))
        select = select.from_(statement.this.copy())
        if statement.args.get("where") is not None:
            select = select.where(statement.args["where"].this.copy())
    elif isinstance(statement, exp.Delete):
        select = exp.select("*").from_(statement.this.copy())
        if statement.args.get("where") is not None:
            select = select.where(statement.args["where"].this.copy())
    else:
        raise ValueError(f"not a Delta DML statement: {type(statement).__name__}")
    if with_ is not None:
        select.set("with_", with_.copy())
    return select.sql(dialect="databricks")


def _first_line(exc: Exception) -> str:
    text = str(exc).strip().splitlines()
    return text[0][:300] if text else type(exc).__name__


def check_dbt_project(spark, project) -> list[CheckResult]:
    """Render every generated model (full and incremental builds) and check it with Spark.

    Model SQL runs against the empty tables; ``incremental_predicates`` are checked as a
    WHERE clause on the target; post-hooks go through :func:`analyze_dml`.
    """
    from sproc_to_lakehouse.validation.dbt_render import model_files, render_model

    templates = model_files(project)
    refs = {
        spec.name: spec.target.qualified_name if spec.target else spec.name
        for mapping in project.mappings
        for spec in mapping.models
    }
    results: list[CheckResult] = []
    for mapping in project.mappings:
        variables = {name: sample_value(info) for name, info in mapping.plan.env.params.items()}
        for spec in mapping.models:
            this = spec.target.qualified_name if spec.target else spec.name
            modes = (False, True) if spec.materialized == "incremental" else (False,)
            for incremental in modes:
                label = f"dbt model {spec.name}" + (" (incremental)" if incremental else "")
                result = CheckResult(mapping.plan.name, label, spec.primary.line, "executed")
                try:
                    rendered = render_model(
                        templates[spec.name],
                        this=this,
                        variables=variables,
                        refs=refs,
                        incremental=incremental,
                    )
                    spark.sql(rendered.sql).collect()
                    for predicate in rendered.config.get("incremental_predicates") or []:
                        spark.sql(f"SELECT * FROM {this} WHERE {predicate}").collect()
                    for hook in rendered.config.get("post_hook") or []:
                        analyze_dml(spark, hook, {})
                except Exception as exc:
                    result.error = _first_line(exc)
                results.append(result)
    return results
