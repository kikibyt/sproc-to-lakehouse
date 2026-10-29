-- Composite primary key of dbo.daily_dispense_summary must stay unique.
SELECT summary_date, therapy_area, COUNT(*) AS n
FROM {{ ref('daily_dispense_summary') }}
GROUP BY summary_date, therapy_area
HAVING COUNT(*) > 1
