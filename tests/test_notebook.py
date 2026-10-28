"""Databricks notebook (.py source format) generation."""

from __future__ import annotations

import ast

import pytest

from sproc_to_lakehouse.codegen.notebook import CELL, HEADER, generate_notebook


def cells(source: str) -> list[str]:
    assert source.startswith(HEADER)
    return source[len(HEADER) :].split(CELL)


@pytest.mark.parametrize(
    "name",
    [
        "usp_load_daily_dispense_summary",
        "usp_upsert_patient_enrollment",
        "usp_shipment_sla_report",
        "usp_update_shipment_status",
        "usp_recalc_reorder_points_cursor",
    ],
)
def test_notebook_is_valid_python_in_databricks_source_format(corpus_plans, name):
    source = generate_notebook(corpus_plans[name])
    ast.parse(source)  # MAGIC lines are comments, so the whole file must be valid Python
    assert all(c.strip() for c in cells(source))


def test_parameters_become_widgets_and_named_markers(corpus_plans):
    source = generate_notebook(corpus_plans["usp_calc_refill_gaps"])
    assert 'dbutils.widgets.text("gap_threshold_days", "7")' in source
    assert 'as_of_date = dbutils.widgets.get("as_of_date") or None' in source
    assert "CAST(:as_of_date AS DATE)" in source
    assert 'args={"as_of_date": as_of_date, "gap_threshold_days": gap_threshold_days}' in source


def test_only_used_parameters_are_passed(corpus_plans):
    source = generate_notebook(corpus_plans["usp_calc_refill_gaps"])
    truncate_cell = next(c for c in cells(source) if "TRUNCATE TABLE" in c)
    assert "args={}" in truncate_cell


def test_loaded_variable_is_collected_once_then_passed(corpus_plans):
    source = generate_notebook(corpus_plans["usp_load_daily_dispense_summary"])
    assert 'v_run_date = spark.sql(r"""' in source and '.first()["value"]' in source
    assert source.count('args={"v_run_date": v_run_date}') == 2


def test_manual_steps_show_original_tsql_and_stop_the_run(corpus_plans):
    source = generate_notebook(corpus_plans["usp_recalc_reorder_points_cursor"])
    assert "**Manual conversion required.**" in source
    assert "# MAGIC DECLARE product_cursor CURSOR LOCAL FAST_FORWARD FOR" in source
    assert "raise NotImplementedError(" in source
    assert "converted without manual steps" not in source


def test_review_notes_and_dropped_statements_are_listed(corpus_plans):
    source = generate_notebook(corpus_plans["usp_build_monthly_inventory_snapshot"])
    assert "`EX009` ISNULL becomes COALESCE" in source
    assert "temp table cleanup is not needed" in source
    assert "Rewrites applied: transient_table_to_cte." in source
