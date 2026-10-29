# dbo.usp_rebuild_prescriber_rollup

**Needs human review.** Partly converted: 2 step(s) need manual conversion.

- Source: `samples/tsql/09_usp_rebuild_prescriber_rollup.sql`
- Findings: 9 auto, 2 with notes, 1 needing review
- Parameters: `@period_month DATE`
- Generated: `dbt/macros/procedures/usp_rebuild_prescriber_rollup.sql`, `notebooks/usp_rebuild_prescriber_rollup.py`

## Needs human review

| Line | Rule | Why | T-SQL |
|---|---|---|---|
| 32 | ST001 | #rollup is modified after it is populated (UPDATE/DELETE at line 48, 68, 73). Step-by-step mutation of intermediate state has to be re-expressed as a chain of CTEs/models, each deriving the next version of the data. | `INSERT INTO #rollup (prescriber_id, region_code, patient_count, dispense_count, total_days_supply...` |

## Converted, with review notes

| Line | Rule | Note | Fix applied |
|---|---|---|---|
| 48 | DM002 | Delta UPDATE/DELETE has no FROM/JOIN clause; rewritten as MERGE INTO with the join as the source. If the join matches a target row more than once, SQL Server picks one silently but Delta MERGE fails. | update_from_to_merge |
| 73 | DM002 | Delta UPDATE/DELETE has no FROM/JOIN clause; rewritten as MERGE INTO with the join as the source. If the join matches a target row more than once, SQL Server picks one silently but Delta MERGE fails. | update_from_to_merge |

## Converted automatically

| Rule | Construct | Lines | How |
|---|---|---|---|
| EX001 | cte | 73 | CTEs are supported natively. |
| EX002 | window_function | 48, 73 | Window functions and frames translate directly. |
| EX003 | case_expression | 73 | CASE and IIF translate directly (IIF becomes IF). |
| EX004 | date_function | 16, 17 | Date functions map to Databricks equivalents (DATEADD, LAST_DAY, MAKE_DATE, ...). |
| ST004 | parameters | 10 | 1 input parameter(s) become dbt vars and notebook widgets. |
| ST006 | session_option | 14 | SET NOCOUNT is a SQL Server session setting with no Databricks equivalent; dropped. |
| ST008 | temp_cleanup | 101 | Temp table cleanup has no equivalent (CTEs and views are scoped to the run); removed. |

## Conversion plan

| # | Step | T-SQL line | Kind | Fixes applied |
|---|---|---|---|---|
| 1 | manual block | 19 | manual | - |
| 2 | delete dbo.prescriber_monthly_rollup | 89 | write | - |
| 3 | insert statement | 92 | manual | - |

### Step 1: manual conversion (T-SQL line 19)

- #rollup is modified across steps (see ST001/ST002)
- ST001: #rollup is modified after it is populated (UPDATE/DELETE at line 48, 68, 73). Step-by-step mutation of intermediate state has to be re-expressed as a chain of CTEs/models, each deriving the next version of the data.

```sql
CREATE TABLE #rollup (
        prescriber_id     INT           NOT NULL PRIMARY KEY,
        region_code       VARCHAR(10)   NOT NULL,
        patient_count     INT           NOT NULL,
        dispense_count    INT           NOT NULL,
        total_days_supply INT           NOT NULL,
        total_paid        DECIMAL(14,2) NOT NULL,
        new_patient_count INT           NULL,
        rank_in_region    INT           NULL,
        prescriber_tier   VARCHAR(10)   NULL
    )
INSERT INTO #rollup (prescriber_id, region_code, patient_count, dispense_count, total_days_supply, total_paid)
    SELECT
        pr.prescriber_id,
        pr.region_code,
        COUNT(DISTINCT d.patient_id),
        COUNT(*),
        SUM(d.days_supply),
        SUM(d.paid_amount)
    FROM dbo.dispenses AS d
    INNER JOIN dbo.prescribers AS pr
        ON pr.prescriber_id = d.prescriber_id
    WHERE d.dispense_date BETWEEN @period_start AND @period_end
      AND d.status = 'DISPENSED'
    GROUP BY pr.prescriber_id, pr.region_code
UPDATE r
    SET r.new_patient_count = n.new_patients
    FROM #rollup AS r
    INNER JOIN (
        SELECT first_fill.prescriber_id, COUNT(*) AS new_patients
        FROM (
            SELECT
                d.patient_id,
                d.prescriber_id,
                d.dispense_date,
                ROW_NUMBER() OVER (PARTITION BY d.patient_id ORDER BY d.dispense_date, d.dispense_id) AS rn
            FROM dbo.dispenses AS d
            WHERE d.status = 'DISPENSED'
        ) AS first_fill
        WHERE first_fill.rn = 1
          AND first_fill.dispense_date BETWEEN @period_start AND @period_end
        GROUP BY first_fill.prescriber_id
    ) AS n
        ON n.prescriber_id = r.prescriber_id
UPDATE #rollup
    SET new_patient_count = 0
    WHERE new_patient_count IS NULL
WITH ranked AS (
        SELECT
            prescriber_id,
            DENSE_RANK() OVER (PARTITION BY region_code ORDER BY total_paid DESC) AS rnk
        FROM #rollup
    )
    UPDATE r
    SET r.rank_in_region  = k.rnk,
        r.prescriber_tier = CASE WHEN k.rnk <= 5  THEN 'TOP'
                                 WHEN k.rnk <= 20 THEN 'HIGH'
                                 ELSE 'STANDARD' END
    FROM #rollup AS r
    INNER JOIN ranked AS k
        ON k.prescriber_id = r.prescriber_id
```

### Step 3: manual conversion (T-SQL line 92)

- #rollup is modified across steps (see ST001/ST002)

```sql
INSERT INTO dbo.prescriber_monthly_rollup (
        period_month, prescriber_id, region_code, patient_count, new_patient_count,
        dispense_count, total_days_supply, total_paid, rank_in_region, prescriber_tier
    )
    SELECT
        @period_end, prescriber_id, region_code, patient_count, new_patient_count,
        dispense_count, total_days_supply, total_paid, rank_in_region, prescriber_tier
    FROM #rollup
```

## Dropped

Statements with no Databricks equivalent needed:

- line 14: session option with no Databricks equivalent
- line 101: temp table cleanup is not needed (CTEs/views are scoped to the run)
