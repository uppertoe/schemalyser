-- Every anaesthetic falls inside an observation period of its person. ATLAS leaves out an event that does not.
SELECT po.procedure_occurrence_id
FROM   omop.procedure_occurrence po
WHERE  po.procedure_source_value = 'ANAESTHETIC'
  AND  NOT EXISTS (SELECT 1
                   FROM   omop.observation_period op
                   WHERE  op.person_id = po.person_id
                     AND  po.procedure_date >= op.observation_period_start_date
                     AND  po.procedure_date <= op.observation_period_end_date)
