{{ config(materialized='table') }}
with first_staff as (
    select s.ANAES_KEY, s.STAFF_KEY, row_number() over (partition by s.ANAES_KEY order by s.SEQ) as rn
    from {{ source('site', 'ANAES_STAFF') }} s
)
select dense_rank() over (order by a.ANAES_KEY) as visit_detail_id,
       vo.person_id as person_id,
       vo.visit_concept_id as visit_detail_concept_id,
       cast(a.ANAES_START_TS as date) as visit_detail_start_date,
       a.ANAES_START_TS as visit_detail_start_datetime,
       cast(coalesce(a.ANAES_STOP_TS, a.ANAES_START_TS) as date) as visit_detail_end_date,
       a.ANAES_STOP_TS as visit_detail_end_datetime,
       32817 as visit_detail_type_concept_id,
       pr.provider_id as provider_id,
       cast(a.ANAES_KEY as varchar(50)) as visit_detail_source_value,
       vo.visit_occurrence_id as visit_occurrence_id
from {{ source('site', 'ANAES_RECORD') }} a
join {{ ref('encounters') }} vo on vo.visit_source_value = cast(a.VISIT_KEY as varchar(50))
left join first_staff on first_staff.ANAES_KEY = a.ANAES_KEY and first_staff.rn = 1
left join {{ ref('provider') }} pr on pr.provider_source_value = first_staff.STAFF_KEY
where a.ANAES_START_TS is not null
  and (a.ANAES_STOP_TS is null or a.ANAES_STOP_TS >= a.ANAES_START_TS)
