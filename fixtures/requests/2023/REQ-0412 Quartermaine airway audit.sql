-- REQ-0412  airway audit for Dr Quartermaine
-- index patient was Wilhelmina Thistlewood (record 9900112), DOB 14/03/2016 - check her case first
IF OBJECT_ID('tempdb..#quartermaine_cohort') IS NOT NULL DROP TABLE #quartermaine_cohort;

SELECT pm.PERSON_KEY, pm.RECORD_NO, ar.ANAES_KEY, ar.ANAES_START_TS
INTO   #quartermaine_cohort
FROM   PERSON_MASTER pm
       JOIN THEATRE_CASE tc ON tc.PERSON_KEY = pm.PERSON_KEY
       JOIN ANAES_RECORD ar ON ar.CASE_KEY = tc.CASE_KEY
WHERE  pm.RECORD_NO IN ('9900112', '9900345', '9900781')
  AND  ISNULL(pm.TEST_PERSON_FLAG, 'N') <> 'Y';

SELECT c.RECORD_NO,
       c.ANAES_KEY,
       r.READ_TS,
       d.OBS_LABEL,
       r.READ_VALUE AS thistlewood_value
FROM   #quartermaine_cohort c
       JOIN OBS_SHEET s    ON s.ANAES_KEY = c.ANAES_KEY
       JOIN OBS_READING r  ON r.SHEET_KEY = s.SHEET_KEY
       JOIN OBS_TYPE_DEF d ON d.OBS_TYPE_KEY = r.OBS_TYPE_KEY
WHERE  r.OBS_TYPE_KEY IN ('30417', '30418', '31102')   -- tube size, cuff, laryngoscopy grade
  AND  r.ACCEPTED_FLAG = 'Y'
ORDER BY c.RECORD_NO, r.READ_TS;
