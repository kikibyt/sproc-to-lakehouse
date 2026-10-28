# sproc-to-lakehouse

Migrate T-SQL stored procedures to Databricks (PySpark/dbt) with automated parity testing.

> **Status: work in progress (Phase 2 of 5).** Parsing, analysis and code generation work and
> are tested; reports, a CLI and parity testing are still to come. All sample code and data
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

Generate the dbt project and notebooks for the samples into `output\` (a CLI comes in Phase 3):

```powershell
python -c "from sproc_to_lakehouse.codegen import generate_all; generate_all('samples/tsql', 'samples/ddl', 'output')"
```

The Spark validation tests are skipped unless PySpark 4 and Java 17+ are installed
(`pip install -e ".[dev,spark]"`); they are intended for Linux/CI.

## Licence

MIT. See [LICENSE](LICENSE).
