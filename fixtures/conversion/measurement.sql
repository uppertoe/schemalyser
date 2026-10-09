-- MEASUREMENT: one row for each accepted numeric reading in the anaesthetic's own record, mapped or not, and for each
-- accepted numeric reading elsewhere whose observation type has a mapping row.
-- The anaesthetic's own record is the observation sheet that carries the key of an anaesthetic (OBS_SHEET.ANAES_KEY)
-- which the anaesthesia layer has written as a visit detail. Every heart rate, pressure and saturation charted there
-- is written. A reading whose observation type has no mapping row takes the concept 0 and keeps its observation type
-- as measurement_source_value, so that a mapping row added later gives those rows their concept on the next run.
-- A value that is not a number is written only where a mapping row gives it a meaning, as the blood pressure step does.
-- Weights that arrive in ounces are written in kilograms, heights in inches in centimetres, and
-- temperatures in Fahrenheit in Celsius. The unit concepts named here are 9373 ounce, 9529 kilogram,
-- 9327 inch, 8582 centimetre, 9289 degree Fahrenheit and 586323 degree Celsius.
-- The rows are numbered in the order of their sheet and line, and then of their time and value, because a monitor can
-- chart several readings, such as a mean pressure every minute, under one line.
SELECT ROW_NUMBER() OVER (ORDER BY r.SHEET_KEY, r.SEQ, vo.visit_occurrence_id, vd.visit_detail_id, po.procedure_occurrence_id, what.target_concept_id, unit.target_concept_id, r.READ_TS, TRY_CAST(r.READ_VALUE AS float)) AS measurement_id,
       vo.person_id                                      AS person_id,
       COALESCE(what.target_concept_id, 0)           AS measurement_concept_id,
       CAST(r.READ_TS AS date)                          AS measurement_date,
       r.READ_TS                                        AS measurement_datetime,
       32817                                         AS measurement_type_concept_id,
       ROUND(CASE unit.target_concept_id
                  WHEN 9373 THEN TRY_CAST(r.READ_VALUE AS float) * 0.028349523125
                  WHEN 9327 THEN TRY_CAST(r.READ_VALUE AS float) * 2.54
                  WHEN 9289 THEN (TRY_CAST(r.READ_VALUE AS float) - 32) * 5.0 / 9.0
                  ELSE TRY_CAST(r.READ_VALUE AS float) END, 3) AS value_as_number,
       CASE unit.target_concept_id
            WHEN 9373 THEN 9529
            WHEN 9327 THEN 8582
            WHEN 9289 THEN 586323
            ELSE COALESCE(unit.target_concept_id, 0) END AS unit_concept_id,
       vo.visit_occurrence_id                                       AS visit_occurrence_id,
       vd.visit_detail_id                            AS visit_detail_id,
       po.procedure_occurrence_id                    AS measurement_event_id,
       CASE WHEN po.procedure_occurrence_id IS NOT NULL THEN 1147082 END AS meas_event_field_concept_id,
       CAST(r.OBS_TYPE_KEY AS varchar(50))                    AS measurement_source_value,
       CAST(r.READ_VALUE AS varchar(50))                  AS value_source_value
FROM   OBS_READING r
       JOIN OBS_SHEET s ON s.SHEET_KEY = r.SHEET_KEY
       JOIN omop.visit_occurrence vo ON vo.visit_source_value = CAST(s.VISIT_KEY AS varchar(50))
       LEFT JOIN omop.visit_detail vd ON vd.visit_detail_source_value = CAST(s.ANAES_KEY AS varchar(50))
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
             AND r.READ_TS >= DATEADD(minute, -po.margin_minutes, po.procedure_datetime)
             AND (po.procedure_end_datetime IS NULL OR r.READ_TS <= DATEADD(minute, po.margin_minutes, po.procedure_end_datetime))
       LEFT JOIN omop.source_to_concept_map what
              ON what.source_vocabulary_id = 'SITE_OBS'
             AND what.source_code = CAST(r.OBS_TYPE_KEY AS varchar(50))
       LEFT JOIN omop.source_to_concept_map unit
              ON unit.source_vocabulary_id = 'SITE_OBS_UNIT'
             AND unit.source_code = CAST(r.OBS_TYPE_KEY AS varchar(50))
WHERE  TRY_CAST(r.READ_VALUE AS float) IS NOT NULL
  AND  r.READ_TS IS NOT NULL
  AND  COALESCE(r.ACCEPTED_FLAG, 'Y') <> 'N'
  AND  (what.source_code IS NOT NULL OR vd.visit_detail_id IS NOT NULL)
