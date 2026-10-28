"""Each analyzer rule against small, focused T-SQL snippets."""

from __future__ import annotations

import pytest

from sproc_to_lakehouse.analyzer import ALL_RULES, analyze
from sproc_to_lakehouse.analyzer.rewrites import REWRITES
from sproc_to_lakehouse.catalog import Catalog
from sproc_to_lakehouse.models import Severity

from .conftest import SAMPLES_DIR

AUTO, NOTE, REVIEW = Severity.AUTO, Severity.AUTO_WITH_NOTE, Severity.NEEDS_REVIEW
PARAMS = "@p INT, @d DATE, @out INT OUTPUT"

# rule_id -> (procedure body, expected severity). At least one positive example per rule.
RULE_EXAMPLES: list[tuple[str, str, Severity]] = [
    ("CF001", "DECLARE c CURSOR FOR SELECT a FROM dbo.t", REVIEW),
    ("CF002", "WHILE @p > 0 SET @p = @p - 1", REVIEW),
    ("CF003", "BEGIN TRY SELECT 1 END TRY BEGIN CATCH SELECT 2 END CATCH", REVIEW),
    ("CF004", "BEGIN TRAN UPDATE dbo.t SET a = 1 COMMIT", REVIEW),
    ("CF005", "EXEC (N'SELECT 1')", REVIEW),
    ("CF006", "EXEC dbo.usp_other @p = 1", REVIEW),
    ("CF007", "RAISERROR('bad', 16, 1)", REVIEW),
    ("CF008", "IF @p = 1 SELECT 1", REVIEW),
    ("CF008", "IF @d IS NULL SET @d = GETDATE()", AUTO),
    ("CF008", "IF EXISTS (SELECT 1 FROM dbo.t) SELECT 1", REVIEW),
    ("CF009", "RETURN 1", REVIEW),
    ("CF010", "PRINT 'step 1 done'", NOTE),
    ("ST001", "SELECT a INTO #t FROM dbo.s\nINSERT INTO dbo.x (a) SELECT a FROM #t", AUTO),
    (
        "ST002",
        "DECLARE @t TABLE (a INT)\nINSERT INTO @t SELECT a FROM dbo.s\nSELECT a FROM @t",
        AUTO,
    ),
    ("ST003", "SELECT @p = COUNT(*) FROM dbo.t", NOTE),
    ("ST003", "SET @p = (SELECT MAX(a) FROM dbo.t)", NOTE),
    ("ST004", "SELECT 1", AUTO),
    ("ST005", "UPDATE dbo.t SET a = 1\nSET @p = @@ROWCOUNT", REVIEW),
    ("ST006", "SET NOCOUNT ON", AUTO),
    ("ST006", "SET XACT_ABORT ON", NOTE),
    ("ST007", "INSERT INTO dbo.t (a) VALUES (1)\nSET @p = SCOPE_IDENTITY()", REVIEW),
    ("ST008", "IF OBJECT_ID('tempdb..#t') IS NOT NULL DROP TABLE #t", AUTO),
    ("DM001", "MERGE dbo.t AS t USING dbo.s AS s ON t.id = s.id WHEN MATCHED THEN DELETE;", NOTE),
    ("DM002", "UPDATE t SET t.a = s.a FROM dbo.t AS t JOIN dbo.s AS s ON s.id = t.id", NOTE),
    ("DM003", "DELETE TOP (10) FROM dbo.t WHERE a = 1", REVIEW),
    ("DM004", "UPDATE dbo.t SET a = 1 OUTPUT inserted.a WHERE a = 0", REVIEW),
    ("DM005", "SELECT a FROM dbo.t AS t PIVOT", REVIEW),  # malformed: sqlglot rejects it
    ("DM006", "TRUNCATE TABLE dbo.t", AUTO),
    ("DM007", "SELECT a FROM dbo.t", NOTE),
    ("EX001", "WITH c AS (SELECT 1 AS a) SELECT a FROM c", AUTO),
    ("EX002", "SELECT ROW_NUMBER() OVER (ORDER BY a) FROM dbo.t", AUTO),
    ("EX003", "SELECT IIF(a > 1, 'y', 'n') FROM dbo.t", AUTO),
    ("EX004", "SELECT EOMONTH(@d), DATEADD(DAY, 1, @d)", AUTO),
    ("EX005", "SELECT DATEDIFF(MONTH, @d, GETDATE())", NOTE),
    ("EX006", "SELECT DATEPART(ISO_WEEK, @d)", NOTE),
    ("EX006", "SELECT DATEPART(WEEKDAY, @d)", NOTE),
    ("EX007", "SELECT SYSDATETIME()", NOTE),
    ("EX008", "SELECT CONVERT(VARCHAR(10), @d, 103)", NOTE),
    ("EX009", "SELECT ISNULL(a, 0) FROM dbo.t", NOTE),
    ("EX010", "SELECT 'id-' + CAST(a AS VARCHAR(10)) FROM dbo.t", NOTE),
    ("EX011", "SELECT UPPER(LTRIM(a)) FROM dbo.t", AUTO),
    ("EX012", "SELECT LEN(a) FROM dbo.t", NOTE),
    ("EX013", "SELECT STUFF(a, 1, 2, 'x') FROM dbo.t", REVIEW),
    ("EX014", "SELECT STRING_AGG(a, ',') FROM dbo.t", NOTE),
    # EX015/EX016 need column types; they run against the sample DDL (see findings_for).
    ("EX015", "SELECT product_id FROM dbo.products WHERE is_cold_chain = 1", NOTE),
    ("EX015", "UPDATE dbo.dispenses SET is_period_locked = 1", NOTE),
    ("EX016", "SELECT 1 FROM dbo.dispenses WHERE dispense_date > '20240101'", NOTE),
]


