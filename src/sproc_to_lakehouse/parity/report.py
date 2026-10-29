"""Parity report: Markdown for people, JSON for tools."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from sproc_to_lakehouse.parity.compare import MAX_LISTED, NORMALISATION_NOTES
from sproc_to_lakehouse.parity.runner import ScenarioResult

STATUS_LABEL = {"match": "MATCH", "mismatch": "MISMATCH", "not_run": "not run", "error": "ERROR"}


def report_data(results: list[ScenarioResult], environment: dict[str, Any]) -> dict[str, Any]:
    return {
        "environment": environment,
        "normalisation": NORMALISATION_NOTES,
        "totals": {
            status: sum(r.status == status for r in results)
            for status in ("match", "mismatch", "not_run", "error")
        },
        "scenarios": [
            {
                "procedure": r.procedure,
                "params": r.scenario.params,
                "status": r.status,
                "reason": r.reason,
                "changes": r.changes,
                "sqlserver_seconds": r.sqlserver_seconds,
                "spark_seconds": r.spark_seconds,
                "comparisons": [
                    {
                        **{k: v for k, v in asdict(c).items() if k != "columns"},
                        "match": c.match,
                        "columns": [{**asdict(col), "match": col.match} for col in c.columns],
                    }
                    for c in r.comparisons
                ],
            }
            for r in results
        ],
    }


def _cell(value: Any) -> str:
    return "NULL" if value is None else str(value).replace("|", "\\|")


def report_markdown(data: dict[str, Any]) -> str:
    env, totals = data["environment"], data["totals"]
    out = [
        "# Parity report",
        "",
        "The original T-SQL procedure runs on SQL Server and the generated code runs on "
        "Spark + Delta Lake, each against the same synthetic data. The tables each "
        "procedure writes (and any result set it returns) are then compared: row counts, "
        "per-column checksums and row-level differences by primary key.",
        "",
        f"- Reference: {env['sqlserver']}",
        f"- Candidate: {env['spark']}",
        f"- Data: {env['data_rows']} synthetic rows across {env['data_tables']} tables "
        f"(seed {env['seed']})",
        *(
            []
            if env.get("fixes", True)
            else [
                "- **Ablation run: the tool's targeted fixes were switched off** (plain sqlglot "
                "output inside the same pipeline)."
            ]
        ),
        f"- Result: **{totals['match']} match**, {totals['mismatch']} mismatch, "
        f"{totals['error']} error, {totals['not_run']} not run",
        "",
        "| Procedure | Status | Compared | Rows (SQL Server / Spark) | Columns matching "
        "| Changed by the procedure | Note |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in data["scenarios"]:
        comps = s["comparisons"]
        compared = ", ".join(c["name"] for c in comps) or "-"
        rows = ", ".join(f"{c['sqlserver_rows']} / {c['spark_rows']}" for c in comps) or "-"
        cols = (
            ", ".join(f"{sum(x['match'] for x in c['columns'])}/{len(c['columns'])}" for c in comps)
            or "-"
        )
        changed = (
            "; ".join(
                f"+{c['inserted']} ~{c['updated']} -{c['deleted']}" for c in s["changes"].values()
            )
            or "-"
        )
        out.append(
            f"| {s['procedure']} | {STATUS_LABEL[s['status']]} | {compared} | {rows} | {cols} | "
            f"{changed} | {_cell(s['reason'] or '')} |"
        )
    out += [
        "",
        "Changed by the procedure: rows inserted (+), updated (~) and deleted (-) on SQL Server "
        "compared with the data loaded before the run. A match on a table the procedure changed "
        "is real evidence; a match on an unchanged table would prove nothing.",
        "",
        "Normalisation applied before comparing:",
        "",
    ]
    out += [f"- {note}" for note in data["normalisation"]]

    for s in data["scenarios"]:
        if not s["comparisons"]:
            continue
        params = ", ".join(f"{k}={v}" for k, v in s["params"].items()) or "none"
        out += [
            "",
            f"## {s['procedure']}: {STATUS_LABEL[s['status']]}",
            "",
            f"Parameters: {params}.",
        ]
        for c in s["comparisons"]:
            out += ["", f"### {c['name']}", ""]
            key = ", ".join(c["key"]) or "full row (no key)"
            out.append(
                f"Rows: {c['sqlserver_rows']} on SQL Server, {c['spark_rows']} on Spark. "
                f"Matched by: {key}."
                + (
                    f" Ignored (set from the current time): {', '.join(c['ignored'])}."
                    if c["ignored"]
                    else ""
                )
            )
            if c["column_mismatch"]:
                out.append(f"Columns present on one side only: {', '.join(c['column_mismatch'])}.")
            out += [
                "",
                "| Column | SQL Server checksum | Spark checksum | Match |",
                "|---|---|---|---|",
            ]
            out += [
                f"| {col['name']} | `{col['sqlserver']}` | `{col['spark']}` | "
                f"{'yes' if col['match'] else '**no**'} |"
                for col in c["columns"]
            ]
            if c["value_diff_count"]:
                out += [
                    "",
                    f"{c['value_diff_count']} value difference(s) (first {MAX_LISTED} shown):",
                    "",
                ]
                out += ["| Key | Column | SQL Server | Spark |", "|---|---|---|---|"]
                out += [
                    f"| {_cell(d['key'])} | {d['column']} | "
                    f"{_cell(d['sqlserver'])} | {_cell(d['spark'])} |"
                    for d in c["value_diffs"]
                ]
            for side, label in (("only_in_sqlserver", "SQL Server"), ("only_in_spark", "Spark")):
                count = c[f"{side}_count"]
                if count:
                    out += ["", f"{count} row(s) only on {label} (first {MAX_LISTED} shown):", ""]
                    out += [f"- {_cell(row)}" for row in c[side]]
    return "\n".join(out) + "\n"


def write_report(results: list[ScenarioResult], environment: dict[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    data = report_data(results, environment)
    (out_dir / "parity_report.json").write_text(
        json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8"
    )
    path = out_dir / "parity_report.md"
    path.write_text(report_markdown(data), encoding="utf-8")
    return path
