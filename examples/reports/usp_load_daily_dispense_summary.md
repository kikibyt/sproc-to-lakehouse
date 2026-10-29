# dbo.usp_load_daily_dispense_summary

**Converts automatically, with review notes.** Fully converted: the generated code runs without manual steps.

- Source: `samples/tsql/01_usp_load_daily_dispense_summary.sql`
- Findings: 5 auto, 3 with notes, 0 needing review
- Parameters: `@run_date DATE` = `NULL`
- Generated: `dbt/models/usp_load_daily_dispense_summary/daily_dispense_summary.sql`, `notebooks/usp_load_daily_dispense_summary.py`

## Converted, with review notes

| Line | Rule | Note | Fix applied |
|---|---|---|---|
| 16 | EX007 | Current time: SQL Server returns the server's local time; Databricks returns the session time zone (UTC unless configured). | - |
| 22 | EX007 | Current time: SQL Server returns the server's local time; Databricks returns the session time zone (UTC unless configured). | - |
| 56 | EX008 | CONVERT style code is mapped to a Databricks format pattern; verify the output format on sample data. | - |

## Converted automatically

| Rule | Construct | Lines | How |
|---|---|---|---|
| CF008 | if_branch | 15 | Parameter default (IF @p IS NULL SET @p = ...): folded into COALESCE(@p, ...). |
| EX001 | cte | 22 | CTEs are supported natively. |
| EX004 | date_function | 16 | Date functions map to Databricks equivalents (DATEADD, LAST_DAY, MAKE_DATE, ...). |
| ST004 | parameters | 8 | 1 input parameter(s) become dbt vars and notebook widgets. |
| ST006 | session_option | 12 | SET NOCOUNT is a SQL Server session setting with no Databricks equivalent; dropped. |

## Conversion plan

| # | Step | T-SQL line | Kind | Fixes applied |
|---|---|---|---|---|
| 1 | load @run_date | 15 | scalar | - |
| 2 | delete dbo.daily_dispense_summary | 19 | write | - |
| 3 | insert dbo.daily_dispense_summary | 22 | write | - |

## Dropped

Statements with no Databricks equivalent needed:

- line 12: session option with no Databricks equivalent
