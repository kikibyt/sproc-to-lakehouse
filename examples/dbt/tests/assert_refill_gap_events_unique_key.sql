-- Composite primary key of dbo.refill_gap_events must stay unique.
SELECT patient_id, product_id, fill_seq, COUNT(*) AS n
FROM {{ ref('refill_gap_events') }}
GROUP BY patient_id, product_id, fill_seq
HAVING COUNT(*) > 1
