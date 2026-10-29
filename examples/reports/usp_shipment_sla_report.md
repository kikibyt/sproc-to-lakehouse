# dbo.usp_shipment_sla_report

**Converts automatically, with review notes.** Fully converted: the generated code runs without manual steps.

- Source: `samples/tsql/07_usp_shipment_sla_report.sql`
- Findings: 7 auto, 5 with notes, 0 needing review
- Parameters: `@week_start DATE`
- Generated: `dbt/models/usp_shipment_sla_report/shipment_sla_report.sql`, `notebooks/usp_shipment_sla_report.py`

## Converted, with review notes

| Line | Rule | Note | Fix applied |
|---|---|---|---|
| 26 | EX005 | DATEDIFF(DAY): SQL Server counts unit boundaries crossed, Databricks counts whole units elapsed. Identical for DATE inputs in days; can differ by one for datetimes with a time part or for larger units. | - |
| 26 | EX009 | ISNULL becomes COALESCE. ISNULL returns the type of its first argument (and can truncate the fallback value); COALESCE uses type precedence. | - |
| 38 | DM007 | Returns a result set to the caller; becomes a model/view (dbt) or a displayed DataFrame (notebook). Consumers must read it from there. | - |
| 38 | EX006 | DATEPART(ISO_WEEK): sqlglot emits EXTRACT(WEEKISO ...), which is not a Databricks field; rewritten to EXTRACT(WEEK ...), which is ISO 8601. | iso_week_extract |
| 38 | EX015 | BIT column compared with or assigned 0/1. On Databricks BIT is BOOLEAN and ANSI mode rejects BOOLEAN = INT; rewritten to TRUE/FALSE and CAST(... AS BOOLEAN). | bit_to_boolean |

## Converted automatically

| Rule | Construct | Lines | How |
|---|---|---|---|
| EX003 | case_expression | 38 | CASE and IIF translate directly (IIF becomes IF). |
| EX004 | date_function | 16, 26, 38 | Date functions map to Databricks equivalents (DATEADD, LAST_DAY, MAKE_DATE, ...). |
| ST002 | table_variable | 26 | @late is populated once and read once; it is inlined as a CTE. |
| ST004 | parameters | 10 | 1 input parameter(s) become dbt vars and notebook widgets. |
| ST006 | session_option | 14 | SET NOCOUNT is a SQL Server session setting with no Databricks equivalent; dropped. |

## Conversion plan

| # | Step | T-SQL line | Kind | Fixes applied |
|---|---|---|---|---|
| 1 | result set | 38 | result | bit_to_boolean, iso_week_extract, transient_table_to_cte |

## Dropped

Statements with no Databricks equivalent needed:

- line 14: session option with no Databricks equivalent
- line 18: @late is defined by its query instead
