-- MEASUREMENT, ASA class: one row for each anaesthetic whose risk grade has a mapping row.
-- The vocabulary places the ASA physical status class (concept 4199571) in the measurement domain,
-- with the class itself as the value. This step numbers its rows from 1, and the runner and the release script move them on past the earlier measurement steps.
SELECT ROW_NUMBER() OVER (ORDER BY ar.ANAES_KEY, vd.visit_detail_id, po.procedure_occurrence_id, grade.target_concept_id)      AS measurement_id,
       vd.person_id                                  AS person_id,
       4199571                                       AS measurement_concept_id,
       vd.visit_detail_start_date                    AS measurement_date,
       vd.visit_detail_start_datetime                AS measurement_datetime,
       32817                                         AS measurement_type_concept_id,
       grade.target_concept_id                       AS value_as_concept_id,
       vd.visit_occurrence_id                        AS visit_occurrence_id,
       vd.visit_detail_id                            AS visit_detail_id,
       po.procedure_occurrence_id                    AS measurement_event_id,
       CASE WHEN po.procedure_occurrence_id IS NOT NULL THEN 1147082 END AS meas_event_field_concept_id,
       'ASA class'                                   AS measurement_source_value,
       CAST(ar.RISK_GRADE_CAT AS varchar(50))                   AS value_source_value
FROM   ANAES_RECORD ar
       JOIN omop.visit_detail vd ON vd.visit_detail_source_value = CAST(ar.ANAES_KEY AS varchar(50))
       LEFT JOIN omop.procedure_occurrence po
              ON po.visit_detail_id = vd.visit_detail_id
             AND po.procedure_source_value = 'ANAESTHETIC'
       JOIN omop.source_to_concept_map grade
              ON grade.source_vocabulary_id = 'SITE_RISK_GRADE'
             AND grade.source_code = CAST(ar.RISK_GRADE_CAT AS varchar(50))
