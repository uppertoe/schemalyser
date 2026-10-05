-- The vocabulary has no visit concept for time in theatre, so without a mapping row the
-- visit detail takes the concept of the visit it sits in.
-- VISIT_DETAIL: one row for each anaesthetic, as the part of the hospital visit spent under anaesthesia.
-- An alternative to visit_detail_through_case.sql: the anaesthetic reaches its hospital visit through the visit key
-- that the anaesthetic record carries, rather than through its theatre case as the data team's queries do.
-- An anaesthetic with a start and no recorded stop is written: its visit_detail_end_datetime is empty, and its
-- visit_detail_end_date is the date of its start, because the model requires an end date. An anaesthetic whose
-- stop is before its start is left out, and the run counts it (counts/).
-- Each anaesthetic takes one number, so that an anaesthetic listed twice repeats its identifier and the primary key refuses it.
SELECT DENSE_RANK() OVER (ORDER BY ar.ANAES_KEY) AS visit_detail_id,
       vo.person_id                                AS person_id,
       COALESCE(kind.target_concept_id, vo.visit_concept_id)         AS visit_detail_concept_id,
       CAST(ar.ANAES_START_TS AS date)             AS visit_detail_start_date,
       ar.ANAES_START_TS                           AS visit_detail_start_datetime,
       CAST(COALESCE(ar.ANAES_STOP_TS, ar.ANAES_START_TS) AS date)              AS visit_detail_end_date,
       ar.ANAES_STOP_TS                            AS visit_detail_end_datetime,
       32817                                       AS visit_detail_type_concept_id,
       pr.provider_id                              AS provider_id,
       CAST(ar.ANAES_KEY AS varchar(50))           AS visit_detail_source_value,
       vo.visit_occurrence_id                      AS visit_occurrence_id
FROM   ANAES_RECORD ar
       JOIN omop.visit_occurrence vo ON vo.visit_source_value = CAST(ar.VISIT_KEY AS varchar(50))
       LEFT JOIN (SELECT s.ANAES_KEY, s.STAFF_KEY, ROW_NUMBER() OVER (PARTITION BY s.ANAES_KEY ORDER BY s.SEQ) AS position
                  FROM   ANAES_STAFF s) st ON st.ANAES_KEY = ar.ANAES_KEY AND st.position = 1
       LEFT JOIN omop.provider pr ON pr.provider_source_value = st.STAFF_KEY
       LEFT JOIN omop.source_to_concept_map kind
              ON kind.source_vocabulary_id = 'SITE_VISIT_DETAIL'
             AND kind.source_code = 'ANAESTHESIA'
WHERE  ar.ANAES_START_TS IS NOT NULL
  AND  (ar.ANAES_STOP_TS IS NULL OR ar.ANAES_STOP_TS >= ar.ANAES_START_TS)
