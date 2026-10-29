# dbo.usp_calc_refill_gaps

**Converts automatically, with review notes.** Fully converted: the generated code runs without manual steps.

- Source: `samples/tsql/04_usp_calc_refill_gaps.sql`
- Findings: 7 auto, 4 with notes, 0 needing review
- Parameters: `@as_of_date DATE`, `@gap_threshold_days INT` = `7`
- Generated: `dbt/models/usp_calc_refill_gaps/refill_gap_events.sql`, `notebooks/usp_calc_refill_gaps.py`

## Converted, with review notes

| Line | Rule | Note | Fix applied |
|---|---|---|---|
| 18 | EX005 | DATEDIFF(DAY): SQL Server counts unit boundaries crossed, Databricks counts whole units elapsed. Identical for DATE inputs in days; can differ by one for datetimes with a time part or for larger units. | - |
| 18 | EX007 | Current time: SQL Server returns the server's local time; Databricks returns the session time zone (UTC unless configured). | - |
| 18 | EX009 | ISNULL becomes COALESCE. ISNULL returns the type of its first argument (and can truncate the fallback value); COALESCE uses type precedence. | - |
| 18 | EX015 | BIT column compared with or assigned 0/1. On Databricks BIT is BOOLEAN and ANSI mode rejects BOOLEAN = INT; rewritten to TRUE/FALSE and CAST(... AS BOOLEAN). | bit_to_boolean |

## Converted automatically

| Rule | Construct | Lines | How |
|---|---|---|---|
| DM006 | truncate | 16 | Full reload: becomes a table materialization (dbt) and DELETE FROM before the INSERT (notebook; open-source Delta has no TRUNCATE). |
| EX001 | cte | 18 | CTEs are supported natively. |
| EX002 | window_function | 18 | Window functions and frames translate directly. |
| EX003 | case_expression | 18 | CASE and IIF translate directly (IIF becomes IF). |
| EX004 | date_function | 18 | Date functions map to Databricks equivalents (DATEADD, LAST_DAY, MAKE_DATE, ...). |
| ST004 | parameters | 9 | 2 input parameter(s) become dbt vars and notebook widgets. |
| ST006 | session_option | 14 | SET NOCOUNT is a SQL Server session setting with no Databricks equivalent; dropped. |

## Conversion plan

| # | Step | T-SQL line | Kind | Fixes applied |
|---|---|---|---|---|
| 1 | truncate dbo.refill_gap_events | 16 | write | - |
| 2 | insert dbo.refill_gap_events | 18 | write | bit_to_boolean |

## Dropped

Statements with no Databricks equivalent needed:

- line 14: session option with no Databricks equivalent
