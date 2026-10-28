"""Code generation: conversion plans, Databricks notebooks and dbt projects."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sproc_to_lakehouse.analyzer import analyze
from sproc_to_lakehouse.analyzer import rewrites as rw
from sproc_to_lakehouse.catalog import Catalog
from sproc_to_lakehouse.codegen.dbt import DbtProject, generate_dbt_project
from sproc_to_lakehouse.codegen.notebook import generate_notebook
from sproc_to_lakehouse.codegen.plan import Plan, build_plan
from sproc_to_lakehouse.codegen.sql_rewrites import EXPRESSION_REWRITES, STATEMENT_REWRITES
from sproc_to_lakehouse.parser import parse_file

# Every rewrite the generator performs. tests/test_rewrites_implemented.py checks this
# covers every rewrite the analyzer's classifications depend on.
IMPLEMENTED_REWRITES = {
    *EXPRESSION_REWRITES,
    *STATEMENT_REWRITES,
    rw.TRANSIENT_TABLE_TO_CTE,  # codegen.plan (structural)
    rw.BIT_TO_BOOLEAN,  # typed_rewrites, applied in codegen.plan
    rw.COMPACT_DATE_LITERAL,  # typed_rewrites, applied in codegen.plan
}

__all__ = ["IMPLEMENTED_REWRITES", "GeneratedOutput", "build_plans", "generate_all"]


@dataclass
class GeneratedOutput:
    plans: list[Plan]
    dbt: DbtProject
    notebooks: dict[str, str]  # procedure name -> notebook source


def build_plans(sql_dir: Path, ddl_dir: Path | None = None) -> tuple[list[Plan], Catalog | None]:
    catalog = Catalog.from_files(sorted(ddl_dir.glob("*.sql"))) if ddl_dir is not None else None
    procedures = [p for path in sorted(sql_dir.glob("*.sql")) for p in parse_file(path)]
    return [build_plan(analyze(p, catalog), catalog) for p in procedures], catalog


def generate_all(
    sql_dir: str | Path, ddl_dir: str | Path | None = None, out_dir: str | Path | None = None
) -> GeneratedOutput:
    """Generate the dbt project and notebooks for every procedure in ``sql_dir``.

    With ``out_dir``, files are written to ``<out_dir>/dbt`` and ``<out_dir>/notebooks``.
    """
    plans, catalog = build_plans(Path(sql_dir), Path(ddl_dir) if ddl_dir is not None else None)
    output = GeneratedOutput(
        plans=plans,
        dbt=generate_dbt_project(plans, catalog),
        notebooks={plan.name: generate_notebook(plan) for plan in plans},
    )
    if out_dir is not None:
        out = Path(out_dir)
        output.dbt.write(out / "dbt")
        (out / "notebooks").mkdir(parents=True, exist_ok=True)
        for name, source in output.notebooks.items():
            (out / "notebooks" / f"{name}.py").write_text(source, encoding="utf-8", newline="\n")
    return output
