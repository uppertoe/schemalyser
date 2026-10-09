{{ config(materialized='view') }}
{# The visits with an admission time, which the plain variant keeps in a CTE. #}
select v.VISIT_KEY, v.PERSON_KEY, v.WARD_KEY, v.ADMIT_TS,
       case when v.DISCH_TS >= v.ADMIT_TS then v.DISCH_TS else v.ADMIT_TS end as END_TS
from {{ source('site', 'VISIT') }} v
where v.ADMIT_TS is not null
