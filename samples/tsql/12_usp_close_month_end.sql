/*
    Synthetic sample procedure (fictional specialty pharmacy). Not real business logic.

    Month-end close: records period totals and locks the period's dispenses in one
    transaction, then archives dispenses older than 24 months in batches.
    Every run is logged; failures roll back, are logged and re-thrown.
    Constructs: SET XACT_ABORT, SCOPE_IDENTITY, BEGIN TRY / BEGIN CATCH,
    BEGIN TRANSACTION / COMMIT / ROLLBACK, @@TRANCOUNT, THROW (with and without
    arguments), WHILE loop with DELETE TOP (n) ... OUTPUT INTO, @@ROWCOUNT,
    ERROR_MESSAGE().
*/
CREATE OR ALTER PROCEDURE dbo.usp_close_month_end
    @period_month       DATE,
    @archive_batch_size INT = 5000
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    DECLARE @period_end     DATE = EOMONTH(@period_month);
    DECLARE @period_start   DATE = DATEADD(DAY, 1, EOMONTH(@period_month, -1));
    DECLARE @archive_before DATE = DATEADD(MONTH, -24, @period_start);
    DECLARE @rows_archived  INT = 0,
            @batch_rows     INT = 1,
            @run_id         INT;

    INSERT INTO dbo.etl_run_log (proc_name, step_name, status, started_at)
    VALUES ('usp_close_month_end', 'close_and_archive', 'RUNNING', SYSDATETIME());

    SET @run_id = SCOPE_IDENTITY();

    BEGIN TRY
        IF EXISTS (SELECT 1 FROM dbo.period_close WHERE period_month = @period_end AND close_status = 'CLOSED')
            THROW 50001, 'Period is already closed.', 1;

        BEGIN TRANSACTION;

        INSERT INTO dbo.period_close (period_month, close_status, dispense_count, total_paid, closed_at)
        SELECT @period_end, 'CLOSED', COUNT(*), SUM(d.paid_amount), SYSDATETIME()
        FROM dbo.dispenses AS d
        WHERE d.dispense_date BETWEEN @period_start AND @period_end
          AND d.status = 'DISPENSED';

        UPDATE dbo.dispenses
        SET is_period_locked = 1
        WHERE dispense_date <= @period_end
          AND is_period_locked = 0;

        COMMIT TRANSACTION;

        -- Archive outside the transaction, in batches, to keep log growth bounded
        WHILE @batch_rows > 0
        BEGIN
            DELETE TOP (@archive_batch_size)
            FROM dbo.dispenses
            OUTPUT DELETED.dispense_id, DELETED.patient_id, DELETED.product_id, DELETED.dispense_date,
                   DELETED.quantity, DELETED.paid_amount, DELETED.status
            INTO dbo.dispenses_archive (dispense_id, patient_id, product_id, dispense_date,
                                        quantity, paid_amount, status)
            WHERE dispense_date < @archive_before
              AND is_period_locked = 1;

            SET @batch_rows = @@ROWCOUNT;
            SET @rows_archived = @rows_archived + @batch_rows;
        END

        UPDATE dbo.etl_run_log
        SET status        = 'SUCCEEDED',
            rows_affected = @rows_archived,
            finished_at   = SYSDATETIME()
        WHERE run_id = @run_id;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0
            ROLLBACK TRANSACTION;

        UPDATE dbo.etl_run_log
        SET status        = 'FAILED',
            error_message = LEFT(ERROR_MESSAGE(), 4000),
            finished_at   = SYSDATETIME()
        WHERE run_id = @run_id;

        THROW;
    END CATCH
END
GO
