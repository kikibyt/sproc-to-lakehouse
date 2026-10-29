"""Markdown rendering of the report data, readable on GitHub without any tooling."""

from __future__ import annotations

from typing import Any

VERDICT_LABEL = {
    "auto": "Converts automatically",
    "auto_with_note": "Converts automatically, with review notes",
    "needs_review": "Needs human review",
}
SEVERITY_LABEL = {"auto": "auto", "auto_with_note": "note", "needs_review": "review"}


def _cell(text: Any) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return lines


def procedure_markdown(report: dict[str, Any]) -> str:
    counts = report["counts"]
    out = [
        f"# {report['procedure']}",
        "",
        f"**{VERDICT_LABEL[report['verdict']]}.** "
        + (
            "Fully converted: the generated code runs without manual steps."
            if report["conversion"] == "full"
            else f"Partly converted: {report['manual_steps']} step(s) need manual conversion."
        ),
        "",
        f"- Source: `{report['source_file']}`",
        f"- Findings: {counts['auto']} auto, {counts['auto_with_note']} with notes, "
        f"{counts['needs_review']} needing review",
    ]
    if report["parameters"]:
        params = ", ".join(
            f"`{p['name']} {p['type']}`" + (f" = `{p['default']}`" if p["default"] else "")
            for p in report["parameters"]
        )
        out.append(f"- Parameters: {params}")
    if not report["ddl_loaded"]:
        out.append("- No DDL was loaded, so type-dependent checks (EX015, EX016) were skipped.")
    if report["outputs"]:
        out.append("- Generated: " + ", ".join(f"`{o}`" for o in report["outputs"]))

    review = [f for f in report["findings"] if f["severity"] == "needs_review"]
    if review:
        out += ["", "## Needs human review", ""]
        out += _table(
            ["Line", "Rule", "Why", "T-SQL"],
            [[f["line"], f["rule_id"], f["message"], f"`{f['snippet']}`"] for f in review],
        )

    notes = [f for f in report["findings"] if f["severity"] == "auto_with_note"]
    if notes:
        out += ["", "## Converted, with review notes", ""]
        out += _table(
            ["Line", "Rule", "Note", "Fix applied"],
            [[f["line"], f["rule_id"], f["message"], f["rewrite"] or "-"] for f in notes],
        )

    auto = [f for f in report["findings"] if f["severity"] == "auto"]
    if auto:
        grouped: dict[str, dict[str, Any]] = {}
        for f in auto:
            entry = grouped.setdefault(
                f["rule_id"], {"construct": f["construct"], "lines": [], "message": f["message"]}
            )
            entry["lines"].append(str(f["line"]))
        out += ["", "## Converted automatically", ""]
        out += _table(
            ["Rule", "Construct", "Lines", "How"],
            [
                [rid, g["construct"], ", ".join(g["lines"]), g["message"]]
                for rid, g in sorted(grouped.items())
            ],
        )

    out += ["", "## Conversion plan", ""]
    out += _table(
        ["#", "Step", "T-SQL line", "Kind", "Fixes applied"],
        [
            [s["number"], s["title"], s["line"], s["kind"], ", ".join(s["rewrites"]) or "-"]
            for s in report["steps"]
        ],
    )
    for step in report["steps"]:
        if step["kind"] != "manual":
            continue
        out += ["", f"### Step {step['number']}: manual conversion (T-SQL line {step['line']})", ""]
        out += [f"- {reason}" for reason in step["reasons"]]
        out += ["", "```sql", step["source_sql"].rstrip(), "```"]

    if report["dropped"]:
        out += ["", "## Dropped", "", "Statements with no Databricks equivalent needed:", ""]
        out += [f"- line {d['line']}: {d['reason']}" for d in report["dropped"]]
    return "\n".join(out) + "\n"


def summary_markdown(summary: dict[str, Any]) -> str:
    t = summary["totals"]
    v = t["verdicts"]
    out = [
        "# Conversion summary",
        "",
        f"{t['procedures']} procedures: **{t['fully_converted']} fully converted**, "
        f"{t['partially_converted']} partly converted ({t['manual_steps']} manual steps in total).",
        "",
        f"Verdicts: {v['auto']} auto, {v['auto_with_note']} auto with notes, "
        f"{v['needs_review']} needing review. Target: {summary['target']}. "
        f"Tool {summary['tool']['sproc_to_lakehouse']}, sqlglot {summary['tool']['sqlglot']}.",
    ]
    if not summary["ddl_loaded"]:
        out += ["", "No DDL was loaded: type-dependent checks (EX015, EX016) were skipped."]

    out += ["", "## Procedures", ""]
    out += _table(
        ["Procedure", "Verdict", "Conversion", "Auto", "Notes", "Review", "Blocking rules"],
        [
            [
                f"[{p['name']}]({p['name']}.md)",
                SEVERITY_LABEL[p["verdict"]],
                p["conversion"] + (f" ({p['manual_steps']} manual)" if p["manual_steps"] else ""),
                p["counts"]["auto"],
                p["counts"]["auto_with_note"],
                p["counts"]["needs_review"],
                ", ".join(p["blocking_rules"]) or "-",
            ]
            for p in summary["procedures"]
        ],
    )
    out += ["", "## Findings by rule", ""]
    out += _table(
        ["Rule", "Construct", "Findings", "Procedures", "Severity"],
        [
            [
                r["rule_id"],
                r["summary"],
                r["findings"],
                r["procedures"],
                ", ".join(SEVERITY_LABEL[s] for s in r["severities"]),
            ]
            for r in summary["rules"]
        ],
    )
    out += ["", "## Fixes applied by the generator", ""]
    out += _table(
        ["Fix", "What it does", "Procedures"],
        [
            [r["name"], r["description"], ", ".join(r["procedures"]) or "-"]
            for r in summary["rewrites"]
        ],
    )
    return "\n".join(out) + "\n"
