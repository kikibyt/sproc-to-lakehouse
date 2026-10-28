"""Classify the constructs in parsed procedures as auto-convertible or needing review."""

from __future__ import annotations

from collections.abc import Iterable

from sproc_to_lakehouse.analyzer.registry import Rule
from sproc_to_lakehouse.analyzer.rules import ALL_RULES
from sproc_to_lakehouse.models import Procedure, ProcedureAnalysis

__all__ = ["ALL_RULES", "Rule", "analyze", "analyze_all"]


def analyze(procedure: Procedure, rules: Iterable[Rule] = ALL_RULES) -> ProcedureAnalysis:
    findings = [finding for r in rules for finding in r.run(procedure)]
    findings.sort(key=lambda f: (f.line, f.rule_id))
    return ProcedureAnalysis(procedure=procedure, findings=findings)


def analyze_all(procedures: Iterable[Procedure]) -> list[ProcedureAnalysis]:
    return [analyze(p) for p in procedures]
