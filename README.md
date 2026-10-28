# sproc-to-lakehouse

Migrate T-SQL stored procedures to Databricks (PySpark/dbt) with automated parity testing.

> **Status: work in progress (Phase 1 of 5).** The parser and analyzer work and are tested;
> code generation, reports, a CLI and parity testing are still to come. All sample code and
> data in this repository are **synthetic** (a fictional specialty pharmacy).

## What works today

- **Parser** (`src/sproc_to_lakehouse/parser`): splits T-SQL files into procedures, reads
  parameters, and breaks each body into statements and control-flow blocks (IF/ELSE, WHILE,
  BEGIN/END, TRY/CATCH). It handles legacy code without semicolons. Each SQL statement is
  parsed with [sqlglot](https://github.com/tobymao/sqlglot), and the tables it reads and
  writes are recorded.
- **Analyzer** (`src/sproc_to_lakehouse/analyzer`): 39 rules classify every construct as
  `auto`, `auto_with_note` (converts, but a semantic difference is worth a look) or
  `needs_review`. Each finding comes with the reason. Besides the obvious blockers (cursors,
  dynamic SQL, transactions), the rules catch places where a plain sqlglot transpile would be
  silently wrong on Databricks. Examples: string `+` concatenation, `TRY_CONVERT` losing its
  "try", `STUFF()` passed through unchanged.
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

## Licence

MIT. See [LICENSE](LICENSE).
