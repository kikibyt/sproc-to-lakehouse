"""Command-line interface.

    python -m sproc_to_lakehouse convert samples/tsql --target dbt --out output
    python -m sproc_to_lakehouse analyze samples/tsql

``convert`` writes the generated code plus Markdown/JSON reports. If ``--ddl`` is not
given, a ``ddl`` folder next to the source folder is used when present (and announced),
because several checks need column types; ``--no-ddl`` turns that off.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from sproc_to_lakehouse.analyzer import analyze
from sproc_to_lakehouse.catalog import Catalog
from sproc_to_lakehouse.codegen.dbt import generate_dbt_project
from sproc_to_lakehouse.codegen.notebook import generate_notebook
from sproc_to_lakehouse.codegen.plan import Plan, build_plan
from sproc_to_lakehouse.models import Severity
from sproc_to_lakehouse.parser import LexError, ProcedureParseError, parse_file
from sproc_to_lakehouse.reports import procedure_report, summary_report, write_reports
from sproc_to_lakehouse.reports.markdown import SEVERITY_LABEL

EXIT_OK, EXIT_ERROR, EXIT_THRESHOLD = 0, 1, 2


class CliError(Exception):
    pass


def _sql_files(paths: Sequence[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(sorted(path.glob("*.sql")))
        elif path.is_file():
            files.append(path)
        else:
            raise CliError(f"not found: {path}")
    if not files:
        raise CliError("no .sql files found in " + ", ".join(str(p) for p in paths))
    return files


def _catalog(args: argparse.Namespace, out) -> Catalog | None:
    if args.no_ddl:
        return None
    ddl_paths: list[Path] = list(args.ddl or [])
    if not ddl_paths:
        first = args.source[0]
        guess = (first if first.is_dir() else first.parent).parent / "ddl"
        if guess.is_dir():
            print(f"Using DDL from {guess} (pass --no-ddl to skip)", file=out)
            ddl_paths = [guess]
    if not ddl_paths:
        return None
    catalog = Catalog.from_files(_sql_files(ddl_paths))
    print(f"Loaded {len(catalog)} table definitions", file=out)
    return catalog


def _plans(files: list[Path], catalog: Catalog | None) -> list[Plan]:
    plans = []
    for path in files:
        try:
            procedures = parse_file(path)
        except (ProcedureParseError, LexError) as exc:
            raise CliError(f"{path}: {exc}") from exc
        plans.extend(build_plan(analyze(p, catalog), catalog) for p in procedures)
    if not plans:
        raise CliError("no CREATE PROCEDURE statements found")
    return plans


def _rel(path: Path, base: Path) -> str:
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.as_posix()


def cmd_convert(args: argparse.Namespace, out) -> int:
    files = _sql_files(args.source)
    catalog = _catalog(args, out)
    plans = _plans(files, catalog)
    out_dir: Path = args.out
    outputs: dict[str, list[str]] = {p.name: [] for p in plans}

    if args.target in ("dbt", "both"):
        project = generate_dbt_project(plans, catalog)
        project.write(out_dir / "dbt")
        for mapping in project.mappings:
            name = mapping.plan.name
            if mapping.as_macro:
                outputs[name].append(f"dbt/macros/procedures/{name}.sql")
            outputs[name] += [f"dbt/models/{name}/{spec.name}.sql" for spec in mapping.models]
    if args.target in ("notebook", "both"):
        nb_dir = out_dir / "notebooks"
        nb_dir.mkdir(parents=True, exist_ok=True)
        for plan in plans:
            path = nb_dir / f"{plan.name}.py"
            path.write_text(generate_notebook(plan), encoding="utf-8", newline="\n")
            outputs[plan.name].append(_rel(path, out_dir))

    reports = [procedure_report(p, outputs[p.name], catalog is not None) for p in plans]
    summary = summary_report(reports, target=args.target, ddl_loaded=catalog is not None)
    write_reports(reports, summary, out_dir / "reports")

    _print_table(reports, out)
    totals = summary["totals"]
    print(
        f"\n{totals['procedures']} procedures: {totals['fully_converted']} fully converted, "
        f"{totals['partially_converted']} partly converted "
        f"({totals['manual_steps']} manual steps).",
        file=out,
    )
    print(f"Wrote {args.target} output and reports to {out_dir}", file=out)
    print(f"Summary: {out_dir / 'reports' / 'summary.md'}", file=out)
    return _threshold_exit(args, [p.analysis.verdict for p in plans])


def cmd_analyze(args: argparse.Namespace, out) -> int:
    files = _sql_files(args.source)
    catalog = _catalog(args, out)
    plans = _plans(files, catalog)
    reports = [procedure_report(p, [], catalog is not None) for p in plans]
    if args.format == "json":
        summary = summary_report(reports, target="none", ddl_loaded=catalog is not None)
        print(json.dumps({"summary": summary, "procedures": reports}, indent=2), file=out)
    else:
        _print_table(reports, out)
        for report in reports:
            review = [f for f in report["findings"] if f["severity"] == "needs_review"]
            for f in review:
                print(
                    f"  {report['name']} line {f['line']} {f['rule_id']}: {f['message']}", file=out
                )
    return _threshold_exit(args, [p.analysis.verdict for p in plans])


def _print_table(reports: list[dict], out) -> None:
    width = max(len(r["name"]) for r in reports)
    print(f"{'procedure':<{width}}  verdict  conversion  auto  note  review", file=out)
    for r in reports:
        c = r["counts"]
        print(
            f"{r['name']:<{width}}  {SEVERITY_LABEL[r['verdict']]:<7}  {r['conversion']:<10}  "
            f"{c['auto']:>4}  {c['auto_with_note']:>4}  {c['needs_review']:>6}",
            file=out,
        )


def _threshold_exit(args: argparse.Namespace, verdicts: list[Severity]) -> int:
    if args.fail_on is None:
        return EXIT_OK
    threshold = Severity(args.fail_on)
    return EXIT_THRESHOLD if any(v.rank >= threshold.rank for v in verdicts) else EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sproc_to_lakehouse",
        description="Migrate T-SQL stored procedures to Databricks (dbt / notebooks).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("source", nargs="+", type=Path, help=".sql files or folders of them")
        p.add_argument("--ddl", nargs="+", type=Path, help="CREATE TABLE files or folders")
        p.add_argument(
            "--no-ddl", action="store_true", help="do not look for DDL next to the source"
        )
        p.add_argument(
            "--fail-on",
            choices=[s.value for s in Severity if s is not Severity.AUTO],
            help="exit with code 2 if any procedure has this verdict or worse (for CI)",
        )

    convert = sub.add_parser("convert", help="generate Databricks code and reports")
    common(convert)
    convert.add_argument("--target", choices=["dbt", "notebook", "both"], default="both")
    convert.add_argument("--out", type=Path, default=Path("output"), help="output folder")
    convert.set_defaults(func=cmd_convert)

    analyze_cmd = sub.add_parser("analyze", help="classify constructs without generating code")
    common(analyze_cmd)
    analyze_cmd.add_argument("--format", choices=["text", "json"], default="text")
    analyze_cmd.set_defaults(func=cmd_analyze)
    return parser


def main(argv: Sequence[str] | None = None, out=None) -> int:
    out = out or sys.stdout
    args = build_parser().parse_args(argv)
    try:
        return args.func(args, out)
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
