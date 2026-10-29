# dbo.usp_recalc_reorder_points_cursor

**Needs human review.** Partly converted: 1 step(s) need manual conversion.

- Source: `samples/tsql/10_usp_recalc_reorder_points_cursor.sql`
- Findings: 7 auto, 9 with notes, 4 needing review
- Parameters: `@as_of_date DATE`
- Generated: `dbt/macros/procedures/usp_recalc_reorder_points_cursor.sql`, `notebooks/usp_recalc_reorder_points_cursor.py`

## Needs human review

| Line | Rule | Why | T-SQL |
|---|---|---|---|
| 24 | CF001 | Cursor loops process one row at a time. Spark has no cursors; the loop body must be re-expressed as set-based logic (joins, window functions, aggregates). | `DECLARE product_cursor CURSOR LOCAL FAST_FORWARD FOR SELECT p.product_id, p.lead_time_days, CASE ...` |
| 35 | CF002 | Procedural loop. Its termination and per-iteration state cannot be translated mechanically; rewrite set-based or as a driver loop in Python. | `WHILE @@FETCH_STATUS = 0` |
| 35 | ST005 | @@FETCH_STATUS: Cursor loop state; it disappears once the cursor is rewritten set-based. | `@@FETCH_STATUS = 0` |
| 51 | CF008 | Branch depends on table data (dbo.product_reorder_points) evaluated at run time. dbt models cannot branch on data; express it as a set-based condition or MERGE. | `IF EXISTS (SELECT 1 FROM dbo.product_reorder_points WHERE product_id = @product_id)` |

## Converted, with review notes

| Line | Rule | Note | Fix applied |
|---|---|---|---|
| 24 | EX015 | BIT column compared with or assigned 0/1. On Databricks BIT is BOOLEAN and ANSI mode rejects BOOLEAN = INT; rewritten to TRUE/FALSE and CAST(... AS BOOLEAN). | bit_to_boolean |
| 37 | EX009 | ISNULL becomes COALESCE. ISNULL returns the type of its first argument (and can truncate the fallback value); COALESCE uses type precedence. | - |
| 37 | ST003 | Variable is loaded from a query. The notebook collects the value at this point; dbt inlines it as a scalar subquery, evaluated when the model runs. | - |
| 44 | EX009 | ISNULL becomes COALESCE. ISNULL returns the type of its first argument (and can truncate the fallback value); COALESCE uses type precedence. | - |
| 44 | ST003 | Variable is loaded from a query. The notebook collects the value at this point; dbt inlines it as a scalar subquery, evaluated when the model runs. | - |
| 52 | EX007 | Current time: SQL Server returns the server's local time; Databricks returns the session time zone (UTC unless configured). | - |
| 52 | EX015 | BIT column compared with or assigned 0/1. On Databricks BIT is BOOLEAN and ANSI mode rejects BOOLEAN = INT; rewritten to TRUE/FALSE and CAST(... AS BOOLEAN). | bit_to_boolean |
| 60 | EX007 | Current time: SQL Server returns the server's local time; Databricks returns the session time zone (UTC unless configured). | - |
| 60 | EX015 | BIT column compared with or assigned 0/1. On Databricks BIT is BOOLEAN and ANSI mode rejects BOOLEAN = INT; rewritten to TRUE/FALSE and CAST(... AS BOOLEAN). | bit_to_boolean |

## Converted automatically

| Rule | Construct | Lines | How |
|---|---|---|---|
| EX003 | case_expression | 24, 52, 60 | CASE and IIF translate directly (IIF becomes IF). |
| EX004 | date_function | 37, 44 | Date functions map to Databricks equivalents (DATEADD, LAST_DAY, MAKE_DATE, ...). |
| ST004 | parameters | 11 | 1 input parameter(s) become dbt vars and notebook widgets. |
| ST006 | session_option | 15 | SET NOCOUNT is a SQL Server session setting with no Databricks equivalent; dropped. |

## Conversion plan

| # | Step | T-SQL line | Kind | Fixes applied |
|---|---|---|---|---|
| 1 | manual block | 24 | manual | - |

### Step 1: manual conversion (T-SQL line 24)

- CF001: Cursor loops process one row at a time. Spark has no cursors; the loop body must be re-expressed as set-based logic (joins, window functions, aggregates).
- open cursor has no automatic conversion
- fetch cursor has no automatic conversion
- CF002 (line 35): Procedural loop. Its termination and per-iteration state cannot be translated mechanically; rewrite set-based or as a driver loop in Python.
- ST005 (line 35): @@FETCH_STATUS: Cursor loop state; it disappears once the cursor is rewritten set-based.
- CF008 (line 51): Branch depends on table data (dbo.product_reorder_points) evaluated at run time. dbt models cannot branch on data; express it as a set-based condition or MERGE.
- close cursor has no automatic conversion
- deallocate cursor has no automatic conversion

```sql
DECLARE product_cursor CURSOR LOCAL FAST_FORWARD FOR
        SELECT
            p.product_id,
            p.lead_time_days,
            CASE WHEN p.is_cold_chain = 1 THEN 10 ELSE 7 END
        FROM dbo.products AS p
        WHERE p.is_active = 1
OPEN product_cursor
FETCH NEXT FROM product_cursor INTO @product_id, @lead_time_days, @safety_days
WHILE @@FETCH_STATUS = 0
    BEGIN
        SELECT @avg_daily_units = ISNULL(SUM(d.quantity), 0) / 90.0
        FROM dbo.dispenses AS d
        WHERE d.product_id = @product_id
          AND d.status = 'DISPENSED'
          AND d.dispense_date >  DATEADD(DAY, -90, @as_of_date)
          AND d.dispense_date <= @as_of_date;

        SELECT @on_hand = ISNULL(SUM(s.closing_qty), 0)
        FROM dbo.inventory_snapshots AS s
        WHERE s.product_id = @product_id
          AND s.snapshot_month = EOMONTH(@as_of_date, -1);

        SET @reorder_point = CEILING(@avg_daily_units * (@lead_time_days + @safety_days));

        IF EXISTS (SELECT 1 FROM dbo.product_reorder_points WHERE product_id = @product_id)
            UPDATE dbo.product_reorder_points
            SET avg_daily_units = @avg_daily_units,
                reorder_point   = @reorder_point,
                on_hand_qty     = @on_hand,
                needs_reorder   = CASE WHEN @on_hand <= @reorder_point THEN 1 ELSE 0 END,
                calculated_at   = GETDATE()
            WHERE product_id = @product_id;
        ELSE
            INSERT INTO dbo.product_reorder_points
                (product_id, avg_daily_units, reorder_point, on_hand_qty, needs_reorder, calculated_at)
            VALUES
                (@product_id, @avg_daily_units, @reorder_point, @on_hand,
                 CASE WHEN @on_hand <= @reorder_point THEN 1 ELSE 0 END, GETDATE());

        FETCH NEXT FROM product_cursor INTO @product_id, @lead_time_days, @safety_days;
    END
CLOSE product_cursor
DEALLOCATE product_cursor
```

## Dropped

Statements with no Databricks equivalent needed:

- line 15: session option with no Databricks equivalent
