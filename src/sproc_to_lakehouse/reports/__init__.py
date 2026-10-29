"""Per-procedure and summary conversion reports (Markdown and JSON)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sproc_to_lakehouse.reports.data import procedure_report, summary_report
from sproc_to_lakehouse.reports.markdown import procedure_markdown, summary_markdown

__all__ = ["procedure_report", "summary_report", "write_reports"]


def _dump(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def write_reports(
    reports: list[dict[str, Any]], summary: dict[str, Any], out_dir: Path
) -> list[Path]:
    written = []
    for report in reports:
        written.append(_dump(out_dir / f"{report['name']}.md", procedure_markdown(report)))
        written.append(
            _dump(out_dir / f"{report['name']}.json", json.dumps(report, indent=2) + "\n")
        )
    written.append(_dump(out_dir / "summary.md", summary_markdown(summary)))
    written.append(_dump(out_dir / "summary.json", json.dumps(summary, indent=2) + "\n"))
    return written
