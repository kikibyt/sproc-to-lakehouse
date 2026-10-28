"""Generated SQL checked against a local Apache Spark session.

Needs the optional ``spark`` extra (``pip install -e ".[spark]"``) and Java 17+;
skipped otherwise. See sproc_to_lakehouse.validation.spark_check for what is checked.
"""

from __future__ import annotations

import shutil

import pytest

pytest.importorskip("pyspark")
if shutil.which("java") is None:  # pragma: no cover - depends on the machine
    pytest.skip("Java runtime not found", allow_module_level=True)

from sproc_to_lakehouse.codegen.dbt import generate_dbt_project
from sproc_to_lakehouse.validation.spark_check import (
    check_dbt_project,
    check_plan,
    create_tables,
    spark_session,
)

pytestmark = pytest.mark.spark


@pytest.fixture(scope="module")
def spark(catalog):
    session = spark_session()
    session.sparkContext.setLogLevel("OFF")
    create_tables(session, catalog)
    yield session
    session.stop()


def test_every_converted_notebook_step_is_valid_spark_sql(spark, corpus_plans):
    results = [r for plan in corpus_plans.values() for r in check_plan(spark, plan)]
    errors = [f"{r.procedure} line {r.line} {r.step}: {r.error}" for r in results if not r.ok]
    assert errors == []
    checked = [r for r in results if r.level != "skipped"]
    assert len(checked) == 19  # update this count if the corpus changes


def test_every_generated_dbt_model_is_valid_spark_sql(spark, corpus_plans, catalog):
    project = generate_dbt_project(list(corpus_plans.values()), catalog)
    results = check_dbt_project(spark, project)
    assert [f"{r.step}: {r.error}" for r in results if not r.ok] == []
    assert len(results) == 10
