-- Which airway devices are placed during each type of anaesthetic?
-- The anaesthetic is a PROCEDURE_OCCURRENCE with the concept of its type: 4174669 general,
-- 4100052 regional and 4219502 sedation. An airway is a DEVICE_EXPOSURE with the concept
-- 4097216 (endotracheal tube) or 4106029 (laryngeal mask), linked to the anaesthetic through the
-- visit detail that both rows carry.
SELECT po.procedure_concept_id                AS anaesthesia_type_concept_id,
       d.device_concept_id                    AS airway_concept_id,
       COUNT(DISTINCT po.procedure_occurrence_id) AS anaesthetics,
       COUNT(*)                               AS devices
FROM   omop.device_exposure d
       JOIN omop.procedure_occurrence po ON po.visit_detail_id = d.visit_detail_id
WHERE  d.device_concept_id IN (4097216, 4106029)
  AND  po.procedure_concept_id IN (4174669, 4100052, 4219502)
GROUP BY po.procedure_concept_id, d.device_concept_id
ORDER BY po.procedure_concept_id, d.device_concept_id
