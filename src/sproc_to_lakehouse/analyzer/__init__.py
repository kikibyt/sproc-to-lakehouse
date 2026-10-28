"""Classify the constructs in parsed procedures as auto-convertible or needing review."""

from __future__ import annotations

from collections.abc import Iterable

from sproc_to_lakehouse.analyzer.registry import Rule
from sproc_to_lakehouse.analyzer.rules import ALL_RULES
from sproc_to_lakehouse.catalog import Catalog
from sproc_to_lakehouse.models import Procedure, ProcedureAnalysis

__all__ = ["ALL_RULES", "Rule", "analyze", "analyze_all"]


def analyze(
    procedure: Procedure, catalog: Catalog | None = None, rules: Iterable[Rule] = ALL_RULES
) -> ProcedureAnalysis:
    """Run every rule. Type-dependent rules (EX015, EX016) need the DDL ``catalog``."""
    findings = [finding for r in rules for finding in r.run(procedure, catalog)]
    findings.sort(key=lambda f: (f.line, f.rule_id))
    return ProcedureAnalysis(procedure=procedure, findings=findings)


def analyze_all(
    procedures: Iterable[Procedure], catalog: Catalog | None = None
) -> list[ProcedureAnalysis]:
    return [analyze(p, catalog) for p in procedures]
