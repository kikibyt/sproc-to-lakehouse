/*
    Synthetic sample procedure (fictional specialty pharmacy). Not real business logic.

    Exports dispensed quantity per product with one column per active region. The
    region list is only known at run time, so the PIVOT is built as dynamic SQL.
    Constructs: STRING_AGG ... WITHIN GROUP, QUOTENAME, NVARCHAR(MAX) string building
    with '+', optional filter appended conditionally, parameterised sp_executesql.
*/
CREATE OR ALTER PROCEDURE dbo.usp_export_dispenses_by_region_dynamic
    @from_date    DATE,
    @to_date      DATE,
    @therapy_area VARCHAR(30) = NULL
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @cols NVARCHAR(MAX),
            @sql  NVARCHAR(MAX);

    SELECT @cols = STRING_AGG(QUOTENAME(region_code), N', ') WITHIN GROUP (ORDER BY region_code)
    FROM (SELECT DISTINCT region_code FROM dbo.sites WHERE is_active = 1) AS r;

    SET @sql = N'
SELECT product_code, ' + @cols + N'
FROM (
    SELECT p.product_code, st.region_code, d.quantity
    FROM dbo.dispenses AS d
    INNER JOIN dbo.products AS p ON p.product_id = d.product_id
    INNER JOIN dbo.sites AS st ON st.site_id = d.site_id
    WHERE d.dispense_date BETWEEN @from_date AND @to_date
      AND d.status = ''DISPENSED'''
        + CASE WHEN @therapy_area IS NOT NULL
               THEN N'
      AND p.therapy_area = @therapy_area'
               ELSE N'' END
        + N'
) AS src
PIVOT (SUM(quantity) FOR region_code IN (' + @cols + N')) AS pvt
ORDER BY product_code;';

    EXEC sys.sp_executesql
        @sql,
        N'@from_date DATE, @to_date DATE, @therapy_area VARCHAR(30)',
        @from_date    = @from_date,
        @to_date      = @to_date,
        @therapy_area = @therapy_area;
END
GO
