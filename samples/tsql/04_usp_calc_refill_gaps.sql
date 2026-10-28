/*
    Synthetic sample procedure (fictional specialty pharmacy). Not real business logic.

    Finds gaps between a fill running out and the next fill of the same product,
    and categorises them. Full rebuild on every run.
    Constructs: TRUNCATE + INSERT, CTE chain, ROW_NUMBER / LEAD, DATEADD and
    DATEDIFF in days, CASE thresholds driven by a parameter.
*/
CREATE OR ALTER PROCEDURE dbo.usp_calc_refill_gaps
    @as_of_date         DATE,
    @gap_threshold_days INT = 7
AS
BEGIN
    SET NOCOUNT ON;

    TRUNCATE TABLE dbo.refill_gap_events;

    WITH fills AS (
        SELECT
            d.patient_id,
            d.product_id,
            d.dispense_date,
            DATEADD(DAY, d.days_supply, d.dispense_date) AS supply_end_date,
            ROW_NUMBER() OVER (
                PARTITION BY d.patient_id, d.product_id
                ORDER BY d.dispense_date, d.dispense_id
            ) AS fill_seq,
            LEAD(d.dispense_date) OVER (
                PARTITION BY d.patient_id, d.product_id
                ORDER BY d.dispense_date, d.dispense_id
            ) AS next_dispense_date
        FROM dbo.dispenses AS d
        WHERE d.status = 'DISPENSED'
          AND d.dispense_date <= @as_of_date
    ),
    gaps AS (
        SELECT
            patient_id,
            product_id,
            fill_seq,
            dispense_date,
            supply_end_date,
            next_dispense_date,
            DATEDIFF(DAY, supply_end_date, ISNULL(next_dispense_date, @as_of_date)) AS gap_days
        FROM fills
    )
    INSERT INTO dbo.refill_gap_events (
        patient_id, product_id, fill_seq, dispense_date, supply_end_date,
        next_dispense_date, gap_days, gap_category, is_open_gap, calculated_at
    )
    SELECT
        patient_id,
        product_id,
        fill_seq,
        dispense_date,
        supply_end_date,
        next_dispense_date,
        gap_days,
        CASE
            WHEN gap_days <= 0                   THEN 'ON_TIME'
            WHEN gap_days <= @gap_threshold_days THEN 'MINOR_GAP'
            WHEN gap_days <= 30                  THEN 'MAJOR_GAP'
            ELSE 'LAPSED'
        END,
        CASE WHEN next_dispense_date IS NULL THEN 1 ELSE 0 END,
        GETDATE()
    FROM gaps
    WHERE gap_days > 0
       OR next_dispense_date IS NULL;
END
GO
