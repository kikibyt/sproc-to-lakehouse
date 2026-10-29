"""Report content as plain data (dicts and lists), shared by the JSON and Markdown writers.

Reports contain no timestamps, so converting the same input twice gives identical
files and report changes show up cleanly in version control.
"""

from __future__ import annotations

from collections import Counter
from pathlib import PurePath
from typing import Any

import sqlglot

import sproc_to_lakehouse
from sproc_to_lakehouse.analyzer import ALL_RULES
from sproc_to_lakehouse.analyzer.rewrites import REWRITES
from sproc_to_lakehouse.codegen.plan import Plan, StepKind
from sproc_to_lakehouse.models import Severity

SEVERITIES = [s.value for s in Severity]
_RULES = {r.rule_id: r for r in ALL_RULES}


def procedure_report(plan: Plan, outputs: list[str], ddl_loaded: bool) -> dict[str, Any]:
    """Everything known about one procedure's conversion."""
    analysis = plan.analysis
    proc = analysis.procedure
    findings = [
        {
            "rule_id": f.rule_id,
            "construct": f.construct,
            "severity": f.severity.value,
            "line": f.line,
            "message": f.message,
            "snippet": f.snippet,
            "rewrite": f.rewrite,
        }
        for f in analysis.findings
    ]
    steps = [
        {
            "number": i,
            "kind": s.kind.value,
            "title": s.title,
            "line": s.line,
            "rewrites": sorted(s.rewrites),
            "reasons": s.reasons,
            "source_sql": s.source_sql if s.kind is StepKind.MANUAL else None,
        }
        for i, s in enumerate(plan.steps, start=1)
    ]
    return {
        "procedure": proc.qualified_name,
        "name": proc.name,
        "source_file": PurePath(proc.source_path).as_posix() if proc.source_path else None,
        "parameters": [
            {"name": p.name, "type": p.data_type, "default": p.default, "output": p.is_output}
            for p in proc.parameters
        ],
        "verdict": analysis.verdict.value,
        "conversion": "full" if plan.is_fully_converted else "partial",
        "counts": {s.value: len(analysis.by_severity(s)) for s in Severity},
        "manual_steps": len(plan.manual_steps),
        "ddl_loaded": ddl_loaded,
        "rewrites_applied": sorted(plan.rewrites_used()),
        "findings": findings,
        "steps": steps,
        "dropped": [{"line": d.line, "reason": d.reason} for d in plan.dropped],
        "outputs": outputs,
    }


def summary_report(
    reports: list[dict[str, Any]], *, target: str, ddl_loaded: bool
) -> dict[str, Any]:
    verdicts = Counter(r["verdict"] for r in reports)
    rule_counts: Counter[str] = Counter()
    rule_procs: dict[str, set[str]] = {}
    for r in reports:
        for f in r["findings"]:
            rule_counts[f["rule_id"]] += 1
            rule_procs.setdefault(f["rule_id"], set()).add(r["name"])
    rewrite_procs: dict[str, list[str]] = {}
    for r in reports:
        for name in r["rewrites_applied"]:
            rewrite_procs.setdefault(name, []).append(r["name"])
    return {
        "tool": {
            "sproc_to_lakehouse": sproc_to_lakehouse.__version__,
            "sqlglot": sqlglot.__version__,
        },
        "target": target,
        "ddl_loaded": ddl_loaded,
        "totals": {
            "procedures": len(reports),
            "fully_converted": sum(r["conversion"] == "full" for r in reports),
            "partially_converted": sum(r["conversion"] == "partial" for r in reports),
            "manual_steps": sum(r["manual_steps"] for r in reports),
            "findings": sum(len(r["findings"]) for r in reports),
            "verdicts": {s: verdicts.get(s, 0) for s in SEVERITIES},
        },
        "procedures": [
            {
                "name": r["name"],
                "source_file": r["source_file"],
                "verdict": r["verdict"],
                "conversion": r["conversion"],
                "counts": r["counts"],
                "manual_steps": r["manual_steps"],
                "rewrites_applied": r["rewrites_applied"],
                "blocking_rules": sorted(
                    {f["rule_id"] for f in r["findings"] if f["severity"] == "needs_review"}
                ),
            }
            for r in reports
        ],
        "rules": [
            {
                "rule_id": rule_id,
                "construct": _RULES[rule_id].construct,
                "summary": _RULES[rule_id].summary,
                "findings": count,
                "procedures": len(rule_procs[rule_id]),
                "severities": sorted(
                    {
                        f["severity"]
                        for r in reports
                        for f in r["findings"]
                        if f["rule_id"] == rule_id
                    },
                    key=SEVERITIES.index,
                ),
            }
            for rule_id, count in sorted(rule_counts.items())
        ],
        "rewrites": [
            {"name": name, "description": REWRITES[name], "procedures": rewrite_procs.get(name, [])}
            for name in sorted(REWRITES)
        ],
    }
