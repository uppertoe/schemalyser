-- DEVICE_EXPOSURE: one row for each airway or line whose kind has a mapping row.
-- A removal time more than a lifetime ahead is the source's way of saying that the device is still
-- in place, and it is written as an empty end.
SELECT ROW_NUMBER() OVER (ORDER BY dv.DEVICE_KEY, vd.visit_detail_id, what.target_concept_id) AS device_exposure_id,
       vd.person_id                                      AS person_id,
       what.target_concept_id                        AS device_concept_id,
       CAST(dv.PLACED_TS AS date)                        AS device_exposure_start_date,
       dv.PLACED_TS                                      AS device_exposure_start_datetime,
       CAST(CASE WHEN YEAR(dv.REMOVED_TS) < 2100 AND dv.REMOVED_TS >= dv.PLACED_TS THEN dv.REMOVED_TS END AS date) AS device_exposure_end_date,
       CASE WHEN YEAR(dv.REMOVED_TS) < 2100 AND dv.REMOVED_TS >= dv.PLACED_TS THEN dv.REMOVED_TS END AS device_exposure_end_datetime,
       32817                                         AS device_type_concept_id,
       vd.visit_occurrence_id                                       AS visit_occurrence_id,
       vd.visit_detail_id                            AS visit_detail_id,
       CAST(dv.DEVICE_KIND_KEY AS varchar(50))                   AS device_source_value
FROM   AIRWAY_DEVICE dv
       JOIN omop.visit_detail vd ON vd.visit_detail_source_value = CAST(dv.ANAES_KEY AS varchar(50))
       JOIN omop.source_to_concept_map what
              ON what.source_vocabulary_id = 'SITE_DEVICE'
             AND what.source_code = CAST(dv.DEVICE_KIND_KEY AS varchar(50))
WHERE  dv.PLACED_TS IS NOT NULL
