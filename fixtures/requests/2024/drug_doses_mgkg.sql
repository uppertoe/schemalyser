DECLARE @start datetime = '2024-01-01';
DECLARE @end   datetime;
SET @end = DATEADD(MONTH, 6, @start);

SELECT dd.DRUG_LABEL,
       rt.LABEL AS route,
       CONVERT(varchar(10), dg.GIVEN_TS, 103) AS given_date,
       dg.DOSE_AMT,
       dg.DOSE_AMT / NULLIF(TRY_CONVERT(decimal(6,2), wt.READ_VALUE), 0) AS dose_per_kg,
       IIF(pm2.GEST_WEEKS < 37, 'preterm', 'term') AS gestation
FROM   DRUG_GIVEN dg
       JOIN DRUG_DEF dd ON dd.DRUG_KEY = dg.DRUG_KEY
       JOIN ANAES_RECORD ar ON ar.ANAES_KEY = dg.ANAES_KEY
       JOIN THEATRE_CASE tc ON tc.CASE_KEY = ar.CASE_KEY
       LEFT JOIN PERSON_MASTER_2 pm2 ON pm2.PERSON_KEY = tc.PERSON_KEY
       LEFT JOIN LK_ROUTE rt ON rt.ROUTE_CAT = dg.ROUTE_CAT
       LEFT JOIN OBS_SHEET s ON s.ANAES_KEY = ar.ANAES_KEY
       LEFT JOIN OBS_READING wt ON wt.SHEET_KEY = s.SHEET_KEY AND wt.OBS_TYPE_KEY = '14'
WHERE  dg.GIVEN_TS >= @start AND dg.GIVEN_TS < @end
  AND  dg.ACTION_CAT IN (1, 6)
  AND  dg.DRUG_KEY IN (SELECT DRUG_KEY FROM DRUG_DEF WHERE DRUG_LABEL LIKE 'PROPOFOL%')
OPTION (RECOMPILE);
