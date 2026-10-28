"""The 12 sample procedures, checked against the hand-written answer key."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlglot
import yaml
from sqlglot import exp

from sproc_to_lakehouse.analyzer import analyze
from sproc_to_lakehouse.catalog import Catalog
from sproc_to_lakehouse.models import Severity, TableKind
from sproc_to_lakehouse.parser import parse_file

from .conftest import SAMPLES_DIR

TSQL_DIR = SAMPLES_DIR / "tsql"
DDL_FILE = SAMPLES_DIR / "ddl" / "00_schema.sql"
CATALOG = Catalog.from_files([SAMPLES_DIR / "ddl" / "00_schema.sql"])
EXPECTED = yaml.safe_load((SAMPLES_DIR / "expected_findings.yml").read_text(encoding="utf-8"))
SAMPLE_FILES = sorted(TSQL_DIR.glob("*.sql"))


def load(path: Path):
    (procedure,) = parse_file(path)
    return procedure


def test_corpus_has_twelve_procedures_all_in_the_answer_key():
    assert len(SAMPLE_FILES) == 12
    assert sorted(entry["file"] for entry in EXPECTED.values()) == [p.name for p in SAMPLE_FILES]


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_findings_match_answer_key(name):
    entry = EXPECTED[name]
    procedure = load(TSQL_DIR / entry["file"])
    assert procedure.name == name

    analysis = analyze(procedure, CATALOG)
    rule_ids = sorted({f.rule_id for f in analysis.findings})
    review_ids = sorted({f.rule_id for f in analysis.by_severity(Severity.NEEDS_REVIEW)})

    assert rule_ids == sorted(entry["rules"])
    assert review_ids == sorted(entry["needs_review"])
    assert analysis.verdict.value == entry["verdict"]


@pytest.mark.parametrize("path", SAMPLE_FILES, ids=lambda p: p.stem)
def test_only_known_sqlglot_gaps_are_unparsed(path):
    """Every SQL statement is parsed by sqlglot except the documented DELETE TOP gap."""
    unparsed = [s for s in load(path).statements() if s.parse_error]
    assert all("TOP (n)" in s.parse_error for s in unparsed), [s.parse_error for s in unparsed]


def _ddl_tables() -> set[str]:
    statements = sqlglot.parse(DDL_FILE.read_text(encoding="utf-8"), read="tsql")
    assert all(isinstance(s, exp.Create) for s in statements)
    return {s.this.this.sql(dialect="tsql").lower() for s in statements}


def test_ddl_parses_and_every_referenced_table_is_defined():
    defined = _ddl_tables()
    referenced: set[str] = set()
    for path in SAMPLE_FILES:
        for stmt in load(path).statements():
            for ref in (*stmt.reads, *stmt.writes):
                if ref.kind is TableKind.PERMANENT:
                    referenced.add(ref.qualified_name)
    assert referenced, "expected the corpus to reference tables"
    assert referenced <= defined, sorted(referenced - defined)


@pytest.mark.parametrize("path", [*SAMPLE_FILES, DDL_FILE], ids=lambda p: p.stem)
def test_samples_are_labelled_synthetic(path):
    header = path.read_text(encoding="utf-8")[:400].lower()
    assert "synthetic" in header
