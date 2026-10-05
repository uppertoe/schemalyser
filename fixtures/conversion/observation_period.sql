-- OBSERVATION_PERIOD: one row for each person, from their first recorded event to their last.
-- This step reads only the OMOP tables that the core's earlier steps wrote.
SELECT ROW_NUMBER() OVER (ORDER BY e.person_id)    AS observation_period_id,
       e.person_id                                 AS person_id,
       MIN(e.first_date)                           AS observation_period_start_date,
       MAX(e.last_date)                            AS observation_period_end_date,
       32817                                       AS period_type_concept_id
FROM  (SELECT vo.person_id, vo.visit_start_date AS first_date, vo.visit_end_date AS last_date
       FROM   omop.visit_occurrence vo
       UNION ALL
       SELECT po.person_id, po.procedure_date, COALESCE(po.procedure_end_date, po.procedure_date)
       FROM   omop.procedure_occurrence po
       UNION ALL
       SELECT m.person_id, m.measurement_date, m.measurement_date
       FROM   omop.measurement m
       UNION ALL
       SELECT o.person_id, o.observation_date, o.observation_date
       FROM   omop.observation o
       UNION ALL
       SELECT d.person_id, d.drug_exposure_start_date, d.drug_exposure_end_date
       FROM   omop.drug_exposure d
       UNION ALL
       SELECT c.person_id, c.condition_start_date, c.condition_start_date
       FROM   omop.condition_occurrence c
       UNION ALL
       SELECT x.person_id, x.device_exposure_start_date, COALESCE(x.device_exposure_end_date, x.device_exposure_start_date)
       FROM   omop.device_exposure x) e
GROUP BY e.person_id
