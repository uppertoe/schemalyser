-- An invented reference conversion, written as another team might write it, for testing the comparison.
-- PERSON is written by INSERT INTO and is planted to agree with our conversion: the same table, the same filter
-- column and the same fields, with different aliases and a different literal.
INSERT INTO cdm.person (person_id, gender_concept_id, year_of_birth, month_of_birth, day_of_birth, birth_datetime,
                        race_concept_id, ethnicity_concept_id, person_source_value, gender_source_value)
SELECT DENSE_RANK() OVER (ORDER BY p.PERSON_KEY),
       ISNULL(g.target_concept_id, 0),
       YEAR(p.BIRTH_TS),
       MONTH(p.BIRTH_TS),
       DAY(p.BIRTH_TS),
       p.BIRTH_TS,
       0,
       0,
       p.PERSON_KEY,
       p.SEX_CAT
FROM   dbo.PERSON_MASTER p
       LEFT JOIN cdm.source_to_concept_map g
              ON g.source_vocabulary_id = 'LOCAL_SEX'
             AND g.source_code = p.SEX_CAT
WHERE  ISNULL(p.TEST_PERSON_FLAG, 'N') = 'N'
GO
