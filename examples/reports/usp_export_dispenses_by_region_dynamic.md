# dbo.usp_export_dispenses_by_region_dynamic

**Needs human review.** Partly converted: 1 step(s) need manual conversion.

- Source: `samples/tsql/11_usp_export_dispenses_by_region_dynamic.sql`
- Findings: 3 auto, 4 with notes, 2 needing review
- Parameters: `@from_date DATE`, `@to_date DATE`, `@therapy_area VARCHAR(30)` = `NULL`
- Generated: `dbt/macros/procedures/usp_export_dispenses_by_region_dynamic.sql`, `notebooks/usp_export_dispenses_by_region_dynamic.py`

## Needs human review

| Line | Rule | Why | T-SQL |
|---|---|---|---|
| 20 | EX013 | QUOTENAME(): no Databricks equivalent; sqlglot passes the call through unchanged, so the generated SQL would fail. Needs a manual rewrite. | `SELECT @cols = STRING_AGG(QUOTENAME(region_code), N', ') WITHIN GROUP (ORDER BY region_code) FROM...` |
| 41 | CF005 | SQL text is assembled at run time, so it cannot be analysed or converted statically. Rewrite with the shape known in advance (e.g. PySpark pivot() or a dbt macro that builds the column list). | `EXEC sys.sp_executesql @sql, N'@from_date DATE, @to_date DATE, @therapy_area VARCHAR(30)', @from_...` |

## Converted, with review notes

| Line | Rule | Note | Fix applied |
|---|---|---|---|
| 20 | EX014 | STRING_AGG becomes LISTAGG, which requires a recent Databricks Runtime; on older runtimes use array_join(collect_list(...)). Check your runtime version. | - |
| 20 | EX015 | BIT column compared with or assigned 0/1. On Databricks BIT is BOOLEAN and ANSI mode rejects BOOLEAN = INT; rewritten to TRUE/FALSE and CAST(... AS BOOLEAN). | bit_to_boolean |
| 20 | ST003 | Variable is loaded from a query. The notebook collects the value at this point; dbt inlines it as a scalar subquery, evaluated when the model runs. | - |
| 23 | EX010 | '+' concatenates strings in SQL Server but is numeric addition in Databricks (sqlglot leaves it unchanged, which yields NULL or an error); rewritten to CONCAT. NULL handling is the same: any NULL gives NULL. | plus_to_concat |

## Converted automatically

| Rule | Construct | Lines | How |
|---|---|---|---|
| EX003 | case_expression | 23 | CASE and IIF translate directly (IIF becomes IF). |
| ST004 | parameters | 9 | 3 input parameter(s) become dbt vars and notebook widgets. |
| ST006 | session_option | 15 | SET NOCOUNT is a SQL Server session setting with no Databricks equivalent; dropped. |

## Conversion plan

| # | Step | T-SQL line | Kind | Fixes applied |
|---|---|---|---|---|
| 1 | manual block | 20 | manual | - |

### Step 1: manual conversion (T-SQL line 20)

- EX013: QUOTENAME(): no Databricks equivalent; sqlglot passes the call through unchanged, so the generated SQL would fail. Needs a manual rewrite.
- CF005: SQL text is assembled at run time, so it cannot be analysed or converted statically. Rewrite with the shape known in advance (e.g. PySpark pivot() or a dbt macro that builds the column list).

```sql
SELECT @cols = STRING_AGG(QUOTENAME(region_code), N', ') WITHIN GROUP (ORDER BY region_code)
    FROM (SELECT DISTINCT region_code FROM dbo.sites WHERE is_active = 1) AS r
EXEC sys.sp_executesql
        @sql,
        N'@from_date DATE, @to_date DATE, @therapy_area VARCHAR(30)',
        @from_date    = @from_date,
        @to_date      = @to_date,
        @therapy_area = @therapy_area
```

## Dropped

Statements with no Databricks equivalent needed:

- line 15: session option with no Databricks equivalent
