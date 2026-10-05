SELECT p.SHEET_KEY, p.[5], p.[8], p.[10]
FROM (
    SELECT r.SHEET_KEY, r.OBS_TYPE_KEY, TRY_CAST(r.READ_VALUE AS float) AS val
    FROM   OBS_READING r
    WHERE  r.OBS_TYPE_KEY IN ('5', '8', '10')
) src
PIVOT (AVG(val) FOR OBS_TYPE_KEY IN ([5], [8], [10])) p;
