-- OBSERVATION, anaesthesia events: one row for each event whose type has a mapping row under SITE_EVENT_OBS.
-- These are the events that the vocabulary describes as a time, such as the start of anaesthesia.
SELECT ROW_NUMBER() OVER (ORDER BY ev.ANAES_KEY, ev.SEQ, vd.visit_detail_id, po.procedure_occurrence_id, what.target_concept_id) AS observation_id,
       vd.person_id                                  AS person_id,
       what.target_concept_id                        AS observation_concept_id,
       CAST(ev.EVENT_TS AS date)                          AS observation_date,
       ev.EVENT_TS                                        AS observation_datetime,
       32817                                         AS observation_type_concept_id,
       vd.visit_occurrence_id                        AS visit_occurrence_id,
       vd.visit_detail_id                            AS visit_detail_id,
       CAST(ev.EVENT_TYPE_KEY AS varchar(50))                   AS observation_source_value,
       po.procedure_occurrence_id                    AS observation_event_id,
       CASE WHEN po.procedure_occurrence_id IS NOT NULL THEN 1147082 END AS obs_event_field_concept_id
FROM   ANAES_EVENT ev
       JOIN omop.visit_detail vd ON vd.visit_detail_source_value = CAST(ev.ANAES_KEY AS varchar(50))
       -- The anaesthetic that the row points at through its event field: only a row taken from the anaesthetic's start to
       -- its end, with the margin either side, or from its start less the margin where it has no end. The margin, in minutes, is
       -- the target of the one mapping row under SITE_SETTING (EVENT_LINK_MARGIN_MINUTES), so that it is set in one place.
       -- A row outside that window keeps the anaesthetic's visit_detail_id. The field concept 1147082 names procedure_occurrence_id.
       -- The margin is read from that row as a value beside each anaesthetic, rather than joined on a constant, so that
       -- every join carries an equality between two columns that the policy can see; a second such row stops the step,
       -- where a join would have repeated every row.
       LEFT JOIN (SELECT p.procedure_occurrence_id, p.visit_detail_id, p.procedure_datetime, p.procedure_end_datetime,
                         (SELECT margin.target_concept_id
                          FROM   omop.source_to_concept_map margin
                          WHERE  margin.source_vocabulary_id = 'SITE_SETTING'
                            AND  margin.source_code = 'EVENT_LINK_MARGIN_MINUTES') AS margin_minutes
                  FROM   omop.procedure_occurrence p
                  WHERE  p.procedure_source_value = 'ANAESTHETIC') po
              ON po.visit_detail_id = vd.visit_detail_id
             AND ev.EVENT_TS >= DATEADD(minute, -po.margin_minutes, po.procedure_datetime)
             AND (po.procedure_end_datetime IS NULL OR ev.EVENT_TS <= DATEADD(minute, po.margin_minutes, po.procedure_end_datetime))
       JOIN omop.source_to_concept_map what
              ON what.source_vocabulary_id = 'SITE_EVENT_OBS'
             AND what.source_code = CAST(ev.EVENT_TYPE_KEY AS varchar(50))
WHERE  ev.EVENT_TS IS NOT NULL
