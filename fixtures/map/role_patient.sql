-- role_patient for the invented world: one row for each row of the patient table, test patients included and marked.
-- The date of death sits in the second patient table, which holds at most one row for each patient.
SELECT pm.PERSON_KEY                                         AS patient_key,
       CAST(pm.BIRTH_TS AS date)                             AS birth_date,
       CAST(pm2.DEATH_TS AS date)                            AS death_date,
       CASE WHEN pm.TEST_PERSON_FLAG = 'Y' THEN 1 ELSE 0 END AS is_test
FROM   PERSON_MASTER pm
       LEFT JOIN PERSON_MASTER_2 pm2 ON pm2.PERSON_KEY = pm.PERSON_KEY
