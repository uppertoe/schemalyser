-- DEVICE_EXPOSURE is written by INSERT INTO. It is planted to differ from our conversion in one filter: a device is
-- kept when its removal time is recorded, where ours keeps a device whose placement time is recorded.
INSERT INTO cdm.device_exposure (device_exposure_id, person_id, device_concept_id, device_exposure_start_date,
                                 device_exposure_start_datetime, device_exposure_end_date, device_exposure_end_datetime,
                                 device_type_concept_id, visit_occurrence_id, visit_detail_id, device_source_value)
SELECT ROW_NUMBER() OVER (ORDER BY d.DEVICE_KEY),
       vd.person_id,
       m.target_concept_id,
       CAST(d.PLACED_TS AS date),
       d.PLACED_TS,
       CAST(d.REMOVED_TS AS date),
       d.REMOVED_TS,
       32817,
       vd.visit_occurrence_id,
       vd.visit_detail_id,
       CAST(d.DEVICE_KIND_KEY AS varchar(50))
FROM   dbo.AIRWAY_DEVICE d
       JOIN cdm.visit_detail vd ON vd.visit_detail_source_value = CAST(d.ANAES_KEY AS varchar(50))
       JOIN cdm.source_to_concept_map m
         ON m.source_vocabulary_id = 'LOCAL_DEVICE'
        AND m.source_code = CAST(d.DEVICE_KIND_KEY AS varchar(50))
WHERE  d.REMOVED_TS IS NOT NULL
