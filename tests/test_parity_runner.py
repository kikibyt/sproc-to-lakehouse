"""Parity runner and report, driven by in-memory fake engines (no database needed)."""

from __future__ import annotations

from sproc_to_lakehouse.parity.compare import RowSet
from sproc_to_lakehouse.parity.report import report_data, report_markdown
from sproc_to_lakehouse.parity.runner import Scenario, load_scenarios, run_scenario, tables_written

from .conftest import SAMPLES_DIR


class FakeEngine:
    """Holds tables in memory; ``procedure`` mutates them like the real procedure would."""

    def __init__(self, procedure=None, results=None, fail=None):
        self.tables: dict[str, RowSet] = {}
        self.procedure = procedure
        self.results = results or []
        self.fail = fail

    def load(self, data, catalog):
        self.tables = {}
        for table in catalog._tables.values():
            columns = [c.name for c in table.columns]
            rows = data.get(table.qualified_name) or []
            self.tables[table.qualified_name] = RowSet(
                columns, [tuple(r[c] for c in columns) for r in rows]
            )

    def run(self, *args):
        if self.fail:
            raise RuntimeError(self.fail)
        if self.procedure:
            self.procedure(self.tables)
        return self.results

    def read(self, table):
        return self.tables[table]


def add_gap_row(value: str):
    def procedure(tables):
        rowset = tables["dbo.refill_gap_events"]
        row = ["PT000001", 1, 1, None, None, None, 5, value, True, None]
        rowset.rows.append(tuple(row))

    return procedure


SCENARIO = Scenario("usp_calc_refill_gaps", {"as_of_date": "2024-03-31", "gap_threshold_days": "7"})


def test_matching_engines_produce_a_match_with_change_counts(corpus_plans, catalog):
    plan = corpus_plans["usp_calc_refill_gaps"]
    result = run_scenario(
        SCENARIO,
        plan,
        {},
        catalog,
        FakeEngine(add_gap_row("MINOR_GAP")),
        FakeEngine(add_gap_row("MINOR_GAP")),
    )
    assert result.status == "match"
    assert [c.name for c in result.comparisons] == ["dbo.refill_gap_events"]
    assert result.changes["dbo.refill_gap_events"] == {
        "before": 0,
        "after": 1,
        "inserted": 1,
        "deleted": 0,
        "updated": 0,
    }


def test_a_single_different_value_is_a_mismatch(corpus_plans, catalog):
    plan = corpus_plans["usp_calc_refill_gaps"]
    result = run_scenario(
        SCENARIO,
        plan,
        {},
        catalog,
        FakeEngine(add_gap_row("MINOR_GAP")),
        FakeEngine(add_gap_row("MAJOR_GAP")),
    )
    assert result.status == "mismatch"
    (diff,) = result.comparisons[0].value_diffs
    assert (diff.column, diff.sqlserver, diff.spark) == ("gap_category", "MINOR_GAP", "MAJOR_GAP")


def test_result_sets_are_compared(corpus_plans, catalog):
    plan = corpus_plans["usp_shipment_sla_report"]
    scenario = Scenario("usp_shipment_sla_report", {"week_start": "2024-03-04"})
    sql = FakeEngine(results=[RowSet(["region_code", "n"], [("NORTH", 3)])])
    spark = FakeEngine(results=[RowSet(["region_code", "n"], [("NORTH", 4)])])
    result = run_scenario(scenario, plan, {}, catalog, sql, spark)
    assert result.status == "mismatch" and result.comparisons[0].name == "result set 1"


def test_partly_converted_procedures_are_not_run(corpus_plans, catalog):
    name = "usp_recalc_reorder_points_cursor"
    result = run_scenario(
        Scenario(name), corpus_plans[name], {}, catalog, FakeEngine(), FakeEngine()
    )
    assert result.status == "not_run" and "manual step" in result.reason


def test_engine_failure_and_missing_params_are_errors(corpus_plans, catalog):
    plan = corpus_plans["usp_calc_refill_gaps"]
    failed = run_scenario(SCENARIO, plan, {}, catalog, FakeEngine(fail="boom"), FakeEngine())
    assert failed.status == "error" and failed.reason == "boom"
    missing = run_scenario(
        Scenario("usp_calc_refill_gaps"), plan, {}, catalog, FakeEngine(), FakeEngine()
    )
    assert missing.status == "error" and "as_of_date" in missing.reason


def test_report_markdown(corpus_plans, catalog):
    plan = corpus_plans["usp_calc_refill_gaps"]
    result = run_scenario(
        SCENARIO, plan, {}, catalog, FakeEngine(add_gap_row("A")), FakeEngine(add_gap_row("B"))
    )
    env = {
        "sqlserver": "SQL Server X",
        "spark": "Spark Y",
        "data_rows": 0,
        "data_tables": 0,
        "seed": 1,
    }
    md = report_markdown(report_data([result], env))
    assert "**0 match**, 1 mismatch" in md
    assert (
        "| usp_calc_refill_gaps | MISMATCH | dbo.refill_gap_events | 1 / 1 | 9/10 | +1 ~0 -0 |"
        in md
    )
    assert "| gap_category |" in md and "**no**" in md


def test_sample_scenarios_cover_every_procedure(corpus_plans):
    scenarios = load_scenarios(SAMPLES_DIR / "parity" / "scenarios.yml")
    assert {s.procedure for s in scenarios} == set(corpus_plans)
    for s in scenarios:
        plan = corpus_plans[s.procedure]
        if plan.is_fully_converted:
            assert set(plan.env.params) <= set(s.params), s.procedure
            for table in s.ignore_columns:
                assert table in tables_written(plan)
