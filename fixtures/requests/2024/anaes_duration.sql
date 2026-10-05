SELECT x.CASE_KEY, x.anaes_minutes, x.induction_to_incision
FROM (
    SELECT ar.CASE_KEY,
           DATEDIFF(MINUTE, ar.ANAES_START_TS, ar.ANAES_STOP_TS) AS anaes_minutes,
           DATEDIFF(MINUTE,
                    MIN(CASE WHEN ev.EVENT_TYPE_KEY = '1120000001' THEN ev.EVENT_TS END),
                    MIN(CASE WHEN ev.EVENT_TYPE_KEY = '1120000014' THEN ev.EVENT_TS END)) AS induction_to_incision,
           ROW_NUMBER() OVER (PARTITION BY ar.CASE_KEY ORDER BY ar.ANAES_START_TS) AS rn
    FROM   ANAES_RECORD ar
           LEFT JOIN ANAES_EVENT ev ON ev.ANAES_KEY = ar.ANAES_KEY
    GROUP BY ar.CASE_KEY, ar.ANAES_KEY, ar.ANAES_START_TS, ar.ANAES_STOP_TS
) x
WHERE x.rn = 1
  AND x.anaes_minutes BETWEEN 1 AND 1440;
