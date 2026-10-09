{{ config(materialized='table') }}
select dense_rank() over (order by sm.STAFF_KEY) as provider_id,
       0 as specialty_concept_id,
       cast(sm.STAFF_KEY as varchar(50)) as provider_source_value
from {{ source('site', 'STAFF_MASTER') }} sm
