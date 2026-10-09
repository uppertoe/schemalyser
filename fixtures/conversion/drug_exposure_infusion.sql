-- DRUG_EXPOSURE, infusions: one row for each period that an infusion ran at one rate.
-- An action under SITE_DRUG_RUNNING starts a period, and the next action for the same medicine in the same anaesthetic, whatever it is,
-- ends it. An action under SITE_DRUG_STOPPED starts nothing. A period with no later action has no recorded end: the
-- source does not say when the infusion stopped, so the step does not invent an end at the anaesthetic's stop. Its
-- drug_exposure_end_datetime is empty, its stop_reason says that no stop was recorded, and its drug_exposure_end_date
-- is the date of its start only because the model requires an end date. The wording of that reason is the step's
-- own, written as a column of the inner query. The rate and its unit are kept as text, because the model has no field
-- for a rate.
-- This step numbers its rows from 1, and the runner and the release script move them into the anaesthesia layer's own range, above every identifier of the core.
SELECT ROW_NUMBER() OVER (ORDER BY a.ANAES_KEY, a.DRUG_KEY, a.started, a.GIVEN_KEY, vo.visit_occurrence_id, vd.visit_detail_id, what.target_concept_id, route.target_concept_id)      AS drug_exposure_id,
       vo.person_id                                      AS person_id,
       COALESCE(what.target_concept_id, 0)           AS drug_concept_id,
       CAST(a.started AS date)                       AS drug_exposure_start_date,
       a.started                                     AS drug_exposure_start_datetime,
       CAST(COALESCE(a.next_time, a.started) AS date) AS drug_exposure_end_date,
       a.next_time                                   AS drug_exposure_end_datetime,
       32818                                         AS drug_type_concept_id,
       CASE WHEN a.next_time IS NULL THEN a.no_stop END AS stop_reason,
       CAST(a.DOSE_AMT AS varchar(50))                                         AS sig,
       COALESCE(route.target_concept_id, 0)          AS route_concept_id,
       vo.visit_occurrence_id                                       AS visit_occurrence_id,
       vd.visit_detail_id                            AS visit_detail_id,
       LEFT(d.DRUG_LABEL, 50)                              AS drug_source_value,
       CAST(a.route AS varchar(50))                  AS route_source_value,
       CAST(a.DOSE_UNIT_CAT AS varchar(50))                                        AS dose_unit_source_value
FROM  (SELECT g.GIVEN_KEY, g.VISIT_KEY, g.ANAES_KEY, g.DRUG_KEY, g.GIVEN_TS AS started, g.DOSE_AMT, g.DOSE_UNIT_CAT,
              g.ROUTE_CAT AS route, phase.source_vocabulary_id AS phase, 'stop not recorded' AS no_stop,
              LEAD(g.GIVEN_TS) OVER (PARTITION BY g.ANAES_KEY, g.DRUG_KEY ORDER BY g.GIVEN_TS, g.GIVEN_KEY) AS next_time
       FROM   DRUG_GIVEN g
              JOIN omop.source_to_concept_map phase
                     ON phase.source_vocabulary_id IN ('SITE_DRUG_RUNNING', 'SITE_DRUG_STOPPED')
                    AND phase.source_code = CAST(g.ACTION_CAT AS varchar(50))
       WHERE  g.GIVEN_TS IS NOT NULL) a
       JOIN omop.visit_occurrence vo ON vo.visit_source_value = CAST(a.VISIT_KEY AS varchar(50))
       LEFT JOIN DRUG_DEF d ON d.DRUG_KEY = a.DRUG_KEY
       LEFT JOIN omop.visit_detail vd ON vd.visit_detail_source_value = CAST(a.ANAES_KEY AS varchar(50))
       LEFT JOIN omop.source_to_concept_map what
              ON what.source_vocabulary_id = 'SITE_DRUG'
             AND what.source_code = CAST(a.DRUG_KEY AS varchar(50))
       LEFT JOIN omop.source_to_concept_map route
              ON route.source_vocabulary_id = 'SITE_ROUTE'
             AND route.source_code = CAST(a.route AS varchar(50))
WHERE  a.phase = 'SITE_DRUG_RUNNING'
