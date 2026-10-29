# dbo.usp_update_shipment_status

**Needs human review.** Partly converted: 3 step(s) need manual conversion.

- Source: `samples/tsql/08_usp_update_shipment_status.sql`
- Findings: 4 auto, 5 with notes, 3 needing review
- Parameters: `@as_of DATETIME2(0)` = `NULL`
- Generated: `dbt/macros/procedures/usp_update_shipment_status.sql`, `notebooks/usp_update_shipment_status.py`

## Needs human review

| Line | Rule | Why | T-SQL |
|---|---|---|---|
| 43 | ST005 | @@ROWCOUNT: Rows affected by the previous statement. On Delta this comes from the operation metrics (DataFrame counts or DESCRIBE HISTORY), not a session variable. | `SET @rows_delivered = @@ROWCOUNT` |
| 56 | ST005 | @@ROWCOUNT: Rows affected by the previous statement. On Delta this comes from the operation metrics (DataFrame counts or DESCRIBE HISTORY), not a session variable. | `SET @rows_exception = @@ROWCOUNT` |
| 65 | ST005 | @@ROWCOUNT: Rows affected by the previous statement. On Delta this comes from the operation metrics (DataFrame counts or DESCRIBE HISTORY), not a session variable. | `SET @rows_delayed = @@ROWCOUNT` |

## Converted, with review notes

| Line | Rule | Note | Fix applied |
|---|---|---|---|
| 20 | EX007 | Current time: SQL Server returns the server's local time; Databricks returns the session time zone (UTC unless configured). | - |
| 20 | EX009 | ISNULL becomes COALESCE. ISNULL returns the type of its first argument (and can truncate the fallback value); COALESCE uses type precedence. | - |
| 23 | DM002 | Delta UPDATE/DELETE has no FROM/JOIN clause; rewritten as MERGE INTO with the join as the source. If the join matches a target row more than once, SQL Server picks one silently but Delta MERGE fails. | update_from_to_merge |
| 46 | DM002 | Delta UPDATE/DELETE has no FROM/JOIN clause; rewritten as MERGE INTO with the join as the source. If the join matches a target row more than once, SQL Server picks one silently but Delta MERGE fails. | update_from_to_merge |
| 67 | EX007 | Current time: SQL Server returns the server's local time; Databricks returns the session time zone (UTC unless configured). | - |

## Converted automatically

| Rule | Construct | Lines | How |
|---|---|---|---|
| EX001 | cte | 23 | CTEs are supported natively. |
| EX002 | window_function | 23 | Window functions and frames translate directly. |
| ST004 | parameters | 10 | 1 input parameter(s) become dbt vars and notebook widgets. |
| ST006 | session_option | 14 | SET NOCOUNT is a SQL Server session setting with no Databricks equivalent; dropped. |

## Conversion plan

| # | Step | T-SQL line | Kind | Fixes applied |
|---|---|---|---|---|
| 1 | load @as_of | 20 | scalar | - |
| 2 | update dbo.shipments | 23 | write | update_from_to_merge |
| 3 | set_variable statement | 43 | manual | - |
| 4 | update dbo.shipments | 46 | write | update_from_to_merge |
| 5 | set_variable statement | 56 | manual | - |
| 6 | update dbo.shipments | 59 | write | - |
| 7 | manual block | 65 | manual | - |

### Step 3: manual conversion (T-SQL line 43)

- ST005: @@ROWCOUNT: Rows affected by the previous statement. On Delta this comes from the operation metrics (DataFrame counts or DESCRIBE HISTORY), not a session variable.

```sql
SET @rows_delivered = @@ROWCOUNT
```

### Step 5: manual conversion (T-SQL line 56)

- ST005: @@ROWCOUNT: Rows affected by the previous statement. On Delta this comes from the operation metrics (DataFrame counts or DESCRIBE HISTORY), not a session variable.

```sql
SET @rows_exception = @@ROWCOUNT
```

### Step 7: manual conversion (T-SQL line 65)

- ST005: @@ROWCOUNT: Rows affected by the previous statement. On Delta this comes from the operation metrics (DataFrame counts or DESCRIBE HISTORY), not a session variable.
- uses a variable that cannot be resolved: assigned by a manual step (line 43)

```sql
SET @rows_delayed = @@ROWCOUNT
INSERT INTO dbo.etl_run_log (proc_name, step_name, status, rows_affected, started_at, finished_at)
    VALUES
        ('usp_update_shipment_status', 'mark_delivered', 'SUCCEEDED', @rows_delivered, @as_of, SYSDATETIME()),
        ('usp_update_shipment_status', 'mark_exception', 'SUCCEEDED', @rows_exception, @as_of, SYSDATETIME()),
        ('usp_update_shipment_status', 'mark_delayed',   'SUCCEEDED', @rows_delayed,   @as_of, SYSDATETIME())
```

## Dropped

Statements with no Databricks equivalent needed:

- line 14: session option with no Databricks equivalent
