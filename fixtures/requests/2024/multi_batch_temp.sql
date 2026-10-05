USE RPT;
GO
SET NOCOUNT ON;
IF OBJECT_ID('tempdb..#cases') IS NOT NULL DROP TABLE #cases;
IF OBJECT_ID('tempdb..#first_event') IS NOT NULL DROP TABLE #first_event;
GO

SELECT tc.CASE_KEY, tc.VISIT_KEY, ar.ANAES_KEY
INTO   #cases
FROM   THEATRE_CASE tc
       JOIN ANAES_RECORD ar ON ar.CASE_KEY = tc.CASE_KEY
WHERE  tc.EMERGENCY_FLAG = 'Y';

CREATE CLUSTERED INDEX ix_cases ON #cases (ANAES_KEY);
GO

SELECT c.ANAES_KEY, MIN(ev.EVENT_TS) AS first_ts
INTO   #first_event
FROM   #cases c
       JOIN ANAES_EVENT ev ON ev.ANAES_KEY = c.ANAES_KEY
GROUP BY c.ANAES_KEY;
GO

SELECT c.CASE_KEY, v.ADMIT_TS, fe.first_ts, wd.WARD_LABEL
FROM   #cases c
       JOIN #first_event fe ON fe.ANAES_KEY = c.ANAES_KEY
       JOIN VISIT v ON v.VISIT_KEY = c.VISIT_KEY
       LEFT JOIN WARD_DEF wd ON wd.WARD_KEY = v.WARD_KEY;

DROP TABLE #cases;
DROP TABLE #first_event;
