"""Assemble a dbt project (for the dbt-databricks adapter) from conversion plans."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from sqlglot import exp

from sproc_to_lakehouse.catalog import Catalog
from sproc_to_lakehouse.codegen.dbt.jinja import (
    SQL_LITERAL_MACRO,
    as_jinja_string,
    param_expression,
)
from sproc_to_lakehouse.codegen.dbt.patterns import (
    ModelSpec,
    ProcedureMapping,
    ephemeral_name,
    map_procedure,
)
from sproc_to_lakehouse.codegen.plan import Plan, Step, StepKind
from sproc_to_lakehouse.codegen.render import SqlRenderer
from sproc_to_lakehouse.models import Severity, TableRef
from sproc_to_lakehouse.parser.tables import table_ref

PROJECT_NAME = "sproc_to_lakehouse"
_RUN_TIMESTAMP = "CAST('{{ run_started_at.strftime(\"%Y-%m-%d %H:%M:%S\") }}' AS TIMESTAMP)"


@dataclass
class DbtProject:
    files: dict[str, str] = field(default_factory=dict)
    mappings: list[ProcedureMapping] = field(default_factory=list)

    def write(self, out_dir: Path) -> list[Path]:
        written = []
        for rel, content in sorted(self.files.items()):
            path = out_dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
            written.append(path)
        return written


class _Builder:
    def __init__(self, plans: list[Plan], catalog: Catalog) -> None:
        self.catalog = catalog
        self.mappings = [map_procedure(p) for p in plans]
        self.owned: dict[str, str] = {}  # table qualified name -> model name
        for mapping in self.mappings:
            for spec in mapping.models:
                if spec.target is not None and spec.target.qualified_name not in self.owned:
                    self.owned[spec.target.qualified_name] = spec.name
        self.sources: dict[str, set[str]] = {}
        self.project = DbtProject(mappings=self.mappings)

    # -- naming ----------------------------------------------------------------------
    def _renderer(self, plan: Plan, current: TableRef | None, *, in_macro: bool) -> SqlRenderer:
        def table(ref: TableRef) -> str:
            if current is not None and ref.qualified_name == current.qualified_name:
                return "{{ this }}"
            if ref.qualified_name in self.owned and not in_macro:
                return f"{{{{ ref('{self.owned[ref.qualified_name]}') }}}}"
            if ref.name.startswith("tmp_") and not ref.schema:
                return f"{{{{ ref('{ephemeral_name(plan, ref.name)}') }}}}"
            schema = ref.schema or "dbo"
            self.sources.setdefault(schema, set()).add(ref.name)
            return f"{{{{ source('{schema}', '{ref.name}') }}}}"

        def scalar(name: str) -> str:
            if in_macro:
                return f"{{{{ sql_literal(v_{name}) }}}}"
            query = plan.env.scalars[name].query
            inner = renderer.render(query.expressions[0].this, pretty=False)
            return f"({inner})"

        renderer = SqlRenderer(
            table=table,
            param=lambda name: param_expression(plan.env.params[name]),
            scalar=scalar,
            now=None if in_macro else (lambda: _RUN_TIMESTAMP),
        )
        return renderer

    # -- build -----------------------------------------------------------------------
    def build(self) -> DbtProject:
        files = self.project.files
        for mapping in self.mappings:
            folder = f"models/{mapping.plan.name}"
            if mapping.as_macro:
                files[f"macros/procedures/{mapping.plan.name}.sql"] = self._macro(mapping)
                continue
            for spec in mapping.models:
                files[f"{folder}/{spec.name}.sql"] = self._model(mapping.plan, spec)
                test = self._unique_key_test(spec)
                if test is not None:
                    files[f"tests/assert_{spec.name}_unique_key.sql"] = test
            files[f"{folder}/_{mapping.plan.name}__models.yml"] = self._models_yml(mapping)
        files["models/_sources.yml"] = self._sources_yml()
        files["macros/sql_literal.sql"] = SQL_LITERAL_MACRO
        files["dbt_project.yml"] = _DBT_PROJECT_YML
        files["README.md"] = self._readme()
        return self.project

    # -- models ----------------------------------------------------------------------
    def _model(self, plan: Plan, spec: ModelSpec) -> str:
        renderer = self._renderer(plan, spec.target, in_macro=False)
        config: dict[str, str] = {"materialized": json.dumps(spec.materialized)}
        if spec.materialized == "incremental":
            config["incremental_strategy"] = json.dumps(spec.strategy)
        if spec.replace_where is not None:
            predicate = renderer.render(spec.replace_where, pretty=False)
            config["incremental_predicates"] = f"[{as_jinja_string(predicate)}]"
        if spec.merge is not None:
            config["unique_key"] = json.dumps(spec.merge.unique_key)
            if spec.merge.exclude_from_update:
                config["merge_exclude_columns"] = json.dumps(spec.merge.exclude_from_update)
        if spec.post_hooks:
            hooks = [renderer.render(s.sql, pretty=False) for s in spec.post_hooks]
            config["post_hook"] = "[\n    " + ",\n    ".join(json.dumps(h) for h in hooks) + "\n  ]"

        body = self._model_body(spec, renderer)
        config_text = ",\n  ".join(f"{k}={v}" for k, v in config.items())
        header = self._model_header(plan, spec, body)
        return f"{header}{{{{ config(\n  {config_text}\n) }}}}\n\n{body}\n"

    def _model_body(self, spec: ModelSpec, renderer: SqlRenderer) -> str:
        if spec.kind == "merge":
            return self._merge_body(spec, renderer)
        step = spec.primary
        if spec.kind in ("view", "ephemeral"):
            return renderer.render(step.sql)
        insert: exp.Insert = step.sql
        query = insert.expression.copy()
        with_ = insert.args.get("with_")
        if with_ is not None:
            ctes = [c.copy() for c in with_.expressions]
            if query.args.get("with_") is not None:
                ctes.extend(c.copy() for c in query.args["with_"].expressions)
            query.set("with_", exp.With(expressions=ctes))
        columns = (
            [c.name.lower() for c in insert.this.expressions]
            if isinstance(insert.this, exp.Schema)
            else []
        )
        if columns and isinstance(query, exp.Select) and len(query.expressions) == len(columns):
            query.set(
                "expressions",
                [
                    exp.alias_(e.unalias().copy(), col)
                    for e, col in zip(query.expressions, columns, strict=True)
                ],
            )
        elif columns:
            outer = exp.select(*columns).from_(
                exp.Subquery(
                    this=query,
                    alias=exp.TableAlias(
                        this=exp.to_identifier("src"),
                        columns=[exp.to_identifier(c) for c in columns],
                    ),
                )
            )
            query = outer
        return renderer.render(query)

    def _merge_body(self, spec: ModelSpec, renderer: SqlRenderer) -> str:
        shape = spec.merge
        src = shape.source_alias
        source_sql = renderer.render(shape.source)
        select_list = ",\n  ".join(
            f"{renderer.render(value, pretty=False)} AS {col}" for col, value in shape.columns
        )
        lines = [
            f"WITH {src} AS (",
            *("  " + line for line in source_sql.splitlines()),
            ")",
            "SELECT",
            f"  {select_list}",
            f"FROM {src}",
        ]
        if shape.has_matched_update and shape.matched_condition is None:
            return "\n".join(lines)
        # Only rows that are new, or that the original WHEN MATCHED condition would update.
        key = shape.unique_key[0]
        on = renderer.render(shape.on, pretty=False)
        lines += [
            "{% if is_incremental() %}",
            f"LEFT JOIN {{{{ this }}}} AS {shape.target_alias}",
            f"  ON {on}",
        ]
        if shape.matched_condition is not None:
            condition = renderer.render(shape.matched_condition, pretty=False)
            lines.append(f"WHERE {shape.target_alias}.{key} IS NULL OR ({condition})")
        else:
            lines.append(f"WHERE {shape.target_alias}.{key} IS NULL")
        lines.append("{% endif %}")
        return "\n".join(lines)

    def _model_header(self, plan: Plan, spec: ModelSpec, body: str) -> str:
        proc = plan.analysis.procedure
        what = {
            "table": "TRUNCATE + INSERT ... SELECT: full rebuild on every run.",
            "view": "Result set the procedure returned to its caller.",
            "ephemeral": "Intermediate result (temp table read by several steps).",
        }.get(spec.materialized)
        if what is None:
            what = {
                "append": "INSERT ... SELECT: appends on every run.",
                "replace_where": "DELETE slice + INSERT: replaces the slice matched by "
                "incremental_predicates on every run.",
                "merge": "MERGE: updates changed rows and inserts new ones (unique_key).",
            }[spec.strategy]
        lines = [
            f"Generated by sproc-to-lakehouse from {proc.qualified_name}"
            + (f" ({proc.source_path})" if proc.source_path else ""),
            f"T-SQL line {spec.primary.line}. {what}",
        ]
        if spec.post_hooks:
            lines.append(
                "post_hook: later statements of the procedure on this table, kept in order "
                f"(T-SQL line(s) {', '.join(str(s.line) for s in spec.post_hooks)})."
            )
        reads_itself = spec.target is not None and any(
            table_ref(t).qualified_name == spec.target.qualified_name
            for t in _source_query(spec).find_all(exp.Table)
        )
        if reads_itself and spec.materialized == "incremental":
            lines.append(
                "NOTE: this model reads its own earlier rows ({{ this }}). Load history into "
                "the table before the first run; a --full-refresh would lose it."
            )
        if _RUN_TIMESTAMP in body:
            lines.append(
                "NOTE: GETDATE()/SYSDATETIME() use dbt's run_started_at (UTC), so every model "
                "in one run sees the same timestamp."
            )
        rewrites = {r for s in [spec.primary, *spec.post_hooks] for r in s.rewrites}
        if rewrites:
            lines.append(f"Rewrites applied: {', '.join(sorted(rewrites))}.")
        return "{#\n  " + "\n  ".join(_escape_comment(line) for line in lines) + "\n#}\n"

    # -- schema files ----------------------------------------------------------------
    def _models_yml(self, mapping: ProcedureMapping) -> str:
        models = []
        for spec in mapping.models:
            entry: dict = {
                "name": spec.name,
                "description": f"Converted from {mapping.plan.analysis.procedure.qualified_name} "
                f"(T-SQL line {spec.primary.line}).",
            }
            schema = self.catalog.get(spec.target.qualified_name) if spec.target else None
            if schema is not None and spec.materialized != "ephemeral":
                built = _model_columns(spec)
                columns = []
                for col in schema.columns:
                    if built and col.name not in built:
                        continue
                    item: dict = {"name": col.name, "data_type": col.databricks_type.lower()}
                    tests = []
                    if not col.nullable:
                        tests.append("not_null")
                    if schema.primary_key == [col.name]:
                        tests.append("unique")
                    if tests:
                        item["data_tests"] = tests
                    columns.append(item)
                entry["columns"] = columns
            models.append(entry)
        return yaml.safe_dump({"version": 2, "models": models}, sort_keys=False, width=100)

    def _unique_key_test(self, spec: ModelSpec) -> str | None:
        schema = self.catalog.get(spec.target.qualified_name) if spec.target else None
        if schema is None or len(schema.primary_key) < 2:
            return None
        keys = ", ".join(schema.primary_key)
        return (
            f"-- Composite primary key of {schema.qualified_name} must stay unique.\n"
            f"SELECT {keys}, COUNT(*) AS n\nFROM {{{{ ref('{spec.name}') }}}}\n"
            f"GROUP BY {keys}\nHAVING COUNT(*) > 1\n"
        )

    def _sources_yml(self) -> str:
        sources = []
        for schema_name in sorted(self.sources):
            tables = []
            for name in sorted(self.sources[schema_name]):
                entry: dict = {"name": name}
                table = self.catalog.get(f"{schema_name}.{name}")
                if table is not None and table.primary_key:
                    pk_tests = ["not_null"] + (["unique"] if len(table.primary_key) == 1 else [])
                    entry["columns"] = [
                        {"name": col, "data_tests": pk_tests} for col in table.primary_key
                    ]
                tables.append(entry)
            sources.append(
                {
                    "name": schema_name,
                    "schema": f"{{{{ var('source_schema_{schema_name}', '{schema_name}') }}}}",
                    "tables": tables,
                }
            )
        return yaml.safe_dump({"version": 2, "sources": sources}, sort_keys=False, width=100)

    # -- macros ----------------------------------------------------------------------
    def _macro(self, mapping: ProcedureMapping) -> str:
        plan = mapping.plan
        renderer = self._renderer(plan, None, in_macro=True)
        proc = plan.analysis.procedure
        manual = plan.manual_steps
        lines = [
            "{#",
            f"  Generated by sproc-to-lakehouse from {proc.qualified_name}"
            + (f" ({proc.source_path})" if proc.source_path else ""),
            f"  Not split into dbt models because: {mapping.reason}.",
            "  The procedure runs as one ordered macro:",
            f"    dbt run-operation run_{plan.name} --vars '{{...}}'",
            "#}",
            f"{{% macro run_{plan.name}() %}}",
        ]
        if manual:
            message = (
                f"{plan.name}: {len(manual)} step(s) still need manual conversion "
                f"(see macros/procedures/{plan.name}.sql)."
            )
            lines.append(f"  {{{{ exceptions.raise_compiler_error({json.dumps(message)}) }}}}")
        for number, step in enumerate(plan.steps, start=1):
            lines.append("")
            lines.extend("  " + line for line in self._macro_step(number, step, renderer))
        lines.append("{% endmacro %}")
        return "\n".join(lines) + "\n"

    def _macro_step(self, number: int, step: Step, renderer: SqlRenderer) -> list[str]:
        title = f"Step {number}: {step.title} (T-SQL line {step.line})"
        if step.kind is StepKind.MANUAL:
            body = [f"{{# {title}", "   MANUAL CONVERSION REQUIRED:"]
            body += [f"   - {_escape_comment(r)}" for r in step.reasons]
            body += ["   Original T-SQL:"]
            body += ["     " + _escape_comment(line) for line in step.source_sql.splitlines()]
            return [*body, "#}"]
        if step.kind is StepKind.PRINT:
            return [
                f"{{# {title} #}}",
                f"{{% do log({json.dumps(step.sql.this if step.sql else '')}, info=True) %}}",
            ]
        sql = renderer.render(step.sql)
        name = f"step_{number}"
        if step.kind is StepKind.SCALAR:
            return [
                f"{{# {title} #}}",
                f"{{% call statement('{name}', fetch_result=True) %}}",
                *sql.splitlines(),
                "{% endcall %}",
                f"{{% set v_{step.name} = load_result('{name}')['data'][0][0] %}}",
            ]
        if step.kind is StepKind.TEMP_VIEW:
            sql = f"CREATE OR REPLACE TEMPORARY VIEW {step.name} AS\n{sql}"
        return [
            f"{{# {title} #}}",
            f"{{% call statement('{name}') %}}",
            *sql.splitlines(),
            "{% endcall %}",
        ]

    def _readme(self) -> str:
        models = [(m.plan.name, s) for m in self.mappings for s in m.models]
        macros = [m for m in self.mappings if m.as_macro]
        rows = "\n".join(
            f"| `{proc}` | `{s.name}` | {s.materialized}"
            + (f" ({s.strategy})" if s.strategy else "")
            + " |"
            for proc, s in models
        )
        macro_rows = "\n".join(
            f"| `{m.plan.name}` | `run_{m.plan.name}` | {m.reason} |" for m in macros
        )
        return _README_TEMPLATE.format(
            model_rows=rows or "| - | - | - |", macro_rows=macro_rows or "| - | - | - |"
        )


def _source_query(spec: ModelSpec) -> exp.Expression:
    """The part of the primary statement that produces rows (not its target)."""
    if spec.merge is not None:
        return spec.merge.source
    sql = spec.primary.sql
    return sql.expression if isinstance(sql, exp.Insert) else sql


def _model_columns(spec: ModelSpec) -> list[str]:
    if spec.merge is not None:
        return [c for c, _ in spec.merge.columns]
    sql = spec.primary.sql
    if isinstance(sql, exp.Insert) and isinstance(sql.this, exp.Schema):
        return [c.name.lower() for c in sql.this.expressions]
    return []


def _escape_comment(text: str) -> str:
    return text.replace("#}", "# }").replace("{#", "{ #")


def generate_dbt_project(plans: list[Plan], catalog: Catalog | None = None) -> DbtProject:
    return _Builder(plans, catalog or Catalog()).build()


def review_notes(plan: Plan) -> list[str]:
    seen: set[str] = set()
    notes = []
    for f in plan.analysis.by_severity(Severity.AUTO_WITH_NOTE):
        if f.rule_id not in seen:
            seen.add(f.rule_id)
            notes.append(f"{f.rule_id}: {f.message}")
    return notes


_DBT_PROJECT_YML = f"""\
# Generated by sproc-to-lakehouse. Requires dbt-core >= 1.8 and dbt-databricks.
name: {PROJECT_NAME}
version: "1.0.0"
config-version: 2
profile: {PROJECT_NAME}

