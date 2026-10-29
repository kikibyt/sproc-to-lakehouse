"""Command line: convert / analyze, output layout, reports and exit codes."""

from __future__ import annotations

import io
import json

import pytest

from sproc_to_lakehouse.cli import EXIT_ERROR, EXIT_OK, EXIT_THRESHOLD, main

from .conftest import SAMPLES_DIR

TSQL = str(SAMPLES_DIR / "tsql")


def run(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    code = main(list(argv), out=out)
    return code, out.getvalue()


@pytest.fixture(scope="module")
def converted(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("out")
    code, text = run("convert", TSQL, "--target", "both", "--out", str(out_dir))
    return code, text, out_dir


def test_convert_writes_code_and_reports(converted):
    code, text, out = converted
    assert code == EXIT_OK
    assert "Using DDL from" in text and "Loaded 22 table definitions" in text
    assert "12 procedures: 6 fully converted, 6 partly converted (9 manual steps)." in text
    assert (out / "dbt" / "dbt_project.yml").is_file()
    assert len(list((out / "notebooks").glob("*.py"))) == 12
    reports = out / "reports"
    assert len(list(reports.glob("usp_*.md"))) == 12 and len(list(reports.glob("usp_*.json"))) == 12
    assert (reports / "summary.md").is_file() and (reports / "summary.json").is_file()


def test_procedure_report_json(converted):
    _, _, out = converted
    report = json.loads((out / "reports" / "usp_upsert_patient_enrollment.json").read_text())
    assert report["verdict"] == "auto_with_note" and report["conversion"] == "full"
    assert report["rewrites_applied"] == [
        "bit_to_boolean",
        "compact_date_literal",
        "try_convert_safe_parse",
    ]
    assert report["outputs"] == [
        "dbt/models/usp_upsert_patient_enrollment/dim_patient_enrollment.sql",
        "notebooks/usp_upsert_patient_enrollment.py",
    ]
    assert report["source_file"].endswith("samples/tsql/03_usp_upsert_patient_enrollment.sql")


def test_partial_procedure_report_lists_manual_steps_with_original_tsql(converted):
    _, _, out = converted
    md = (out / "reports" / "usp_recalc_reorder_points_cursor.md").read_text()
    assert "**Needs human review.** Partly converted: 1 step(s) need manual conversion." in md
    assert "### Step 1: manual conversion (T-SQL line 24)" in md
    assert "DECLARE product_cursor CURSOR LOCAL FAST_FORWARD FOR" in md


def test_summary_counts_match_answer_key(converted):
    _, _, out = converted
    summary = json.loads((out / "reports" / "summary.json").read_text())
    assert summary["totals"]["verdicts"] == {"auto": 0, "auto_with_note": 6, "needs_review": 6}
    rules = {r["rule_id"]: r for r in summary["rules"]}
    assert rules["EX015"]["procedures"] == 7
    rewrites = {r["name"]: r["procedures"] for r in summary["rewrites"]}
    assert rewrites["plus_to_concat"] == []  # only in statements that stay manual


def test_reports_are_reproducible(tmp_path, converted):
    _, _, first = converted
    run("convert", TSQL, "--target", "both", "--out", str(tmp_path))
    for name in ("summary.md", "summary.json", "usp_close_month_end.md"):
        assert (tmp_path / "reports" / name).read_text() == (first / "reports" / name).read_text()


def test_target_dbt_writes_no_notebooks(tmp_path):
    code, _ = run("convert", TSQL, "--target", "dbt", "--out", str(tmp_path))
    assert code == EXIT_OK
    assert not (tmp_path / "notebooks").exists()


def test_no_ddl_is_reported(tmp_path):
    code, text = run("convert", TSQL, "--no-ddl", "--target", "notebook", "--out", str(tmp_path))
    assert code == EXIT_OK and "Using DDL" not in text
    summary = (tmp_path / "reports" / "summary.md").read_text()
    assert "No DDL was loaded" in summary


def test_fail_on_threshold(tmp_path):
    assert run("analyze", TSQL, "--fail-on", "needs_review")[0] == EXIT_THRESHOLD
    single = str(SAMPLES_DIR / "tsql" / "05_usp_classify_payer_claims.sql")
    assert (
        run("analyze", single, "--ddl", str(SAMPLES_DIR / "ddl"), "--fail-on", "needs_review")[0]
        == EXIT_OK
    )
    assert (
        run("analyze", single, "--ddl", str(SAMPLES_DIR / "ddl"), "--fail-on", "auto_with_note")[0]
        == EXIT_THRESHOLD
    )


def test_analyze_json(tmp_path):
    code, text = run("analyze", TSQL, "--format", "json")
    data = json.loads(text[text.index("{") :])
    assert code == EXIT_OK and len(data["procedures"]) == 12


def test_missing_source_is_an_error(tmp_path, capsys):
    code, _ = run("analyze", str(tmp_path / "nope"))
    assert code == EXIT_ERROR
    assert "not found" in capsys.readouterr().err


def test_module_entry_point(tmp_path):
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "sproc_to_lakehouse", "analyze", TSQL, "--no-ddl"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0 and "usp_close_month_end" in result.stdout
