"""Committed showcase files must match what the current code generates."""

from __future__ import annotations

import io

from sproc_to_lakehouse.cli import main
from sproc_to_lakehouse.reports.markdown import rules_markdown

from .conftest import REPO_ROOT

REGENERATE = "python -m sproc_to_lakehouse convert samples/tsql --target both --out examples"


def test_examples_match_the_generator(tmp_path, monkeypatch):
    monkeypatch.chdir(REPO_ROOT)  # reports record source paths relative to the repo
    assert (
        main(
            ["convert", "samples/tsql", "--target", "both", "--out", str(tmp_path)],
            out=io.StringIO(),
        )
        == 0
    )
    generated = {p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()}
    committed_dir = REPO_ROOT / "examples"
    committed = {
        p.relative_to(committed_dir).as_posix()
        for p in committed_dir.rglob("*")
        if p.is_file() and p != committed_dir / "README.md"
    }
    assert generated == committed, f"examples/ is stale; run: {REGENERATE}"
    stale = [
        path
        for path in sorted(generated)
        if (tmp_path / path).read_text(encoding="utf-8")
        != (committed_dir / path).read_text(encoding="utf-8")
    ]
    assert stale == [], f"examples/ is stale; run: {REGENERATE}"


def test_rule_catalogue_is_current():
    committed = (REPO_ROOT / "docs" / "rules.md").read_text(encoding="utf-8")
    assert committed == rules_markdown(), (
        "docs/rules.md is stale; run: python -m sproc_to_lakehouse rules"
    )


def test_readme_before_after_is_real_output():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    after = readme.split("Databricks notebook (generated", 1)[1].split("```python\n", 1)[1]
    after = after.split("```", 1)[0]
    notebook = REPO_ROOT / "examples" / "notebooks" / "usp_build_monthly_inventory_snapshot.py"
    assert after.strip() in notebook.read_text(encoding="utf-8")
