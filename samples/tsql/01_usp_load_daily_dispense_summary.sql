/*
    Synthetic sample procedure (fictional specialty pharmacy). Not real business logic.

    Rebuilds one day of the daily dispense summary by therapy area.
    Constructs: parameter default logic, CTE chain, aggregation, DATEADD,
    CONVERT with a style code (112 = yyyymmdd), NULLIF.
*/
CREATE OR ALTER PROCEDURE dbo.usp_load_daily_dispense_summary
    @run_date DATE = NULL
AS
BEGIN
    SET NOCOUNT ON;

    -- Default to yesterday when the scheduler does not pass a date
    IF @run_date IS NULL
        SET @run_date = CAST(DATEADD(DAY, -1, GETDATE()) AS DATE);

    -- Idempotent reload: clear the day first
    DELETE FROM dbo.daily_dispense_summary
    WHERE summary_date = @run_date;

    WITH dispensed AS (
        SELECT
            d.dispense_date,
            d.patient_id,
            p.therapy_area,
            d.quantity,
            d.days_supply,
            d.billed_amount,
            d.paid_amount
        FROM dbo.dispenses AS d
        INNER JOIN dbo.products AS p
            ON p.product_id = d.product_id
        WHERE d.dispense_date = @run_date
          AND d.status = 'DISPENSED'
    ),
    by_area AS (
        SELECT
            dispense_date,
            therapy_area,
            COUNT(*)                   AS dispense_count,
            COUNT(DISTINCT patient_id) AS patient_count,
            SUM(quantity)              AS total_quantity,
            SUM(days_supply)           AS total_days_supply,
            SUM(billed_amount)         AS total_billed,
            SUM(paid_amount)           AS total_paid
        FROM dispensed
        GROUP BY dispense_date, therapy_area
    )
    INSERT INTO dbo.daily_dispense_summary (
        summary_date, summary_date_key, therapy_area, dispense_count, patient_count,
        total_quantity, total_days_supply, total_billed, total_paid, paid_to_billed_pct, loaded_at
    )
    SELECT
        dispense_date,
        CONVERT(CHAR(8), dispense_date, 112),
        therapy_area,
        dispense_count,
        patient_count,
        total_quantity,
        total_days_supply,
        total_billed,
        total_paid,
        CAST(ROUND(total_paid * 100.0 / NULLIF(total_billed, 0), 2) AS DECIMAL(7,2)),
        GETDATE()
    FROM by_area;
END
GO
