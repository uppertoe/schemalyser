-- PERSON: one row for each person who is not a test person.
SELECT DENSE_RANK() OVER (ORDER BY pm.PERSON_KEY)  AS person_id,
       COALESCE(sex.target_concept_id, 0)          AS gender_concept_id,
       YEAR(pm.BIRTH_TS)                           AS year_of_birth,
       MONTH(pm.BIRTH_TS)                          AS month_of_birth,
       DAY(pm.BIRTH_TS)                            AS day_of_birth,
       pm.BIRTH_TS                                 AS birth_datetime,
       0                                           AS race_concept_id,
       0                                           AS ethnicity_concept_id,
       pm.PERSON_KEY                               AS person_source_value,
       CAST(pm.SEX_CAT AS varchar(50))             AS gender_source_value
FROM   PERSON_MASTER pm
       LEFT JOIN omop.source_to_concept_map sex
              ON sex.source_vocabulary_id = 'SITE_SEX'
             AND sex.source_code = CAST(pm.SEX_CAT AS varchar(50))
WHERE  COALESCE(pm.TEST_PERSON_FLAG, 'N') <> 'Y'
