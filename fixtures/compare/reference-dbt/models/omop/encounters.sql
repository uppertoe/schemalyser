{{ config(materialized='table') }}
with final as (
    select dense_rank() over (order by vs.VISIT_KEY) as visit_occurrence_id,
           dense_rank() over (order by pm.PERSON_KEY) as person_id,
           9201 as visit_concept_id,
           cast(vs.ADMIT_TS as date) as visit_start_date,
           vs.ADMIT_TS as visit_start_datetime,
           cast(vs.END_TS as date) as visit_end_date,
           vs.END_TS as visit_end_datetime,
           32817 as visit_type_concept_id,
           dense_rank() over (order by w.WARD_KEY) as care_site_id,
           cast(vs.VISIT_KEY as varchar(50)) as visit_source_value
    from {{ ref('stg_visits') }} vs
    join {{ source('site', 'person_master') }} pm on pm.PERSON_KEY = vs.PERSON_KEY
    left join {{ source('site', 'WARD_DEF') }} w on w.WARD_KEY = vs.WARD_KEY
)
select * from final
