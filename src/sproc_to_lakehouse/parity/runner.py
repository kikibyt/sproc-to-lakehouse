"""Run parity scenarios: same data, original T-SQL vs generated code, then compare."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from sqlglot import exp

from sproc_to_lakehouse.catalog import Catalog
from sproc_to_lakehouse.codegen.plan import Plan, StepKind
from sproc_to_lakehouse.parity.compare import Comparison, RowSet, compare


@dataclass
class Scenario:
    procedure: str
    params: dict[str, str] = field(default_factory=dict)
    ignore_columns: dict[str, list[str]] = field(default_factory=dict)
    compare: list[str] | None = None


@dataclass
class ScenarioResult:
    scenario: Scenario
    status: str  # match | mismatch | not_run | error
    reason: str | None = None
    comparisons: list[Comparison] = field(default_factory=list)
    sqlserver_seconds: float | None = None
    spark_seconds: float | None = None
    changes: dict[str, dict[str, int]] = field(default_factory=dict)
    """Per compared table, rows the procedure inserted / updated / deleted on SQL Server.
    Shows that a match is not vacuous (the procedure really changed data)."""

    @property
    def procedure(self) -> str:
        return self.scenario.procedure


def load_scenarios(path: Path) -> list[Scenario]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    scenarios = []
    for item in raw.get("scenarios") or []:
        scenarios.append(
            Scenario(
                procedure=item["procedure"],
                params={k: str(v) for k, v in (item.get("params") or {}).items()},
                ignore_columns={
                    k.lower(): v for k, v in (item.get("ignore_columns") or {}).items()
                },
                compare=item.get("compare"),
            )
        )
    return scenarios


def tables_written(plan: Plan) -> list[str]:
    seen: list[str] = []
    for step in plan.steps:
        if step.kind is StepKind.WRITE and step.target.qualified_name not in seen:
            seen.append(step.target.qualified_name)
    return seen


def _scales(catalog: Catalog, table: str) -> dict[str, int]:
    schema = catalog.get(table)
    scales: dict[str, int] = {}
    for column in schema.columns if schema else []:
        dtype = exp.DataType.build(column.data_type, dialect="tsql")
        if dtype.is_type(exp.DataType.Type.DECIMAL) and len(dtype.expressions) == 2:
            scales[column.name] = int(dtype.expressions[1].this.this)
    return scales


def run_scenario(
    scenario: Scenario,
    plan: Plan | None,
    data: dict[str, list[dict[str, Any]]],
    catalog: Catalog,
    sqlserver,
    spark,
) -> ScenarioResult:
    import time

    if plan is None:
        return ScenarioResult(scenario, "error", reason="procedure not found in the source files")
    if not plan.is_fully_converted:
        return ScenarioResult(
            scenario,
            "not_run",
            reason=f"{len(plan.manual_steps)} manual step(s) must be converted by a person first",
        )
    missing = [p for p in plan.env.params if p not in scenario.params]
    if missing:
        return ScenarioResult(scenario, "error", reason=f"no value for parameter(s) {missing}")

    proc = plan.analysis.procedure
    sqlserver.load(data, catalog)
    spark.load(data, catalog)
    try:
        start = time.perf_counter()
        sql_results = sqlserver.run(proc.schema, proc.name, scenario.params)
        sql_seconds = time.perf_counter() - start
        start = time.perf_counter()
        spark_results = spark.run(plan, scenario.params)
        spark_seconds = time.perf_counter() - start
    except Exception as exc:  # a failing engine is a parity result, not a crash
        return ScenarioResult(scenario, "error", reason=str(exc).strip().splitlines()[0][:400])

    result = ScenarioResult(
        scenario, "match", sqlserver_seconds=sql_seconds, spark_seconds=spark_seconds
    )
    for table in scenario.compare or tables_written(plan):
        schema = catalog.get(table)
        key = schema.primary_key if schema else None
        after = sqlserver.read(table)
        result.comparisons.append(
            compare(
                table,
                after,
                spark.read(table),
                key=key,
                ignore=scenario.ignore_columns.get(table.lower()),
                scales=_scales(catalog, table),
            )
        )
        before = RowSet(
            after.columns, [tuple(r[c] for c in after.columns) for r in data.get(table, [])]
        )
        delta = compare(table, before, after, key=key, scales=_scales(catalog, table))
        result.changes[table] = {
            "before": delta.sqlserver_rows,
            "after": delta.spark_rows,
            "inserted": delta.only_in_spark_count,
            "deleted": delta.only_in_sqlserver_count,
            "updated": delta.rows_with_value_diffs,
        }
    if len(sql_results) != len(spark_results):
        result.status = "mismatch"
        result.reason = (
            f"SQL Server returned {len(sql_results)} result set(s), Spark {len(spark_results)}"
        )
    for i, (left, right) in enumerate(zip(sql_results, spark_results, strict=False), start=1):
        result.comparisons.append(compare(f"result set {i}", left, right))
    if any(not c.match for c in result.comparisons):
        result.status = "mismatch"
    return result


def run_all(
    scenarios: list[Scenario],
    plans: dict[str, Plan],
    data: dict[str, list[dict[str, Any]]],
    catalog: Catalog,
    sqlserver,
    spark,
) -> list[ScenarioResult]:
    return [
        run_scenario(s, plans.get(s.procedure), data, catalog, sqlserver, spark) for s in scenarios
    ]
