-- The anaesthesia layer wrote at least one anaesthetic, as a procedure that belongs to a visit detail.
-- A run that wrote none has not found the core's visits, and its other gates would pass over empty tables.
SELECT 'no anaesthetic was written' AS problem
WHERE  NOT EXISTS (SELECT 1
                   FROM   omop.procedure_occurrence po
                          JOIN omop.visit_detail vd ON vd.visit_detail_id = po.visit_detail_id
                   WHERE  po.procedure_source_value = 'ANAESTHETIC')
