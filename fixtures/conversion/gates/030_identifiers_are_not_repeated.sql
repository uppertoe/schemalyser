-- No identifier appears twice in a table that the anaesthesia layer adds to, the core's rows included.
SELECT 'visit_detail' AS table_name, visit_detail_id AS identifier
FROM   omop.visit_detail GROUP BY visit_detail_id HAVING COUNT(*) > 1
UNION ALL
SELECT 'procedure_occurrence', procedure_occurrence_id
FROM   omop.procedure_occurrence GROUP BY procedure_occurrence_id HAVING COUNT(*) > 1
UNION ALL
SELECT 'measurement', measurement_id
FROM   omop.measurement GROUP BY measurement_id HAVING COUNT(*) > 1
UNION ALL
SELECT 'observation', observation_id
FROM   omop.observation GROUP BY observation_id HAVING COUNT(*) > 1
UNION ALL
SELECT 'drug_exposure', drug_exposure_id
FROM   omop.drug_exposure GROUP BY drug_exposure_id HAVING COUNT(*) > 1
UNION ALL
SELECT 'device_exposure', device_exposure_id
FROM   omop.device_exposure GROUP BY device_exposure_id HAVING COUNT(*) > 1
