{{ config(materialized='ephemeral') }}
select d.VISIT_KEY, d.DIAG_KEY, v.PERSON_KEY, v.ADMIT_TS, v.DISCH_TS
from {{ source('site', 'VISIT_DIAGNOSIS') }} d
join {{ source('site', 'VISIT') }} v on v.VISIT_KEY = d.VISIT_KEY
where d.PRIMARY_FLAG = 'Y'
