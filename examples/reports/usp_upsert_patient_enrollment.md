# dbo.usp_upsert_patient_enrollment

**Converts automatically, with review notes.** Fully converted: the generated code runs without manual steps.

- Source: `samples/tsql/03_usp_upsert_patient_enrollment.sql`
- Findings: 5 auto, 7 with notes, 0 needing review
- Parameters: `@load_batch_id INT`
- Generated: `dbt/models/usp_upsert_patient_enrollment/dim_patient_enrollment.sql`, `notebooks/usp_upsert_patient_enrollment.py`

## Converted, with review notes

| Line | Rule | Note | Fix applied |
|---|---|---|---|
| 16 | DM001 | Maps to Delta MERGE INTO. Like SQL Server, Delta fails when several source rows match one target row, so source de-duplication must be preserved. | - |
| 16 | EX007 | Current time: SQL Server returns the server's local time; Databricks returns the session time zone (UTC unless configured). | - |
| 16 | EX009 | ISNULL becomes COALESCE. ISNULL returns the type of its first argument (and can truncate the fallback value); COALESCE uses type precedence. | - |
| 16 | EX015 | BIT column compared with or assigned 0/1. On Databricks BIT is BOOLEAN and ANSI mode rejects BOOLEAN = INT; rewritten to TRUE/FALSE and CAST(... AS BOOLEAN). | bit_to_boolean |
| 16 | EX016 | Compact date string such as '19000101' compared with a date. SQL Server converts it implicitly; Spark fails at run time (CAST_INVALID_INPUT). Rewritten to a DATE literal. | compact_date_literal |
| 31 | EX008 | CONVERT style code is mapped to a Databricks format pattern; verify the output format on sample data. | - |
| 32 | EX008 | TRY_CONVERT with a style code: sqlglot emits TO_DATE/TO_TIMESTAMP, which raises on bad input under ANSI mode instead of returning NULL; rewritten to the TRY_ variant. | try_convert_safe_parse |

## Converted automatically

| Rule | Construct | Lines | How |
|---|---|---|---|
| EX002 | window_function | 16 | Window functions and frames translate directly. |
| EX003 | case_expression | 16 | CASE and IIF translate directly (IIF becomes IF). |
| EX011 | string_function | 16 | String functions map directly (CHARINDEX becomes LOCATE, LTRIM/RTRIM stay). |
| ST004 | parameters | 10 | 1 input parameter(s) become dbt vars and notebook widgets. |
| ST006 | session_option | 14 | SET NOCOUNT is a SQL Server session setting with no Databricks equivalent; dropped. |

## Conversion plan

| # | Step | T-SQL line | Kind | Fixes applied |
|---|---|---|---|---|
| 1 | merge dbo.dim_patient_enrollment | 16 | write | bit_to_boolean, compact_date_literal, try_convert_safe_parse |

## Dropped

Statements with no Databricks equivalent needed:

- line 14: session option with no Databricks equivalent
