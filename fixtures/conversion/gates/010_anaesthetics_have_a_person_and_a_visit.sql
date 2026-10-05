-- Every anaesthetic belongs to a person and a hospital visit that the core holds.
SELECT vd.visit_detail_id
FROM   omop.visit_detail vd
       LEFT JOIN omop.person p ON p.person_id = vd.person_id
       LEFT JOIN omop.visit_occurrence vo ON vo.visit_occurrence_id = vd.visit_occurrence_id
WHERE  p.person_id IS NULL
   OR  vo.visit_occurrence_id IS NULL
