-- MEASUREMENT, calculated mean pressure: for each accepted charted pressure that gives both a systolic and a diastolic
-- row, as in 110/70, a mean pressure calculated as the diastolic and a third of the difference between the systolic and
-- the diastolic, rounded to three decimal places, in millimetres of mercury (unit concept 8876).
-- The row has the concept 3027598 (Mean blood pressure) and the type concept 32882 (Standard algorithm from EHR), which
-- marks it as calculated rather than measured, so that a query can tell it from a mean that the monitor charted.
-- This step writes nothing unless it is switched on, so that by default the conversion records only what was measured.
-- It is switched on by the target 1 of the one mapping row under SITE_SETTING with the source code
-- CALCULATED_MEAN_PRESSURE, and switched off by the target 0, which is the default.
-- A reading takes the visit, the visit detail and the link to the anaesthetic exactly as the blood pressure step gives
-- them. This step runs after the blood pressure step, and the runner and the release script move its rows on past it.
SELECT ROW_NUMBER() OVER (ORDER BY bp.SHEET_KEY, bp.SEQ, bp.READ_TS, bp.systolic, bp.diastolic, vo.visit_occurrence_id, vd.visit_detail_id, po.procedure_occurrence_id) AS measurement_id,
       vo.person_id                                      AS person_id,
       3027598                                       AS measurement_concept_id,
       CAST(bp.READ_TS AS date)                       AS measurement_date,
       bp.READ_TS                                     AS measurement_datetime,
       32882                                         AS measurement_type_concept_id,
       ROUND(bp.diastolic + (bp.systolic - bp.diastolic) / 3.0, 3) AS value_as_number,
       8876                                          AS unit_concept_id,
       vo.visit_occurrence_id                                       AS visit_occurrence_id,
       vd.visit_detail_id                            AS visit_detail_id,
       po.procedure_occurrence_id                    AS measurement_event_id,
       CASE WHEN po.procedure_occurrence_id IS NOT NULL THEN 1147082 END AS meas_event_field_concept_id,
       CAST(bp.OBS_TYPE_KEY AS varchar(50))                 AS measurement_source_value,
       CAST(bp.READ_VALUE AS varchar(50))               AS value_source_value
FROM  (SELECT x.SHEET_KEY, x.SEQ, x.OBS_TYPE_KEY, x.READ_TS, x.READ_VALUE,
              CASE WHEN CHARINDEX('/', x.READ_VALUE) > 1
                   THEN TRY_CAST(LEFT(x.READ_VALUE, CHARINDEX('/', x.READ_VALUE) - 1) AS float) END AS systolic,
              CASE WHEN CHARINDEX('/', x.READ_VALUE) > 1
                   THEN TRY_CAST(NULLIF(LTRIM(RTRIM(SUBSTRING(x.READ_VALUE, CHARINDEX('/', x.READ_VALUE) + 1, 20))), '') AS float) END AS diastolic
       FROM   OBS_READING x
       WHERE  COALESCE(x.ACCEPTED_FLAG, 'Y') <> 'N') bp
       -- Without the setting's mapping row, or with its target 0, this join keeps no row, so the step writes nothing.
       JOIN omop.source_to_concept_map setting
              ON setting.source_vocabulary_id = 'SITE_SETTING'
             AND setting.source_code = 'CALCULATED_MEAN_PRESSURE'
             AND setting.target_concept_id = 1
       JOIN OBS_SHEET s ON s.SHEET_KEY = bp.SHEET_KEY
       JOIN omop.visit_detail vd ON vd.visit_detail_source_value = CAST(s.ANAES_KEY AS varchar(50))
       JOIN omop.visit_occurrence vo ON vo.visit_occurrence_id = vd.visit_occurrence_id
       -- The anaesthetic that the row points at through its event field: only a row taken from the anaesthetic's start to
       -- its end, with the margin either side, or from its start less the margin where it has no end. The margin, in minutes, is
       -- the target of the one mapping row under SITE_SETTING (EVENT_LINK_MARGIN_MINUTES), so that it is set in one place.
       -- A row outside that window keeps the anaesthetic's visit_detail_id. The field concept 1147082 names procedure_occurrence_id.
       LEFT JOIN omop.source_to_concept_map margin
              ON margin.source_vocabulary_id = 'SITE_SETTING'
             AND margin.source_code = 'EVENT_LINK_MARGIN_MINUTES'
       LEFT JOIN omop.procedure_occurrence po
              ON po.visit_detail_id = vd.visit_detail_id
             AND po.procedure_source_value = 'ANAESTHETIC'
             AND bp.READ_TS >= DATEADD(minute, -margin.target_concept_id, po.procedure_datetime)
             AND (po.procedure_end_datetime IS NULL OR bp.READ_TS <= DATEADD(minute, margin.target_concept_id, po.procedure_end_datetime))
       -- A charted pressure is one whose observation type has a mapping row for its systolic part and one for its diastolic part.
       JOIN omop.source_to_concept_map systolic_part
              ON systolic_part.source_vocabulary_id = 'SITE_OBS_SYSTOLIC'
             AND systolic_part.source_code = CAST(bp.OBS_TYPE_KEY AS varchar(50))
       JOIN omop.source_to_concept_map diastolic_part
              ON diastolic_part.source_vocabulary_id = 'SITE_OBS_DIASTOLIC'
             AND diastolic_part.source_code = CAST(bp.OBS_TYPE_KEY AS varchar(50))
WHERE  bp.systolic IS NOT NULL
  AND  bp.diastolic IS NOT NULL
  AND  bp.READ_TS IS NOT NULL
