{{ config(materialized='table', alias='condition_era') }}
select row_number() over (order by g.PERSON_KEY, g.DIAG_KEY) as condition_era_id,
       p.person_id as person_id,
       coalesce(c.target_concept_id, 0) as condition_concept_id,
       min(cast(g.ADMIT_TS as date)) as condition_era_start_date,
       max(cast(g.DISCH_TS as date)) as condition_era_end_date,
       count(*) as condition_occurrence_count
from {{ ref('stg_diagnoses') }} g
join {{ ref('person') }} p on p.person_source_value = g.PERSON_KEY
left join {{ source('vocab', 'source_to_concept_map') }} c
       on c.source_vocabulary_id = 'LOCAL_DIAGNOSIS'
      and c.source_code = cast(g.DIAG_KEY as varchar(50))
group by g.PERSON_KEY, g.DIAG_KEY, p.person_id, c.target_concept_id
