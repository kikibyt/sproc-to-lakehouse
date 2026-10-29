# dbo.usp_build_monthly_inventory_snapshot

**Converts automatically, with review notes.** Fully converted: the generated code runs without manual steps.

- Source: `samples/tsql/02_usp_build_monthly_inventory_snapshot.sql`
- Findings: 11 auto, 3 with notes, 0 needing review
- Parameters: `@snapshot_month DATE`
- Generated: `dbt/models/usp_build_monthly_inventory_snapshot/inventory_snapshots.sql`, `notebooks/usp_build_monthly_inventory_snapshot.py`

## Converted, with review notes

| Line | Rule | Note | Fix applied |
|---|---|---|---|
| 40 | EX007 | Current time: SQL Server returns the server's local time; Databricks returns the session time zone (UTC unless configured). | - |
| 40 | EX009 | ISNULL becomes COALESCE. ISNULL returns the type of its first argument (and can truncate the fallback value); COALESCE uses type precedence. | - |
| 66 | DM002 | Delta UPDATE/DELETE has no FROM/JOIN clause; rewritten as MERGE INTO with the join as the source. If the join matches a target row more than once, SQL Server picks one silently but Delta MERGE fails. | update_from_to_merge |

## Converted automatically

| Rule | Construct | Lines | How |
|---|---|---|---|
| EX001 | cte | 66 | CTEs are supported natively. |
| EX002 | window_function | 66 | Window functions and frames translate directly. |
| EX003 | case_expression | 25 | CASE and IIF translate directly (IIF becomes IF). |
| EX004 | date_function | 17, 18, 19, 66 | Date functions map to Databricks equivalents (DATEADD, LAST_DAY, MAKE_DATE, ...). |
| ST001 | temp_table | 25 | #movements is populated once and read once; it is inlined as a CTE. |
| ST004 | parameters | 11 | 1 input parameter(s) become dbt vars and notebook widgets. |
| ST006 | session_option | 15 | SET NOCOUNT is a SQL Server session setting with no Databricks equivalent; dropped. |
| ST008 | temp_cleanup | 21 | Temp table cleanup has no equivalent (CTEs and views are scoped to the run); removed. |

## Conversion plan

| # | Step | T-SQL line | Kind | Fixes applied |
|---|---|---|---|---|
| 1 | delete dbo.inventory_snapshots | 37 | write | - |
| 2 | insert dbo.inventory_snapshots | 40 | write | transient_table_to_cte |
| 3 | update dbo.inventory_snapshots | 66 | write | update_from_to_merge |

## Dropped

Statements with no Databricks equivalent needed:

- line 15: session option with no Databricks equivalent
- line 21: temp table cleanup is not needed (CTEs/views are scoped to the run)
