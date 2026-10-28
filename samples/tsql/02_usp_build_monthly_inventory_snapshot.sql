/*
    Synthetic sample procedure (fictional specialty pharmacy). Not real business logic.

    Builds the month-end inventory snapshot per site and product by rolling the
    previous month's closing quantity forward with this month's movements, then
    back-fills a three-month rolling average of dispensed units.
    Constructs: DATEFROMPARTS, EOMONTH (with offset), temp table populated once and
    read once, FULL OUTER JOIN, ISNULL, multi-step INSERT then UPDATE ... FROM,
    window function with a ROWS frame.
*/
CREATE OR ALTER PROCEDURE dbo.usp_build_monthly_inventory_snapshot
    @snapshot_month DATE
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @month_start    DATE = DATEFROMPARTS(YEAR(@snapshot_month), MONTH(@snapshot_month), 1);
    DECLARE @month_end      DATE = EOMONTH(@snapshot_month);
    DECLARE @prev_month_end DATE = EOMONTH(@snapshot_month, -1);

    IF OBJECT_ID('tempdb..#movements') IS NOT NULL
        DROP TABLE #movements;

    -- Step 1: net movements for the month
    SELECT
        m.site_id,
        m.product_id,
        SUM(CASE WHEN m.movement_type = 'RECEIPT'  THEN m.quantity ELSE 0 END) AS received_qty,
        SUM(CASE WHEN m.movement_type = 'DISPENSE' THEN m.quantity ELSE 0 END) AS dispensed_qty,
        SUM(CASE WHEN m.movement_type IN ('ADJUSTMENT', 'RETURN') THEN m.quantity ELSE 0 END) AS adjusted_qty
    INTO #movements
    FROM dbo.inventory_movements AS m
    WHERE m.movement_date BETWEEN @month_start AND @month_end
    GROUP BY m.site_id, m.product_id;

    -- Step 2: replace this month's snapshot
    DELETE FROM dbo.inventory_snapshots
    WHERE snapshot_month = @month_end;

    INSERT INTO dbo.inventory_snapshots (
        snapshot_month, site_id, product_id, opening_qty, received_qty,
        dispensed_qty, adjusted_qty, closing_qty, avg_dispensed_3m, loaded_at
    )
    SELECT
        @month_end,
        COALESCE(mv.site_id, prev.site_id),
        COALESCE(mv.product_id, prev.product_id),
        ISNULL(prev.closing_qty, 0),
        ISNULL(mv.received_qty, 0),
        ISNULL(mv.dispensed_qty, 0),
        ISNULL(mv.adjusted_qty, 0),
        ISNULL(prev.closing_qty, 0) + ISNULL(mv.received_qty, 0)
            - ISNULL(mv.dispensed_qty, 0) + ISNULL(mv.adjusted_qty, 0),
        NULL,
        SYSDATETIME()
    FROM #movements AS mv
    FULL OUTER JOIN (
        SELECT site_id, product_id, closing_qty
        FROM dbo.inventory_snapshots
        WHERE snapshot_month = @prev_month_end
    ) AS prev
        ON prev.site_id = mv.site_id
       AND prev.product_id = mv.product_id;

    -- Step 3: three-month rolling average of dispensed units
    WITH trend AS (
        SELECT
            site_id,
            product_id,
            snapshot_month,
            AVG(dispensed_qty) OVER (
                PARTITION BY site_id, product_id
                ORDER BY snapshot_month
                ROWS BETWEEN 2 PRECEDING AND CURRENT ROW
            ) AS avg_dispensed_3m
        FROM dbo.inventory_snapshots
        WHERE snapshot_month BETWEEN EOMONTH(@snapshot_month, -2) AND @month_end
    )
    UPDATE s
    SET s.avg_dispensed_3m = t.avg_dispensed_3m
    FROM dbo.inventory_snapshots AS s
    INNER JOIN trend AS t
        ON t.site_id = s.site_id
       AND t.product_id = s.product_id
       AND t.snapshot_month = s.snapshot_month
    WHERE s.snapshot_month = @month_end;
END
GO
