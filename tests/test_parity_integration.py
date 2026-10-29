"""End-to-end parity: original T-SQL on SQL Server vs generated code on Spark + Delta.

Needs a running SQL Server (see docker-compose.yml), the ``parity`` extra and Java 17+.
Skipped unless SPROC2LH_MSSQL_PASSWORD is set. Optional: SPROC2LH_MSSQL_HOST/PORT, and
SPROC2LH_DELTA_JARS (comma-separated local Delta jars) when Maven is unreachable.
"""

from __future__ import annotations

import os

import pytest

PASSWORD = os.environ.get("SPROC2LH_MSSQL_PASSWORD")
if not PASSWORD:
    pytest.skip("SPROC2LH_MSSQL_PASSWORD not set (no SQL Server)", allow_module_level=True)
pytest.importorskip("pymssql")
pytest.importorskip("pyspark")
pytest.importorskip("delta")

from sproc_to_lakehouse.parity.datagen import generate, load_spec  # noqa: E402
from sproc_to_lakehouse.parity.runner import load_scenarios, run_all, run_scenario  # noqa: E402
from sproc_to_lakehouse.parity.spark import SparkEngine, spark_session  # noqa: E402
from sproc_to_lakehouse.parity.sqlserver import SqlServerConfig, SqlServerEngine  # noqa: E402

from .conftest import SAMPLES_DIR  # noqa: E402

pytestmark = pytest.mark.parity


@pytest.fixture(scope="module")
def engines(catalog):
    sqlserver = SqlServerEngine(
        SqlServerConfig(
            host=os.environ.get("SPROC2LH_MSSQL_HOST", "localhost"),
            port=int(os.environ.get("SPROC2LH_MSSQL_PORT", "1433")),
            password=PASSWORD,
            database="sproc_parity_test",
        )
    )
    sqlserver.setup(
        sorted((SAMPLES_DIR / "ddl").glob("*.sql")), sorted((SAMPLES_DIR / "tsql").glob("*.sql"))
    )
    spark = SparkEngine(spark_session())
    spark.setup(catalog)
    yield sqlserver, spark
    sqlserver.close()


@pytest.fixture(scope="module")
def data(catalog):
    return generate(load_spec(SAMPLES_DIR / "parity" / "data.yml"), catalog)


def test_every_fully_converted_procedure_matches(engines, corpus_plans, catalog, data):
    scenarios = load_scenarios(SAMPLES_DIR / "parity" / "scenarios.yml")
    results = run_all(scenarios, corpus_plans, data, catalog, *engines)
    by_status = {r.procedure: r.status for r in results}
    expected_match = {name for name, plan in corpus_plans.items() if plan.is_fully_converted}
    assert {p for p, s in by_status.items() if s == "match"} == expected_match, [
        (r.procedure, r.status, r.reason) for r in results if r.status in ("mismatch", "error")
    ]
    assert {p for p, s in by_status.items() if s == "not_run"} == set(corpus_plans) - expected_match
    # Non-vacuous: every compared table was actually changed by its procedure.
    for r in results:
        for table, change in r.changes.items():
            assert change["inserted"] + change["updated"] + change["deleted"] > 0, (
                r.procedure,
                table,
            )


class _DifferentParams:
    """Wraps the Spark engine and runs it with one parameter changed."""

    def __init__(self, engine, **overrides):
        self.engine, self.overrides = engine, overrides

    def load(self, data, catalog):
        self.engine.load(data, catalog)

    def run(self, plan, params):
        return self.engine.run(plan, {**params, **self.overrides})

    def read(self, table):
        return self.engine.read(table)


def test_harness_detects_a_real_difference(engines, corpus_plans, catalog, data):
    sqlserver, spark = engines
    (scenario,) = [
        s for s in load_scenarios(SAMPLES_DIR / "parity" / "scenarios.yml")
        if s.procedure == "usp_calc_refill_gaps"
    ]  # fmt: skip
    mutated = _DifferentParams(spark, gap_threshold_days="8")
    result = run_scenario(
        scenario, corpus_plans[scenario.procedure], data, catalog, sqlserver, mutated
    )
    assert result.status == "mismatch"
    diffs = result.comparisons[0].value_diffs
    assert diffs and {d.column for d in diffs} == {"gap_category"}
