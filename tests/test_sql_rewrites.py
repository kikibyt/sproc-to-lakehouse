"""The generator rewrites that fix plain sqlglot transpilation, one by one."""

from __future__ import annotations

import pytest
import sqlglot

from sproc_to_lakehouse.codegen.sql_rewrites import (
    RewriteError,
    iso_week_extract,
    plus_to_concat,
    try_convert_safe_parse,
    update_from_to_merge,
)
from sproc_to_lakehouse.sqltypes import TypeResolver
from sproc_to_lakehouse.typed_rewrites import bit_to_boolean, compact_date_literal


def dbx(rewrite, tsql: str, *args) -> str:
    return rewrite(sqlglot.parse_one(tsql, read="tsql"), *args).sql(dialect="databricks")


def test_plus_to_concat_flattens_the_whole_chain():
    out = dbx(plus_to_concat, "SELECT UPPER(a) + ' ' + (b + 'x') FROM t")
    assert out == "SELECT CONCAT(UPPER(a), ' ', b, 'x') FROM t"


@pytest.mark.parametrize(
    "tsql",
    ["SELECT a + b + 1 FROM t", "SELECT CHARINDEX('@', e) + 1, LEN(e) - 1 FROM t"],
)
def test_plus_to_concat_leaves_arithmetic_alone(tsql):
    assert "CONCAT" not in dbx(plus_to_concat, tsql)


def test_try_convert_becomes_try_to_timestamp_with_databricks_pattern():
    out = dbx(try_convert_safe_parse, "SELECT TRY_CONVERT(DATE, x, 101), CONVERT(DATE, x, 101)")
    assert out == "SELECT CAST(TRY_TO_TIMESTAMP(x, 'M/d/yyyy') AS DATE), TO_DATE(x, 'M/d/yyyy')"


def test_iso_week_uses_the_databricks_field():
    assert dbx(iso_week_extract, "SELECT DATEPART(ISO_WEEK, d)") == "SELECT EXTRACT(WEEK FROM d)"


def test_update_join_with_cte_becomes_merge_with_cte_in_front():
    out = dbx(
        update_from_to_merge,
        """WITH latest AS (SELECT id, ts FROM dbo.events)
        UPDATE s SET s.delivered_at = l.ts FROM dbo.shipments AS s
        JOIN latest AS l ON l.id = s.id WHERE l.ts > 0 AND s.status <> 'X'""",
    )
    assert out == (
        "WITH latest AS (SELECT id, ts FROM dbo.events) "
        "MERGE INTO dbo.shipments AS s USING (SELECT * FROM latest AS l WHERE l.ts > 0) AS l "
        "ON l.id = s.id WHEN MATCHED AND s.status <> 'X' THEN UPDATE SET s.delivered_at = l.ts"
    )


def test_update_join_not_using_source_columns_dedupes_the_source():
    # Several exception events per shipment must not make the MERGE fail.
    out = dbx(
        update_from_to_merge,
        "UPDATE s SET s.status = 'EXCEPTION' FROM dbo.shipments AS s "
        "JOIN dbo.events AS e ON e.shipment_id = s.shipment_id WHERE e.code = 'EX'",
    )
    assert (
        "USING (SELECT DISTINCT e.shipment_id FROM dbo.events AS e WHERE e.code = 'EX') AS e" in out
    )


def test_delete_join_becomes_merge_delete():
    out = dbx(update_from_to_merge, "DELETE d FROM dbo.d AS d JOIN dbo.x AS x ON x.id = d.id")
    assert out.endswith("WHEN MATCHED THEN DELETE")


@pytest.mark.parametrize(
    "tsql",
    [
        "UPDATE s SET s.a = 1 FROM dbo.s AS s LEFT JOIN dbo.x AS x ON x.id = s.id",
        "UPDATE s SET s.a = 1 FROM dbo.s AS s "
        "JOIN dbo.x AS x ON x.id = s.id JOIN dbo.y AS y ON 1 = 1",
    ],
)
def test_update_shapes_that_would_change_semantics_are_refused(tsql):
    with pytest.raises(RewriteError):
        update_from_to_merge(sqlglot.parse_one(tsql, read="tsql"))


@pytest.fixture(scope="module")
def resolver(catalog):
    return TypeResolver(catalog)


@pytest.mark.parametrize(
    ("tsql", "expected"),
    [
        (
            "SELECT 1 FROM dbo.products AS p WHERE p.is_cold_chain = 1 AND p.lead_time_days = 1",
            "SELECT 1 FROM dbo.products AS p WHERE p.is_cold_chain = TRUE AND p.lead_time_days = 1",
        ),
        (
            "UPDATE dbo.dispenses SET is_period_locked = 1",
            "UPDATE dbo.dispenses SET is_period_locked = TRUE",
        ),
        (
            "INSERT INTO dbo.claim_classification (claim_id, high_oop_flag) "
            "SELECT 1, IIF(a > 1, 1, 0)",
            "INSERT INTO dbo.claim_classification (claim_id, high_oop_flag) "
            "SELECT 1, CAST(IF(a > 1, 1, 0) AS BOOLEAN)",
        ),
    ],
)
def test_bit_to_boolean(resolver, tsql, expected):
    assert dbx(bit_to_boolean, tsql, resolver) == expected


def test_compact_date_literal_only_touches_date_comparisons(resolver):
    out = dbx(
        compact_date_literal,
        "SELECT 1 FROM dbo.dim_patient_enrollment AS t "
        "WHERE ISNULL(t.disenrolled_date, '19000101') <> '19000101' AND t.patient_id = '20240101'",
        resolver,
    )
    assert out == (
        "SELECT 1 FROM dbo.dim_patient_enrollment AS t WHERE "
        "COALESCE(t.disenrolled_date, CAST('1900-01-01' AS DATE)) <> CAST('1900-01-01' AS DATE) "
        "AND t.patient_id = '20240101'"
    )
