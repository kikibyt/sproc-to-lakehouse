from __future__ import annotations

from pathlib import Path

import pytest

from sproc_to_lakehouse.models import Procedure
from sproc_to_lakehouse.parser import parse_procedures

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLES_DIR = REPO_ROOT / "samples"


def make_procedure(body: str, params: str = "") -> Procedure:
    """Wrap a T-SQL body in CREATE PROCEDURE and parse it."""
    sql = f"CREATE PROCEDURE dbo.usp_test {params}\nAS\nBEGIN\n{body}\nEND\nGO\n"
    procedures = parse_procedures(sql)
    assert len(procedures) == 1
    return procedures[0]


@pytest.fixture
def proc():
    return make_procedure


@pytest.fixture(scope="session")
def catalog():
    from sproc_to_lakehouse.catalog import Catalog

    return Catalog.from_files([SAMPLES_DIR / "ddl" / "00_schema.sql"])


@pytest.fixture(scope="session")
def corpus_plans(catalog):
    """Plans for the 12 sample procedures, keyed by procedure name."""
    from sproc_to_lakehouse.codegen import build_plans

    plans, _ = build_plans(SAMPLES_DIR / "tsql", SAMPLES_DIR / "ddl")
    return {plan.name: plan for plan in plans}


@pytest.fixture
def plan_for(catalog):
    """Build a plan for a small T-SQL body (with the sample DDL loaded)."""
    from sproc_to_lakehouse.analyzer import analyze
    from sproc_to_lakehouse.codegen.plan import build_plan

    def build(body: str, params: str = ""):
        return build_plan(analyze(make_procedure(body, params), catalog), catalog)

    return build
