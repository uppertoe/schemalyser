-- CONDITION_OCCURRENCE: one row for each diagnosis recorded against a hospital visit.
-- The diagnosis is dated to the start of that visit. Its concept comes from the mapping table,
-- whose rows are proposed from the ICD-10 code of each diagnosis.
SELECT ROW_NUMBER() OVER (ORDER BY vdx.VISIT_KEY, vdx.SEQ)          AS condition_occurrence_id,
       vo.person_id                                  AS person_id,
       COALESCE(what.target_concept_id, 0)           AS condition_concept_id,
       vo.visit_start_date                           AS condition_start_date,
       vo.visit_start_datetime                       AS condition_start_datetime,
       32817                                         AS condition_type_concept_id,
       vo.visit_occurrence_id                        AS visit_occurrence_id,
       LEFT(dd.ICD_CODE, 50)                               AS condition_source_value
FROM   VISIT_DIAGNOSIS vdx
       JOIN omop.visit_occurrence vo ON vo.visit_source_value = CAST(vdx.VISIT_KEY AS varchar(50))
       LEFT JOIN DIAG_DEF dd ON dd.DIAG_KEY = vdx.DIAG_KEY
       LEFT JOIN omop.source_to_concept_map what
              ON what.source_vocabulary_id = 'SITE_DIAGNOSIS'
             AND what.source_code = CAST(vdx.DIAG_KEY AS varchar(50))
