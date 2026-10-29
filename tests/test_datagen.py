"""Synthetic data generation: deterministic, typed, and consistent with the DDL."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from sproc_to_lakehouse.parity.datagen import DataSpecError, generate, load_spec

from .conftest import SAMPLES_DIR


@pytest.fixture(scope="module")
def data(catalog):
    return generate(load_spec(SAMPLES_DIR / "parity" / "data.yml"), catalog)


def test_generation_is_deterministic(data, catalog):
    again = generate(load_spec(SAMPLES_DIR / "parity" / "data.yml"), catalog)
    assert again == data


def test_every_row_has_every_column_with_the_right_python_type(data, catalog):
    kinds = {"INTEGER": int, "BIGINT": int, "DATE": date, "DATETIME2": datetime, "BIT": bool}
    for table, rows in data.items():
        schema = catalog.get(table)
        for row in rows:
            assert set(row) == {c.name for c in schema.columns}
            for column in schema.columns:
                value = row[column.name]
                if value is None:
                    assert column.nullable, f"{table}.{column.name} is NOT NULL"
                    continue
                base = column.data_type.split("(")[0].upper()
                expected = Decimal if base in ("DECIMAL", "NUMERIC") else kinds.get(base, str)
                assert isinstance(value, expected), (table, column.name, value)


def test_primary_keys_are_unique(data, catalog):
    for table, rows in data.items():
        pk = catalog.get(table).primary_key
        keys = [tuple(r[c] for c in pk) for r in rows]
        assert len(keys) == len(set(keys)), table


def test_foreign_keys_point_at_generated_rows(data):
    patients = {r["patient_id"] for r in data["dbo.patients"]}
    assert {r["patient_id"] for r in data["dbo.dispenses"]} <= patients
    claims = {r["claim_id"]: r for r in data["dbo.claims"]}
    for row in data["dbo.claim_classification"]:
        claim = claims[row["claim_id"]]
        assert row["submitted_date"] == claim["submitted_at"].date()  # lookup
        assert row["dispense_id"] == claim["dispense_id"]


def test_cartesian_tables_cover_every_combination(data):
    snapshots = data["dbo.inventory_snapshots"]
    assert len(snapshots) == 3 * 6 * 12
    assert {r["snapshot_month"] for r in snapshots} == {
        date(2023, 12, 31),
        date(2024, 1, 31),
        date(2024, 2, 29),
    }


def test_edge_cases_are_present(data):
    staged = data["dbo.stg_patient_enrollments"]
    assert any(r["disenrolled_on"] in ("13/45/2024", "N/A", "02/30/2024") for r in staged)
    assert any(r["program_code"] and r["program_code"] != r["program_code"].strip() for r in staged)
    assert any(r["reject_code"] is None for r in data["dbo.claims"])
    emails = [r["email_raw"] for r in data["dbo.patients"] if r["email_raw"]]
    assert all("example.test" in e.lower() for e in emails)  # reserved, non-routable domain


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ({"tables": {"dbo.nope": {"rows": 1}}}, "not in the DDL"),
        (
            {"tables": {"dbo.sites": {"rows": 1, "columns": {"bogus": {"const": 1}}}}},
            "unknown columns",
        ),
        (
            {"tables": {"dbo.sites": {"rows": 1, "columns": {"site_id": {"const": None}}}}},
            "NOT NULL",
        ),
        (
            {
                "tables": {
                    "dbo.dispenses": {
                        "rows": 1,
                        "columns": {"patient_id": {"fk": "dbo.patients.patient_id"}},
                    }
                }
            },
            "generated earlier",
        ),
        (
            {"tables": {"dbo.sites": {"rows": 3, "columns": {"site_id": {"const": 1}}}}},
            "unique keys",
        ),
        (
            {
                "tables": {
                    "dbo.sites": {"rows": 1, "columns": {"state_code": {"const": "TOO LONG"}}}
                }
            },
            "longer than",
        ),
    ],
)
def test_spec_errors_are_explained(catalog, spec, message):
    with pytest.raises(DataSpecError, match=message):
        generate(spec, catalog)
