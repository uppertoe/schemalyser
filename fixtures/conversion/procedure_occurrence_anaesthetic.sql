-- PROCEDURE_OCCURRENCE: one row for each anaesthetic, with its start and its end.
-- Where the kind of anaesthetic has a mapping row, its concept is used. Otherwise the row takes the
-- general concept for an anaesthetic or sedation.
-- Each anaesthetic takes one number, so that an anaesthetic listed twice repeats its identifier and the primary key refuses it.
SELECT DENSE_RANK() OVER (ORDER BY ar.ANAES_KEY) AS procedure_occurrence_id,
       vd.person_id                                AS person_id,
       COALESCE(kind.target_concept_id, what.target_concept_id, 0) AS procedure_concept_id,
       CAST(ar.ANAES_START_TS AS date)             AS procedure_date,
       ar.ANAES_START_TS                           AS procedure_datetime,
       CAST(ar.ANAES_STOP_TS AS date)              AS procedure_end_date,
       ar.ANAES_STOP_TS                            AS procedure_end_datetime,
       32817                                       AS procedure_type_concept_id,
       vd.provider_id                              AS provider_id,
       vd.visit_occurrence_id                      AS visit_occurrence_id,
       vd.visit_detail_id                          AS visit_detail_id,
       'ANAESTHETIC'                               AS procedure_source_value
FROM   ANAES_RECORD ar
       JOIN omop.visit_detail vd ON vd.visit_detail_source_value = CAST(ar.ANAES_KEY AS varchar(50))
       LEFT JOIN omop.source_to_concept_map kind
              ON kind.source_vocabulary_id = 'SITE_ANAES_KIND'
             AND kind.source_code = CAST(ar.ANAES_KIND_CAT AS varchar(50))
       LEFT JOIN omop.source_to_concept_map what
              ON what.source_vocabulary_id = 'SITE_PROCEDURE'
             AND what.source_code = 'ANAESTHETIC'
