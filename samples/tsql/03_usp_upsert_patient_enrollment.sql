/*
    Synthetic sample procedure (fictional specialty pharmacy). Not real business logic.

    Upserts one staging batch of program enrollments into the enrollment dimension.
    Staging may contain several rows per patient/program; the latest one wins.
    Constructs: MERGE with conditional WHEN MATCHED, ROW_NUMBER de-duplication,
    CONVERT / TRY_CONVERT with style 101 (MM/DD/YYYY), ISNULL, NULLIF, COALESCE,
    LTRIM/RTRIM/UPPER/LOWER.
*/
CREATE OR ALTER PROCEDURE dbo.usp_upsert_patient_enrollment
    @load_batch_id INT
AS
BEGIN
    SET NOCOUNT ON;

    MERGE dbo.dim_patient_enrollment AS tgt
    USING (
        SELECT
            patient_id,
            program_code,
            enrollment_status,
            enrolled_date,
            disenrolled_date,
            payer_id,
            case_manager_email
        FROM (
            SELECT
                s.patient_id,
                UPPER(LTRIM(RTRIM(s.program_code)))                                AS program_code,
                ISNULL(NULLIF(UPPER(LTRIM(RTRIM(s.enrollment_status))), ''), 'PENDING') AS enrollment_status,
                CONVERT(DATE, s.enrolled_on, 101)                                  AS enrolled_date,
                TRY_CONVERT(DATE, s.disenrolled_on, 101)                           AS disenrolled_date,
                COALESCE(s.payer_id, -1)                                           AS payer_id,
                LOWER(LTRIM(RTRIM(s.case_manager_email)))                          AS case_manager_email,
                ROW_NUMBER() OVER (
                    PARTITION BY s.patient_id, UPPER(LTRIM(RTRIM(s.program_code)))
                    ORDER BY s.loaded_at DESC, s.stg_row_id DESC
                ) AS rn
            FROM dbo.stg_patient_enrollments AS s
            WHERE s.load_batch_id = @load_batch_id
              AND s.program_code IS NOT NULL
        ) AS ranked
        WHERE rn = 1
    ) AS src
        ON  tgt.patient_id = src.patient_id
        AND tgt.program_code = src.program_code
    WHEN MATCHED AND (
            tgt.enrollment_status <> src.enrollment_status
         OR ISNULL(tgt.disenrolled_date, '19000101') <> ISNULL(src.disenrolled_date, '19000101')
         OR tgt.payer_id <> src.payer_id
    ) THEN
        UPDATE SET
            tgt.enrollment_status  = src.enrollment_status,
            tgt.disenrolled_date   = src.disenrolled_date,
            tgt.payer_id           = src.payer_id,
            tgt.case_manager_email = src.case_manager_email,
            tgt.is_active          = CASE WHEN src.disenrolled_date IS NULL THEN 1 ELSE 0 END,
            tgt.updated_at         = SYSDATETIME()
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (patient_id, program_code, enrollment_status, enrolled_date, disenrolled_date,
                payer_id, case_manager_email, is_active, created_at, updated_at)
        VALUES (src.patient_id, src.program_code, src.enrollment_status, src.enrolled_date,
                src.disenrolled_date, src.payer_id, src.case_manager_email,
                CASE WHEN src.disenrolled_date IS NULL THEN 1 ELSE 0 END,
                SYSDATETIME(), SYSDATETIME());
END
GO
