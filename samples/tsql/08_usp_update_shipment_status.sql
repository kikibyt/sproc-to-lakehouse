/*
    Synthetic sample procedure (fictional specialty pharmacy). Not real business logic.

    Applies carrier tracking events to shipment status in three ordered steps and
    logs how many rows each step changed. Step order matters: a shipment marked
    DELIVERED in step 1 is excluded from step 2 by its WHERE clause.
    Constructs: multi-step UPDATE ... FROM ... JOIN, CTE feeding an UPDATE,
    ROW_NUMBER, @@ROWCOUNT captured into variables, multi-row INSERT ... VALUES.
*/
CREATE OR ALTER PROCEDURE dbo.usp_update_shipment_status
    @as_of DATETIME2(0) = NULL
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @rows_delivered INT = 0,
            @rows_exception INT = 0,
            @rows_delayed   INT = 0;

    SET @as_of = ISNULL(@as_of, SYSDATETIME());

    -- Step 1: shipments whose latest event is a delivery
    WITH latest AS (
        SELECT
            e.shipment_id,
            e.event_code,
            e.event_ts,
            ROW_NUMBER() OVER (PARTITION BY e.shipment_id ORDER BY e.event_ts DESC, e.event_id DESC) AS rn
        FROM dbo.carrier_tracking_events AS e
        WHERE e.event_ts <= @as_of
    )
    UPDATE s
    SET s.status             = 'DELIVERED',
        s.delivered_at       = l.event_ts,
        s.last_status_update = @as_of
    FROM dbo.shipments AS s
    INNER JOIN latest AS l
        ON l.shipment_id = s.shipment_id
       AND l.rn = 1
    WHERE l.event_code = 'DL'
      AND s.status <> 'DELIVERED';

    SET @rows_delivered = @@ROWCOUNT;

    -- Step 2: any carrier exception on a shipment that is not yet delivered
    UPDATE s
    SET s.status             = 'EXCEPTION',
        s.last_status_update = @as_of
    FROM dbo.shipments AS s
    INNER JOIN dbo.carrier_tracking_events AS e
        ON e.shipment_id = s.shipment_id
    WHERE e.event_code = 'EX'
      AND e.event_ts <= @as_of
      AND s.status NOT IN ('DELIVERED', 'EXCEPTION');

    SET @rows_exception = @@ROWCOUNT;

    -- Step 3: still moving but past the promised date
    UPDATE dbo.shipments
    SET status             = 'DELAYED',
        last_status_update = @as_of
    WHERE status IN ('SHIPPED', 'IN_TRANSIT')
      AND promised_delivery_date < CAST(@as_of AS DATE);

    SET @rows_delayed = @@ROWCOUNT;

    INSERT INTO dbo.etl_run_log (proc_name, step_name, status, rows_affected, started_at, finished_at)
    VALUES
        ('usp_update_shipment_status', 'mark_delivered', 'SUCCEEDED', @rows_delivered, @as_of, SYSDATETIME()),
        ('usp_update_shipment_status', 'mark_exception', 'SUCCEEDED', @rows_exception, @as_of, SYSDATETIME()),
        ('usp_update_shipment_status', 'mark_delayed',   'SUCCEEDED', @rows_delayed,   @as_of, SYSDATETIME());
END
GO
