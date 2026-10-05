/* Theatre utilisation by service, FY2023
   Requested by: Dr Fenwick-Okafor (send output to b.thistlewood@example.org)
*/
SELECT TOP 500
       svc.[LABEL]                                  AS service_name,
       DATEPART(YEAR, tc.CASE_DATE)                 AS case_year,
       DATEPART(MONTH, tc.CASE_DATE)                AS case_month,
       COUNT(DISTINCT tc.CASE_KEY)                  AS n_cases,
       SUM(DATEDIFF(MINUTE, ar.ANAES_START_TS, ar.ANAES_STOP_TS)) / 60.0 AS anaes_hours
FROM   [RPT].[dbo].[THEATRE_CASE] tc WITH (NOLOCK)
       INNER JOIN dbo.ANAES_RECORD ar WITH (NOLOCK) ON ar.CASE_KEY = tc.CASE_KEY
       LEFT JOIN dbo.LK_SERVICE svc ON svc.SERVICE_CAT = tc.SERVICE_CAT
WHERE  tc.CASE_DATE >= '2022-07-01'
  AND  tc.CASE_DATE <  '2023-07-01'
  AND  tc.CASE_STATUS_CAT NOT IN (4, 6)   -- cancelled, voided
GROUP BY svc.[LABEL], DATEPART(YEAR, tc.CASE_DATE), DATEPART(MONTH, tc.CASE_DATE)
ORDER BY case_year, case_month, n_cases DESC;
