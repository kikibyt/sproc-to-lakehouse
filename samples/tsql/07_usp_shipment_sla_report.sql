/*
    Synthetic sample procedure (fictional specialty pharmacy). Not real business logic.

    Weekly shipment service-level report by region and ship method. Late shipments
    are collected in a table variable first, then joined back to all shipments.
    Returns a result set to the caller (used by a reporting tool).
    Constructs: table variable, date arithmetic (DATEADD, DATEDIFF in days,
    DATEPART ISO_WEEK), CASE buckets, LEFT JOIN, ORDER BY on a returned result set.
*/
CREATE OR ALTER PROCEDURE dbo.usp_shipment_sla_report
    @week_start DATE
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @week_end DATE = DATEADD(DAY, 6, @week_start);

    DECLARE @late TABLE (
        shipment_id            BIGINT  NOT NULL PRIMARY KEY,
        promised_delivery_date DATE    NOT NULL,
        delivered_date         DATE    NULL,
        days_late              INT     NOT NULL,
        requires_cold_chain    BIT     NOT NULL
    );

    INSERT INTO @late (shipment_id, promised_delivery_date, delivered_date, days_late, requires_cold_chain)
    SELECT
        s.shipment_id,
        s.promised_delivery_date,
        CAST(s.delivered_at AS DATE),
        DATEDIFF(DAY, s.promised_delivery_date, ISNULL(CAST(s.delivered_at AS DATE), @week_end)),
        s.requires_cold_chain
    FROM dbo.shipments AS s
    WHERE s.shipped_at >= @week_start
      AND s.shipped_at <  DATEADD(DAY, 7, @week_start)
      AND (s.delivered_at IS NULL OR CAST(s.delivered_at AS DATE) > s.promised_delivery_date);

    SELECT
        DATEPART(ISO_WEEK, @week_start)                                   AS iso_week,
        st.region_code,
        s.ship_method,
        COUNT(*)                                                          AS shipped_count,
        SUM(CASE WHEN l.shipment_id IS NULL THEN 1 ELSE 0 END)            AS on_time_count,
        SUM(CASE WHEN l.days_late BETWEEN 1 AND 2 THEN 1 ELSE 0 END)      AS late_1_2_days,
        SUM(CASE WHEN l.days_late >= 3 THEN 1 ELSE 0 END)                 AS late_3_plus_days,
        SUM(CASE WHEN l.requires_cold_chain = 1 THEN 1 ELSE 0 END)        AS cold_chain_late,
        CAST(100.0 * SUM(CASE WHEN l.shipment_id IS NULL THEN 1 ELSE 0 END)
             / NULLIF(COUNT(*), 0) AS DECIMAL(5,2))                       AS on_time_pct
    FROM dbo.shipments AS s
    INNER JOIN dbo.sites AS st
        ON st.site_id = s.site_id
    LEFT JOIN @late AS l
        ON l.shipment_id = s.shipment_id
    WHERE s.shipped_at >= @week_start
      AND s.shipped_at <  DATEADD(DAY, 7, @week_start)
    GROUP BY st.region_code, s.ship_method
    ORDER BY st.region_code, s.ship_method;
END
GO
