"""Conversion plans: variable resolution, temp tables, manual steps."""

from __future__ import annotations

import pytest

from sproc_to_lakehouse.codegen import IMPLEMENTED_REWRITES
from sproc_to_lakehouse.codegen.plan import StepKind

W, R, S, V, M = (
    StepKind.WRITE,
    StepKind.RESULT,
    StepKind.SCALAR,
    StepKind.TEMP_VIEW,
    StepKind.MANUAL,
)


def dbx(step) -> str:
    return step.sql.sql(dialect="databricks")


@pytest.mark.parametrize(
    ("name", "kinds"),
    [
        ("usp_load_daily_dispense_summary", [S, W, W]),
        ("usp_build_monthly_inventory_snapshot", [W, W, W]),
        ("usp_upsert_patient_enrollment", [W]),
        ("usp_calc_refill_gaps", [W, W]),
        ("usp_classify_payer_claims", [W, W]),
        ("usp_standardize_patient_contact", [W, M]),
        ("usp_shipment_sla_report", [R]),
        ("usp_update_shipment_status", [S, W, M, W, M, W, M]),
        ("usp_rebuild_prescriber_rollup", [M, W, M]),
        ("usp_recalc_reorder_points_cursor", [M]),
        ("usp_export_dispenses_by_region_dynamic", [M]),
        ("usp_close_month_end", [W, M]),
    ],
)
def test_corpus_step_sequences(corpus_plans, name, kinds):
    assert [s.kind for s in corpus_plans[name].steps] == kinds


@pytest.mark.parametrize(
    "name", ["usp_standardize_patient_contact", "usp_recalc_reorder_points_cursor"]
)
def test_manual_steps_keep_original_tsql_and_reasons(corpus_plans, name):
    for step in corpus_plans[name].manual_steps:
        assert step.reasons and step.source_sql.strip()


def test_fully_converted_plans_apply_exactly_the_promised_rewrites(corpus_plans):
    for plan in corpus_plans.values():
        promised = {f.rewrite for f in plan.analysis.findings if f.rewrite}
        if plan.is_fully_converted:
            assert promised == plan.rewrites_used(), plan.name
        assert plan.rewrites_used() <= IMPLEMENTED_REWRITES


def test_parameter_default_folds_into_coalesce_and_is_loaded_once(plan_for):
    plan = plan_for(
        "IF @d IS NULL SET @d = CAST(GETDATE() AS DATE)\n"
        "DELETE FROM dbo.daily_dispense_summary WHERE summary_date = @d",
        params="@d DATE = NULL",
    )
    load, delete = plan.steps
    assert load.kind is S and "COALESCE(CAST(:__p__d AS DATE)" in dbx(load)
    assert dbx(delete).endswith("summary_date = :__s__d")


def test_derived_variables_are_inlined(plan_for):
    plan = plan_for(
        "DECLARE @end DATE = EOMONTH(@m)\n"
        "DELETE FROM dbo.inventory_snapshots WHERE snapshot_month = @end",
        params="@m DATE",
    )
    (delete,) = plan.steps
    assert dbx(delete) == (
        "DELETE FROM dbo.inventory_snapshots "
        "WHERE snapshot_month = LAST_DAY(TO_DATE(CAST(:__p__m AS DATE)))"
    )


def test_variable_from_rowcount_makes_later_use_manual(plan_for):
    plan = plan_for(
        "UPDATE dbo.shipments SET status = 'X'\nSET @n = @@ROWCOUNT\n"
        "INSERT INTO dbo.etl_run_log (rows_affected) VALUES (@n)",
        params="@n INT",
    )
    assert [s.kind for s in plan.steps] == [W, M]
    assert any("cannot be resolved" in r for r in plan.steps[1].reasons)


def test_single_use_temp_table_becomes_cte(plan_for):
    plan = plan_for(
        "SELECT product_id INTO #p FROM dbo.products\n"
        "INSERT INTO dbo.product_reorder_points (product_id) SELECT product_id FROM #p"
    )
    (insert,) = plan.steps
    assert dbx(insert).startswith("WITH tmp_p AS (SELECT product_id FROM dbo.products) INSERT")
    assert [d.reason for d in plan.dropped] == []


def test_temp_table_read_twice_becomes_temp_view(plan_for):
    plan = plan_for(
        "SELECT product_id INTO #p FROM dbo.products\n"
        "SELECT product_id FROM #p\nSELECT COUNT(*) FROM #p"
    )
    assert [s.kind for s in plan.steps] == [V, R, R]
    assert plan.steps[0].name == "tmp_p"


def test_mutated_temp_table_is_manual(plan_for):
    plan = plan_for(
        "SELECT product_id INTO #p FROM dbo.products\n"
        "UPDATE #p SET product_id = 0\n"
        "SELECT * FROM #p"
    )
    assert {s.kind for s in plan.steps} == {M}


def test_fixes_can_be_switched_off_for_the_ablation(catalog):
    from sproc_to_lakehouse.analyzer import analyze
    from sproc_to_lakehouse.codegen.plan import build_plan
    from sproc_to_lakehouse.parser import parse_file

    from .conftest import SAMPLES_DIR

    (proc,) = parse_file(SAMPLES_DIR / "tsql" / "02_usp_build_monthly_inventory_snapshot.sql")
    plan = build_plan(analyze(proc, catalog), catalog, fixes=False)
    update = plan.steps[-1]
    assert update.sql.sql(dialect="tsql").startswith("WITH trend AS")
    assert "MERGE" not in dbx(update) and "UPDATE s SET" in dbx(update)
    assert plan.rewrites_used() == {"transient_table_to_cte"}  # structural, always on
