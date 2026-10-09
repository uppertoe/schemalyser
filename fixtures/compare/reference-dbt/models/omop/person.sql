{{ config(materialized='table') }}
select dense_rank() over (order by p.PERSON_KEY) as person_id,
       coalesce(g.target_concept_id, 0) as gender_concept_id,
       year(p.BIRTH_TS) as year_of_birth,
       month(p.BIRTH_TS) as month_of_birth,
       day(p.BIRTH_TS) as day_of_birth,
       p.BIRTH_TS as birth_datetime,
       0 as race_concept_id,
       0 as ethnicity_concept_id,
       p.PERSON_KEY as person_source_value,
       p.SEX_CAT as gender_source_value
from {{ source('site', 'person_master') }} p
left join {{ source('vocab', 'source_to_concept_map') }} g
       on g.source_vocabulary_id = 'LOCAL_SEX'
      and g.source_code = p.SEX_CAT
where coalesce(p.TEST_PERSON_FLAG, 'N') = 'N'
