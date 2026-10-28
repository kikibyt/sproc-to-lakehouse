/*
    Synthetic sample procedure (fictional specialty pharmacy). Not real business logic.

    Classifies adjudicated claims into coverage segments, outcomes, copay tiers and
    follow-up priority for the reimbursement team.
    Written in legacy style without statement terminators, as much older code is.
    Constructs: CASE-heavy business rules (searched, simple and nested CASE), IIF,
    decimal division, CAST to DATE, BETWEEN on parameters.
*/
CREATE PROCEDURE dbo.usp_classify_payer_claims
    @from_date DATE,
    @to_date   DATE
AS
BEGIN
    SET NOCOUNT ON

    DELETE FROM dbo.claim_classification
    WHERE submitted_date BETWEEN @from_date AND @to_date

    INSERT INTO dbo.claim_classification (
        claim_id, dispense_id, submitted_date, payer_type, coverage_segment, claim_outcome,
        copay_tier, paid_ratio, follow_up_priority, high_oop_flag, classified_at
    )
    SELECT
        c.claim_id,
        c.dispense_id,
        CAST(c.submitted_at AS DATE),
        py.payer_type,
        CASE
            WHEN py.payer_type = 'CASH'                                   THEN 'SELF_PAY'
            WHEN py.payer_type = 'MANUFACTURER_ASSIST'                    THEN 'ASSISTANCE'
            WHEN py.payer_type = 'GOVERNMENT'
                 AND c.plan_type IN ('PART_D', 'MANAGED_GOV')             THEN 'GOVERNMENT_MANAGED'
            WHEN py.payer_type = 'GOVERNMENT'                             THEN 'GOVERNMENT_OTHER'
            WHEN py.payer_type = 'COMMERCIAL' AND c.plan_type = 'HDHP'    THEN 'COMMERCIAL_HDHP'
            ELSE 'COMMERCIAL_STANDARD'
        END,
        CASE c.response_code
            WHEN 'P' THEN
                CASE WHEN c.paid_amount < c.billed_amount * 0.5 THEN 'PAID_SHORT' ELSE 'PAID' END
            WHEN 'D' THEN 'DUPLICATE'
            WHEN 'R' THEN
                CASE
                    WHEN c.reject_code IN ('70', '75', '76') THEN 'REJECT_COVERAGE'
                    WHEN c.reject_code IN ('79', '88')       THEN 'REJECT_TIMING'
                    WHEN c.reject_code IS NULL               THEN 'REJECT_UNSPECIFIED'
                    ELSE 'REJECT_OTHER'
                END
            ELSE 'UNKNOWN'
        END,
        CASE
            WHEN c.patient_pay_amount = 0    THEN 'TIER_0'
            WHEN c.patient_pay_amount <= 10  THEN 'TIER_1'
            WHEN c.patient_pay_amount <= 50  THEN 'TIER_2'
            WHEN c.patient_pay_amount <= 250 THEN 'TIER_3'
            ELSE 'TIER_4'
        END,
        CASE WHEN c.billed_amount > 0
             THEN CAST(c.paid_amount / c.billed_amount AS DECIMAL(9,4))
        END,
        CASE
            WHEN c.response_code = 'R' AND c.reject_code = '75' THEN 'HIGH'
            WHEN c.response_code = 'R' AND py.payer_type = 'GOVERNMENT' THEN 'HIGH'
            WHEN c.response_code = 'R' THEN 'MEDIUM'
            WHEN c.response_code = 'P' AND c.paid_amount < c.billed_amount * 0.5 THEN 'MEDIUM'
            ELSE 'NONE'
        END,
        IIF(c.patient_pay_amount > 100, 1, 0),
        GETDATE()
    FROM dbo.claims c
    JOIN dbo.payers py ON py.payer_id = c.payer_id
    WHERE CAST(c.submitted_at AS DATE) BETWEEN @from_date AND @to_date
END
GO
