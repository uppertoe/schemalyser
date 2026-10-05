-- DEATH: one row for each person with a recorded date of death.
SELECT p.person_id                                   AS person_id,
       CAST(pm.DEATH_TS AS date)                          AS death_date,
       pm.DEATH_TS                                        AS death_datetime,
       32817                                         AS death_type_concept_id
FROM   PERSON_MASTER_2 pm
       JOIN omop.person p ON p.person_source_value = pm.PERSON_KEY
WHERE  pm.DEATH_TS IS NOT NULL
