select row_number() over (order by d.DEVICE_KEY) as device_exposure_id,
       vd.person_id as person_id,
       m.target_concept_id as device_concept_id,
       cast(d.PLACED_TS as date) as device_exposure_start_date,
       d.PLACED_TS as device_exposure_start_datetime,
       cast(d.REMOVED_TS as date) as device_exposure_end_date,
       d.REMOVED_TS as device_exposure_end_datetime,
       32817 as device_type_concept_id,
       vd.visit_occurrence_id as visit_occurrence_id,
       vd.visit_detail_id as visit_detail_id,
       cast(d.DEVICE_KIND_KEY as varchar(50)) as device_source_value
from {{ source('site', 'AIRWAY_DEVICE') }} d
join {{ ref('visit_detail') }} vd on vd.visit_detail_source_value = cast(d.ANAES_KEY as varchar(50))
join {{ source('vocab', 'source_to_concept_map') }} m
  on m.source_vocabulary_id = 'LOCAL_DEVICE'
 and m.source_code = cast(d.DEVICE_KIND_KEY as varchar(50))
where d.REMOVED_TS is not null
