/*
    Synthetic sample procedure (fictional specialty pharmacy). Not real business logic.

    Recalculates reorder points one product at a time with a cursor: 90-day average
    daily usage times (lead time + safety days), compared with last month-end stock.
    This is a deliberately row-by-row design that a set-based rewrite should replace.
    Constructs: DECLARE CURSOR / OPEN / FETCH / CLOSE / DEALLOCATE,
    WHILE @@FETCH_STATUS = 0, SELECT @var = aggregate, IF EXISTS ... UPDATE ... ELSE INSERT,
    CEILING, EOMONTH with offset.
*/
CREATE OR ALTER PROCEDURE dbo.usp_recalc_reorder_points_cursor
    @as_of_date DATE
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @product_id      INT,
            @lead_time_days  INT,
            @safety_days     INT,
            @avg_daily_units DECIMAL(12,4),
            @on_hand         DECIMAL(12,2),
            @reorder_point   DECIMAL(12,2);

    DECLARE product_cursor CURSOR LOCAL FAST_FORWARD FOR
        SELECT
            p.product_id,
            p.lead_time_days,
            CASE WHEN p.is_cold_chain = 1 THEN 10 ELSE 7 END
        FROM dbo.products AS p
        WHERE p.is_active = 1;

    OPEN product_cursor;
    FETCH NEXT FROM product_cursor INTO @product_id, @lead_time_days, @safety_days;

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

    CLOSE product_cursor;
    DEALLOCATE product_cursor;
END
GO
