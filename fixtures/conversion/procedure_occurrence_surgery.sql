-- PROCEDURE_OCCURRENCE, surgery: one row for each operation on a theatre case that was carried out.
-- A theatre case counts as carried out when the mapping table holds a row for its status under SITE_CASE_DONE.
-- This step numbers its rows on from any already in the table.
SELECT (SELECT COALESCE(MAX(procedure_occurrence_id), 0) FROM omop.procedure_occurrence)
         + ROW_NUMBER() OVER (ORDER BY cp.CASE_KEY, cp.SEQ)      AS procedure_occurrence_id,
       p.person_id                                   AS person_id,
       COALESCE(what.target_concept_id, 0)           AS procedure_concept_id,
       CAST(tc.CASE_DATE AS date)                          AS procedure_date,
       32817                                         AS procedure_type_concept_id,
       vo.visit_occurrence_id                        AS visit_occurrence_id,
       LEFT(pd.PROC_LABEL, 50)                              AS procedure_source_value
FROM   CASE_PROC cp
       JOIN THEATRE_CASE tc ON tc.CASE_KEY = cp.CASE_KEY
       JOIN omop.person p ON p.person_source_value = tc.PERSON_KEY
       LEFT JOIN PROC_DEF pd ON pd.PROC_KEY = cp.PROC_KEY
       LEFT JOIN omop.visit_occurrence vo ON vo.visit_source_value = CAST(tc.VISIT_KEY AS varchar(50))
       JOIN omop.source_to_concept_map done
              ON done.source_vocabulary_id = 'SITE_CASE_DONE'
             AND done.source_code = CAST(tc.CASE_STATUS_CAT AS varchar(50))
       LEFT JOIN omop.source_to_concept_map what
              ON what.source_vocabulary_id = 'SITE_PROC'
             AND what.source_code = CAST(cp.PROC_KEY AS varchar(50))
WHERE  tc.CASE_DATE IS NOT NULL