model-paths: ["models"]
macro-paths: ["macros"]
test-paths: ["tests"]

models:
  {PROJECT_NAME}:
    +file_format: delta
"""

_README_TEMPLATE = """\
# Generated dbt project

Generated by **sproc-to-lakehouse** for the `dbt-databricks` adapter
(dbt-core 1.8 or later). All models are Delta tables or views.

## Procedures converted to models

| Procedure | Model | Materialization |
|---|---|---|
{model_rows}

## Procedures kept as ordered macros

These procedures do not fit dbt's model-per-table pattern (or still need manual work).
They are emitted as one macro each so statement order is preserved; a macro with
manual steps refuses to run until they are converted.

| Procedure | Macro | Why not models |
|---|---|---|
{macro_rows}

## Running

```bash
dbt build --vars '{{"run_date": "2024-01-31"}}'
dbt run-operation run_usp_update_shipment_status --vars '{{"as_of": "2024-01-31 18:00:00"}}'
```

Procedure parameters are dbt vars with the same names (an omitted var falls back
to the T-SQL default, if the parameter had one). Source tables are read from the
schema named in `models/_sources.yml` (override with
`--vars '{{"source_schema_dbo": "my_schema"}}'`).

A `profiles.yml` entry named `sproc_to_lakehouse` pointing at a Databricks SQL
warehouse or cluster is required; see the dbt-databricks documentation.
"""
