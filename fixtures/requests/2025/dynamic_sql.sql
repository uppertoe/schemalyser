DECLARE @sql nvarchar(max);
DECLARE @col sysname = N'RISK_GRADE_CAT';
SET @sql = N'SELECT ' + QUOTENAME(@col) + N', COUNT(*) AS n FROM dbo.ANAES_RECORD GROUP BY ' + QUOTENAME(@col);
EXEC sp_executesql @sql;
