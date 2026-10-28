/*
    Synthetic sample procedure (fictional specialty pharmacy). Not real business logic.

    Monthly prescriber rollup. A temp table is created, filled, then mutated by
    several UPDATE steps before being published: the classic "scratchpad" pattern.
    Constructs: CREATE TABLE #temp, INSERT into it, UPDATE ... FROM against it
    (three times), DENSE_RANK in a CTE feeding an UPDATE, ROW_NUMBER first-fill
    detection, DELETE + INSERT publish, explicit DROP TABLE.
*/
CREATE OR ALTER PROCEDURE dbo.usp_rebuild_prescriber_rollup
    @period_month DATE
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @period_start DATE = DATEADD(DAY, 1, EOMONTH(@period_month, -1));
    DECLARE @period_end   DATE = EOMONTH(@period_month);

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
    );

    -- Step 1: base activity for the month
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
    GROUP BY pr.prescriber_id, pr.region_code;

    -- Step 2: patients whose first-ever fill happened this month
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
        ON n.prescriber_id = r.prescriber_id;

    UPDATE #rollup
    SET new_patient_count = 0
    WHERE new_patient_count IS NULL;

    -- Step 3: rank prescribers within their region by paid amount
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
        ON k.prescriber_id = r.prescriber_id;

    -- Step 4: publish
    DELETE FROM dbo.prescriber_monthly_rollup
    WHERE period_month = @period_end;

    INSERT INTO dbo.prescriber_monthly_rollup (
        period_month, prescriber_id, region_code, patient_count, new_patient_count,
        dispense_count, total_days_supply, total_paid, rank_in_region, prescriber_tier
    )
    SELECT
        @period_end, prescriber_id, region_code, patient_count, new_patient_count,
        dispense_count, total_days_supply, total_paid, rank_in_region, prescriber_tier
    FROM #rollup;

    DROP TABLE #rollup;
END
GO
