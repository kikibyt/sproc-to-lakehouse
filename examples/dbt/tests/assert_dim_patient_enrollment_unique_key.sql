-- Composite primary key of dbo.dim_patient_enrollment must stay unique.
SELECT patient_id, program_code, COUNT(*) AS n
FROM {{ ref('dim_patient_enrollment') }}
GROUP BY patient_id, program_code
HAVING COUNT(*) > 1
