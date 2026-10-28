"""dbt project generation: model patterns, configs, schema files, macros."""

from __future__ import annotations

import pytest
import yaml

from sproc_to_lakehouse.codegen.dbt import generate_dbt_project
from sproc_to_lakehouse.codegen.dbt.jinja import as_jinja_string
from sproc_to_lakehouse.validation.dbt_render import model_files, render_model


@pytest.fixture(scope="module")
def project(corpus_plans, catalog):
    return generate_dbt_project(list(corpus_plans.values()), catalog)


def mapping(project, name):
    return next(m for m in project.mappings if m.plan.name == name)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (
            "usp_load_daily_dispense_summary",
            [("daily_dispense_summary", "incremental", "replace_where")],
        ),
        (
            "usp_build_monthly_inventory_snapshot",
            [("inventory_snapshots", "incremental", "replace_where")],
        ),
        ("usp_upsert_patient_enrollment", [("dim_patient_enrollment", "incremental", "merge")]),
        ("usp_calc_refill_gaps", [("refill_gap_events", "table", None)]),
        ("usp_classify_payer_claims", [("claim_classification", "incremental", "replace_where")]),
        ("usp_shipment_sla_report", [("shipment_sla_report", "view", None)]),
    ],
)
def test_fully_converted_procedures_become_models(project, name, expected):
    m = mapping(project, name)
    assert not m.as_macro
    assert [(s.name, s.materialized, s.strategy) for s in m.models] == expected


@pytest.mark.parametrize(
    "name",
    [
        "usp_standardize_patient_contact",
        "usp_update_shipment_status",
        "usp_rebuild_prescriber_rollup",
        "usp_recalc_reorder_points_cursor",
        "usp_export_dispenses_by_region_dynamic",
        "usp_close_month_end",
    ],
)
def test_procedures_with_manual_steps_become_guarded_macros(project, name):
    m = mapping(project, name)
    assert m.as_macro and not m.models
    macro = project.files[f"macros/procedures/{name}.sql"]
    assert f"{{% macro run_{name}() %}}" in macro
    assert "exceptions.raise_compiler_error" in macro
    assert "MANUAL CONVERSION REQUIRED" in macro


def test_merge_model_keeps_update_only_semantics(project):
    spec = mapping(project, "usp_upsert_patient_enrollment").models[0]
    assert spec.merge.unique_key == ["patient_id", "program_code"]
    # Not touched by the T-SQL WHEN MATCHED UPDATE, so dbt must not update them either.
    assert spec.merge.exclude_from_update == ["enrolled_date", "created_at"]
    sql = project.files["models/usp_upsert_patient_enrollment/dim_patient_enrollment.sql"]
    assert "{% if is_incremental() %}" in sql and "WHERE tgt.patient_id IS NULL OR (" in sql


def test_post_hook_keeps_later_statements_in_order(project):
    sql = project.files["models/usp_build_monthly_inventory_snapshot/inventory_snapshots.sql"]
    assert 'post_hook=[\n    "WITH trend AS (' in sql
    assert "MERGE INTO {{ this }} AS s" in sql


def test_sources_and_schema_tests_come_from_the_ddl(project):
    sources = yaml.safe_load(project.files["models/_sources.yml"])["sources"]
    tables = {t["name"]: t for t in sources[0]["tables"]}
    assert "dispenses" in tables and "daily_dispense_summary" not in tables
    assert tables["dispenses"]["columns"] == [
        {"name": "dispense_id", "data_tests": ["not_null", "unique"]}
    ]
    models = yaml.safe_load(
        project.files["models/usp_calc_refill_gaps/_usp_calc_refill_gaps__models.yml"]
    )["models"]
    gap_days = next(c for c in models[0]["columns"] if c["name"] == "gap_days")
    assert gap_days["data_tests"] == ["not_null"]
    assert "tests/assert_refill_gap_events_unique_key.sql" in project.files


def test_project_files_present(project):
    for path in ("dbt_project.yml", "README.md", "macros/sql_literal.sql", "models/_sources.yml"):
        assert path in project.files
    assert yaml.safe_load(project.files["dbt_project.yml"])["models"]["sproc_to_lakehouse"] == {
        "+file_format": "delta"
    }


@pytest.mark.parametrize("incremental", [False, True])
def test_every_model_renders_with_jinja(project, incremental):
    refs = {s.name: s.name for m in project.mappings for s in m.models}
    variables = {name: "2024-01-31" for m in project.mappings for name in m.plan.env.params}
    variables.update(load_batch_id="7", gap_threshold_days="7", as_of_date="2024-01-31")
    for name, template in model_files(project).items():
        rendered = render_model(
            template, this=f"dbo.{name}", variables=variables, refs=refs, incremental=incremental
        )
        assert "{{" not in rendered.sql and "{%" not in rendered.sql, name
        assert "materialized" in rendered.config


def test_replace_where_predicate_is_built_at_parse_time(project):
    template = model_files(project)["daily_dispense_summary"]
    rendered = render_model(
        template, this="dbo.x", variables={"run_date": "2024-01-31"}, refs={}, incremental=True
    )
    (predicate,) = rendered.config["incremental_predicates"]
    assert predicate.startswith("summary_date = (COALESCE(CAST('2024-01-31' AS DATE)")


def test_as_jinja_string_concatenates_expressions():
    assert as_jinja_string("a = {{ f(x) }} AND b") == '"a = " ~ (f(x)) ~ " AND b"'
