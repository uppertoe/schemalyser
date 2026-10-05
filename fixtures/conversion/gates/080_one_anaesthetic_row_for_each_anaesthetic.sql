-- The custom table ANAESTHETIC holds exactly one row for each anaesthetic that the anaesthesia layer wrote,
-- and no row for anything else. Each anaesthetic is its own row in PROCEDURE_OCCURRENCE, whose
-- procedure_source_value is ANAESTHETIC and which names its visit detail.
SELECT po.procedure_occurrence_id AS identifier, COUNT(a.anaesthetic_id) AS rows_in_anaesthetic
FROM   omop.procedure_occurrence po
       LEFT JOIN omop.anaesthetic a ON a.anaesthetic_id = po.procedure_occurrence_id
WHERE  po.procedure_source_value = 'ANAESTHETIC'
  AND  po.visit_detail_id IS NOT NULL
GROUP BY po.procedure_occurrence_id
HAVING COUNT(a.anaesthetic_id) <> 1
UNION ALL
SELECT a.anaesthetic_id, COUNT(*)
FROM   omop.anaesthetic a
       LEFT JOIN omop.procedure_occurrence po
              ON po.procedure_occurrence_id = a.anaesthetic_id
             AND po.procedure_source_value = 'ANAESTHETIC'
WHERE  po.procedure_occurrence_id IS NULL
GROUP BY a.anaesthetic_id
