-- Every measurement that points at an event points at an anaesthetic that exists.
SELECT m.measurement_id
FROM   omop.measurement m
       LEFT JOIN omop.procedure_occurrence po
              ON po.procedure_occurrence_id = m.measurement_event_id
             AND po.procedure_source_value = 'ANAESTHETIC'
WHERE  m.measurement_event_id IS NOT NULL
  AND  po.procedure_occurrence_id IS NULL