CATALOG = Catalog.from_files([SAMPLES_DIR / "ddl" / "00_schema.sql"])


def findings_for(proc, body: str, rule_id: str):
    analysis = analyze(proc(body, PARAMS), CATALOG)
    return [f for f in analysis.findings if f.rule_id == rule_id]


@pytest.mark.parametrize(("rule_id", "body", "severity"), RULE_EXAMPLES)
def test_rule_examples(proc, rule_id, body, severity):
    found = findings_for(proc, body, rule_id)
    assert found, f"{rule_id} did not fire"
    assert {f.severity for f in found} == {severity}


def test_every_rule_has_an_example():
    assert {r.rule_id for r in ALL_RULES} == {rule_id for rule_id, _, _ in RULE_EXAMPLES}


def test_rule_ids_are_unique_and_well_formed():
    ids = [r.rule_id for r in ALL_RULES]
    assert len(ids) == len(set(ids))
    assert all(len(i) == 5 and i[:2] in {"CF", "ST", "DM", "EX"} and i[2:].isdigit() for i in ids)


def test_findings_only_reference_known_rewrites(proc):
    bodies = "\n".join(body for _, body, _ in RULE_EXAMPLES if "MERGE" not in body)
    analysis = analyze(
        proc(bodies + "\nMERGE dbo.t USING dbo.s ON 1 = 1 WHEN MATCHED THEN DELETE;", PARAMS)
    )
    rewrites = {f.rewrite for f in analysis.findings if f.rewrite}
    assert rewrites and rewrites <= set(REWRITES)


# --- temp tables / table variables: the four lifecycle outcomes ----------------------


def test_temp_table_read_twice_becomes_shared_intermediate(proc):
    body = """
    SELECT a INTO #t FROM dbo.s
    INSERT INTO dbo.x (a) SELECT a FROM #t
    INSERT INTO dbo.y (a) SELECT a FROM #t
    """
    (f,) = findings_for(proc, body, "ST001")
    assert f.severity is NOTE and "read by 2 statements" in f.message


def test_temp_table_filled_by_several_inserts_is_unioned(proc):
    body = """
    CREATE TABLE #t (a INT)
    INSERT INTO #t (a) SELECT a FROM dbo.s
    INSERT INTO #t (a) SELECT a FROM dbo.u
    SELECT a FROM #t
    """
    (f,) = findings_for(proc, body, "ST001")
    assert f.severity is NOTE and "UNION ALL" in f.message


def test_temp_table_mutated_after_population_needs_review(proc):
    body = """
    SELECT a INTO #t FROM dbo.s
    UPDATE #t SET a = 0 WHERE a IS NULL
    SELECT a FROM #t
    """
    (f,) = findings_for(proc, body, "ST001")
    assert f.severity is REVIEW and "modified after it is populated" in f.message


def test_temp_table_read_between_inserts_needs_review(proc):
    body = """
    INSERT INTO #t (a) SELECT a FROM dbo.s
    INSERT INTO dbo.x (a) SELECT a FROM #t
    INSERT INTO #t (a) SELECT a FROM dbo.u
    """
    (f,) = findings_for(proc, body, "ST001")
    assert f.severity is REVIEW and "before its last INSERT" in f.message


def test_temp_table_written_in_loop_needs_review(proc):
    body = "WHILE @p > 0 BEGIN INSERT INTO #t (a) VALUES (@p) SET @p = @p - 1 END\nSELECT a FROM #t"
    (f,) = findings_for(proc, body, "ST001")
    assert f.severity is REVIEW and "inside a loop" in f.message


# --- negatives and precision -----------------------------------------------------------


