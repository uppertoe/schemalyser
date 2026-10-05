-- PROCEDURE_OCCURRENCE, anaesthesia events: one row for each event whose type has a mapping row under SITE_EVENT_PROC.
-- These are the events that the vocabulary describes as a procedure, such as the insertion of an endotracheal tube.
-- This step numbers its rows from 1, and the runner and the release script move them on past the anaesthetics.
SELECT ROW_NUMBER() OVER (ORDER BY ev.ANAES_KEY, ev.SEQ, vd.visit_detail_id, what.target_concept_id)      AS procedure_occurrence_id,
       vd.person_id                                  AS person_id,
       what.target_concept_id                        AS procedure_concept_id,
       CAST(ev.EVENT_TS AS date)                          AS procedure_date,
       ev.EVENT_TS                                        AS procedure_datetime,
       32817                                         AS procedure_type_concept_id,
       vd.visit_occurrence_id                        AS visit_occurrence_id,
       vd.visit_detail_id                            AS visit_detail_id,
       CAST(ev.EVENT_TYPE_KEY AS varchar(50))                   AS procedure_source_value
FROM   ANAES_EVENT ev
       JOIN omop.visit_detail vd ON vd.visit_detail_source_value = CAST(ev.ANAES_KEY AS varchar(50))
       JOIN omop.source_to_concept_map what
              ON what.source_vocabulary_id = 'SITE_EVENT_PROC'
             AND what.source_code = CAST(ev.EVENT_TYPE_KEY AS varchar(50))
WHERE  ev.EVENT_TS IS NOT NULL
