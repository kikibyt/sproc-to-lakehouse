# sproc-to-lakehouse

Migrate T-SQL stored procedures to Databricks (PySpark/dbt) with automated parity testing.

> **Status: work in progress (Phase 3 of 5).** Parsing, analysis, code generation, reports and
> the CLI work and are tested; parity testing is still to come. All sample code and data
> in this repository are **synthetic** (a fictional specialty pharmacy).

## What works today

- **Parser** (`src/sproc_to_lakehouse/parser`): splits T-SQL files into procedures, reads
  parameters, and breaks each body into statements and control-flow blocks (IF/ELSE, WHILE,
  BEGIN/END, TRY/CATCH). It handles legacy code without semicolons. Each SQL statement is
  parsed with [sqlglot](https://github.com/tobymao/sqlglot), and the tables it reads and
  writes are recorded.
- **Analyzer** (`src/sproc_to_lakehouse/analyzer`): 41 rules classify every construct as
  `auto`, `auto_with_note` (converts, but a semantic difference is worth a look) or
  `needs_review`. Each finding comes with the reason. Besides the obvious blockers (cursors,
  dynamic SQL, transactions), the rules catch places where a plain sqlglot transpile would be
  silently wrong on Databricks. Examples: string `+` concatenation, `TRY_CONVERT` losing its
  "try", `STUFF()` passed through unchanged.
- **Code generation** (`src/sproc_to_lakehouse/codegen`): each procedure becomes an ordered
  plan of Databricks-ready steps, emitted as a Databricks notebook (`.py` source format) and
  as part of one dbt project for `dbt-databricks`. Procedures that fit dbt's model-per-table
  pattern become models (table, incremental `replace_where` / `merge`, view); the rest
  become an ordered dbt macro. Anything needing human judgement is emitted as a clearly
  marked manual step with the original T-SQL, and the generated code refuses to run past it.
- **Validation** (`src/sproc_to_lakehouse/validation`): optional tests run every generated
  statement through a local Apache Spark 4 session against empty tables built from the DDL
  (queries and INSERTs execute; MERGE/UPDATE/DELETE are parsed and resolved). This proves the
  SQL is valid and resolves; it does not yet prove results match SQL Server (Phase 4).
- **Sample corpus** (`samples/`): 12 synthetic procedures, the DDL for their tables, and a
  hand-written answer key (`expected_findings.yml`) that the tests check the analyzer against.

## Running it (Windows PowerShell)

```powershell
cd C:\projects\sproc-to-lakehouse
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pytest
ruff check .
```

Convert the samples (dbt project, notebooks and reports go to `output\`):

```powershell
python -m sproc_to_lakehouse convert samples\tsql --target both --out output
```

- `--target dbt|notebook|both` chooses what to generate. Reports are always written to
  `output\reports\`: one Markdown and one JSON file per procedure, plus `summary.md`
  and `summary.json`.
- `--ddl <files or folders>` loads table definitions; without it, a `ddl` folder next to the
  source folder is used if present (`--no-ddl` skips it). Some checks need column types.
- `python -m sproc_to_lakehouse analyze samples\tsql` only classifies, and
  `--fail-on needs_review` makes either command exit with code 2 for use in CI.

The Spark validation tests are skipped unless PySpark 4 and Java 17+ are installed
(`pip install -e ".[dev,spark]"`); they are intended for Linux/CI.

## Licence

MIT. See [LICENSE](LICENSE).