def test_temp_cleanup_if_is_not_an_if_branch_finding(proc):
    body = "IF OBJECT_ID('tempdb..#t') IS NOT NULL DROP TABLE #t"
    assert findings_for(proc, body, "CF008") == []
    assert findings_for(proc, body, "EX013") == []  # OBJECT_ID inside removed cleanup


def test_numeric_addition_is_not_string_concat(proc):
    assert findings_for(proc, "SELECT a + b + 1 FROM dbo.t", "EX010") == []


def test_nested_string_concat_chain_is_detected(proc):
    body = "SELECT UPPER(a) + ' ' + (b + c) FROM dbo.t"
    assert len(findings_for(proc, body, "EX010")) == 1


def test_national_string_literal_concat_is_detected(proc):
    assert findings_for(proc, "SET @p = N'x' + @p", "EX010")


@pytest.mark.parametrize(
    ("body", "expected_fragment", "rewrite"),
    [
        (
            "SELECT TRY_CONVERT(DATE, a, 101) FROM dbo.t",
            "TRY_CONVERT with a style",
            "try_convert_safe_parse",
        ),
        ("SELECT CONVERT(DATE, a, 101) FROM dbo.t", "style code is mapped", None),
        ("SELECT CONVERT(DATE, a) FROM dbo.t", "becomes TRY_CAST", None),
    ],
)
def test_convert_variants(proc, body, expected_fragment, rewrite):
    (f,) = findings_for(proc, body, "EX008")
    assert expected_fragment in f.message
    assert f.rewrite == rewrite


def test_try_convert_without_style_is_silent(proc):
    assert findings_for(proc, "SELECT TRY_CONVERT(INT, a) FROM dbo.t", "EX008") == []


def test_update_without_join_is_not_rewritten(proc):
    assert findings_for(proc, "UPDATE dbo.t SET a = 1 WHERE b = 2", "DM002") == []


def test_merge_not_matched_by_source_mentions_runtime(proc):
    body = (
        "MERGE dbo.t AS t USING dbo.s AS s ON t.id = s.id WHEN NOT MATCHED BY SOURCE THEN DELETE;"
    )
    (f,) = findings_for(proc, body, "DM001")
    assert "12.2" in f.message


def test_delete_top_is_not_double_reported_as_unparsed(proc):
    assert findings_for(proc, "DELETE TOP (10) FROM dbo.t", "DM005") == []


def test_output_parameter_is_reported(proc):
    (f,) = findings_for(proc, "SELECT 1", "CF009")
    assert "@out" in f.message


def test_error_functions_are_left_to_try_catch_review(proc):
    body = "BEGIN TRY SELECT 1 END TRY BEGIN CATCH SELECT ERROR_MESSAGE() END CATCH"
    assert findings_for(proc, body, "EX013") == []


def test_unknown_function_is_reported_by_name(proc):
    (f,) = findings_for(proc, "SELECT GETUTCDATE(), PATINDEX('%x%', a) FROM dbo.t", "EX013")
    assert "GETUTCDATE()" in f.message and "PATINDEX()" in f.message


def test_verdict_is_worst_severity(proc):
    assert analyze(proc("SET NOCOUNT ON")).verdict is AUTO
    assert analyze(proc("SELECT ISNULL(a, 0) INTO #x FROM dbo.t")).verdict is NOTE
    assert analyze(proc("DECLARE c CURSOR FOR SELECT 1")).verdict is REVIEW


def test_integer_string_functions_do_not_make_plus_a_concat(proc):
    # CHARINDEX and LEN return integers: this '+' is arithmetic, not concatenation.
    body = "SELECT SUBSTRING(e, CHARINDEX('@', e) + 1, LEN(e) - 1) FROM dbo.t"
    assert findings_for(proc, body, "EX010") == []


def test_type_rules_need_the_catalog(proc):
    body = "SELECT product_id FROM dbo.products WHERE is_cold_chain = 1"
    assert not [f for f in analyze(proc(body)).findings if f.rule_id == "EX015"]


def test_bit_rule_ignores_non_bit_columns_and_string_codes(proc):
    assert findings_for(proc, "SELECT 1 FROM dbo.products WHERE lead_time_days = 1", "EX015") == []
    assert (
        findings_for(proc, "SELECT 1 FROM dbo.patients WHERE patient_id = '20240101'", "EX016")
        == []
    )


def test_bit_rule_sees_through_table_variables(proc):
    body = """
    DECLARE @late TABLE (shipment_id BIGINT, cold BIT)
    INSERT INTO @late (shipment_id, cold) SELECT shipment_id, requires_cold_chain FROM dbo.shipments
    SELECT COUNT(*) FROM @late AS l WHERE l.cold = 1
    """
    assert findings_for(proc, body, "EX015")
