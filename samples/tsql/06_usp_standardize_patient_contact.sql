/*
    Synthetic sample procedure (fictional specialty pharmacy). Not real business logic.
    All patient data in this project is generated; no real person is represented.

    Rebuilds a cleaned contact table: trims names, strips phone punctuation,
    formats and masks phone numbers, normalises e-mail and postal codes.
    Constructs: string functions (LTRIM, RTRIM, UPPER, LOWER, LEFT, RIGHT, SUBSTRING,
    REPLACE, CHARINDEX, LEN, STUFF), string concatenation with '+', ISNULL, CTE.
*/
CREATE OR ALTER PROCEDURE dbo.usp_standardize_patient_contact
AS
BEGIN
    SET NOCOUNT ON;

    TRUNCATE TABLE dbo.patient_contact_clean;

    WITH cleaned AS (
        SELECT
            patient_id,
            LTRIM(RTRIM(first_name)) AS first_name,
            LTRIM(RTRIM(last_name))  AS last_name,
            REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
                ISNULL(phone_raw, ''), '(', ''), ')', ''), '-', ''), ' ', ''), '.', '') AS phone_digits,
            LOWER(LTRIM(RTRIM(email_raw)))  AS email_normalized,
            LEFT(LTRIM(RTRIM(postal_code)), 5) AS postal_code5,
            UPPER(state_code) AS state_code
        FROM dbo.patients
    )
    INSERT INTO dbo.patient_contact_clean (
        patient_id, full_name, phone_digits, phone_formatted, phone_masked,
        email_normalized, email_domain, postal_code5, state_code, contact_quality
    )
    SELECT
        patient_id,
        UPPER(LEFT(first_name, 1)) + LOWER(SUBSTRING(first_name, 2, LEN(first_name)))
            + ' ' + UPPER(last_name),
        phone_digits,
        CASE WHEN LEN(phone_digits) = 10
             THEN '(' + LEFT(phone_digits, 3) + ') ' + SUBSTRING(phone_digits, 4, 3)
                  + '-' + RIGHT(phone_digits, 4)
        END,
        CASE WHEN LEN(phone_digits) = 10 THEN STUFF(phone_digits, 4, 3, '***') END,
        email_normalized,
        CASE WHEN CHARINDEX('@', email_normalized) > 1
             THEN SUBSTRING(email_normalized, CHARINDEX('@', email_normalized) + 1, LEN(email_normalized))
        END,
        RIGHT('00000' + postal_code5, 5),
        state_code,
        CASE
            WHEN LEN(phone_digits) = 10 AND CHARINDEX('@', email_normalized) > 1 THEN 'COMPLETE'
            WHEN LEN(phone_digits) = 10 OR CHARINDEX('@', email_normalized) > 1  THEN 'PARTIAL'
            ELSE 'MISSING'
        END
    FROM cleaned;
END
GO
