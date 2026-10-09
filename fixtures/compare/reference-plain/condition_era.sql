-- CONDITION_ERA is planted as the one table that only the reference writes. It is built from an intermediate
-- table of primary diagnoses, which the comparison follows through to the tables that it reads.
SELECT d.VISIT_KEY, d.DIAG_KEY, v.PERSON_KEY, v.ADMIT_TS, v.DISCH_TS
INTO   #diagnoses
FROM   dbo.VISIT_DIAGNOSIS d
       JOIN dbo.VISIT v ON v.VISIT_KEY = d.VISIT_KEY
WHERE  d.PRIMARY_FLAG = 'Y'
GO
INSERT INTO cdm.condition_era (condition_era_id, person_id, condition_concept_id, condition_era_start_date,
                               condition_era_end_date, condition_occurrence_count)
SELECT ROW_NUMBER() OVER (ORDER BY g.PERSON_KEY, g.DIAG_KEY),
       p.person_id,
       COALESCE(c.target_concept_id, 0),
       MIN(CAST(g.ADMIT_TS AS date)),
       MAX(CAST(g.DISCH_TS AS date)),
       COUNT(*)
FROM   #diagnoses g
       JOIN cdm.person p ON p.person_source_value = g.PERSON_KEY
       LEFT JOIN cdm.source_to_concept_map c
              ON c.source_vocabulary_id = 'LOCAL_DIAGNOSIS'
             AND c.source_code = CAST(g.DIAG_KEY AS varchar(50))
GROUP BY g.PERSON_KEY, g.DIAG_KEY, p.person_id, c.target_concept_id
GO
