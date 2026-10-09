-- MEASUREMENT, blood pressure: a charted pressure is held as text, systolic over diastolic, as in 110/70.
-- Each accepted value gives two rows, in millimetres of mercury (unit concept 8876).
-- An alternative to measurement_blood_pressure_through_anaesthetic.sql: a reading takes the visit key that its sheet
-- carries, so a pressure charted on a sheet without an anaesthetic is written too.
-- A part that is empty, as in 110/, gives no row, because SQL Server would otherwise read the empty text as 0.
-- This step runs after the main measurement step. It numbers its rows from 1, and the runner and the release script move them on past that step's rows.
-- Where two readings share a record and a line, their time and then their value decide the order, so that every engine numbers them alike.
SELECT ROW_NUMBER() OVER (ORDER BY bp.SHEET_KEY, bp.SEQ, bp.vocabulary, vo.visit_occurrence_id, vd.visit_detail_id, po.procedure_occurrence_id, what.target_concept_id, bp.READ_TS, bp.reading)  AS measurement_id,
       vo.person_id                                      AS person_id,
       what.target_concept_id                        AS measurement_concept_id,
       CAST(bp.READ_TS AS date)                       AS measurement_date,
       bp.READ_TS                                     AS measurement_datetime,
       32817                                         AS measurement_type_concept_id,
       bp.reading                                    AS value_as_number,
       8876                                          AS unit_concept_id,
       vo.visit_occurrence_id                                       AS visit_occurrence_id,
       vd.visit_detail_id                            AS visit_detail_id,
       po.procedure_occurrence_id                    AS measurement_event_id,
       CASE WHEN po.procedure_occurrence_id IS NOT NULL THEN 1147082 END AS meas_event_field_concept_id,
       CAST(bp.OBS_TYPE_KEY AS varchar(50))                 AS measurement_source_value,
       CAST(bp.READ_VALUE AS varchar(50))               AS value_source_value
FROM  (SELECT x.SHEET_KEY, x.SEQ, x.OBS_TYPE_KEY, x.READ_TS, x.READ_VALUE, 'SITE_OBS_SYSTOLIC' AS vocabulary,
              CASE WHEN CHARINDEX('/', x.READ_VALUE) > 1
                   THEN TRY_CAST(LEFT(x.READ_VALUE, CHARINDEX('/', x.READ_VALUE) - 1) AS float) END AS reading
       FROM   OBS_READING x
       WHERE  COALESCE(x.ACCEPTED_FLAG, 'Y') <> 'N'
       UNION ALL
       SELECT x.SHEET_KEY, x.SEQ, x.OBS_TYPE_KEY, x.READ_TS, x.READ_VALUE, 'SITE_OBS_DIASTOLIC' AS vocabulary,
              CASE WHEN CHARINDEX('/', x.READ_VALUE) > 1
                   THEN TRY_CAST(NULLIF(LTRIM(RTRIM(SUBSTRING(x.READ_VALUE, CHARINDEX('/', x.READ_VALUE) + 1, 20))), '') AS float) END AS reading
       FROM   OBS_READING x
       WHERE  COALESCE(x.ACCEPTED_FLAG, 'Y') <> 'N') bp
       JOIN OBS_SHEET s ON s.SHEET_KEY = bp.SHEET_KEY
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
             AND bp.READ_TS >= DATEADD(minute, -po.margin_minutes, po.procedure_datetime)
             AND (po.procedure_end_datetime IS NULL OR bp.READ_TS <= DATEADD(minute, po.margin_minutes, po.procedure_end_datetime))
       JOIN omop.source_to_concept_map what
              ON what.source_vocabulary_id = bp.vocabulary
             AND what.source_code = CAST(bp.OBS_TYPE_KEY AS varchar(50))
WHERE  bp.reading IS NOT NULL
  AND  bp.READ_TS IS NOT NULL
