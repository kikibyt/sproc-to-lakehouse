"""All analyzer rules, grouped by area. Rule IDs are stable and appear in reports."""

from __future__ import annotations

from sproc_to_lakehouse.analyzer.registry import Rule
from sproc_to_lakehouse.analyzer.rules import control_flow, dml, expressions, state

ALL_RULES: list[Rule] = [*control_flow.RULES, *state.RULES, *dml.RULES, *expressions.RULES]

_ids = [r.rule_id for r in ALL_RULES]
if len(_ids) != len(set(_ids)):  # pragma: no cover - guards against copy/paste mistakes
    raise RuntimeError(f"duplicate rule ids: {sorted(i for i in _ids if _ids.count(i) > 1)}")

__all__ = ["ALL_RULES"]
