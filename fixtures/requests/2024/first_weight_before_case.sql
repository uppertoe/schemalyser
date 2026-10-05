SELECT tc.CASE_KEY,
       w.READ_TS      AS weight_ts,
       w.weight_kg
FROM   THEATRE_CASE tc
       OUTER APPLY (
           SELECT TOP 1 r.READ_TS,
                  TRY_CONVERT(decimal(6,2), r.READ_VALUE) AS weight_kg
           FROM   OBS_SHEET s
                  JOIN OBS_READING r ON r.SHEET_KEY = s.SHEET_KEY
           WHERE  s.VISIT_KEY = tc.VISIT_KEY
             AND  r.OBS_TYPE_KEY = '14'
             AND  r.READ_TS <= tc.CASE_DATE
           ORDER BY r.READ_TS DESC
       ) w
WHERE  tc.CASE_DATE >= DATEADD(YEAR, -2, GETDATE());
