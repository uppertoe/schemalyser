-- ANAESTHETIC, a custom table: one row for each anaesthetic, built only from the OMOP rows that the earlier steps wrote.
-- The anaesthetic is its own row in PROCEDURE_OCCURRENCE, whose procedure_source_value is ANAESTHETIC, and the row
-- keeps that row's identifier, so that every measurement, drug and device that points at the anaesthetic points at this row too.
-- The ASA class comes from the ASA measurement (concept 4199571) that points at the anaesthetic. The weight is the most
-- recent body weight (concept 3025315, in kilograms, unit concept 9529) recorded from seven days before the start to the end,
-- or to the start where the anaesthetic has no recorded stop. Such an anaesthetic has an empty end and an empty duration.
SELECT po.procedure_occurrence_id                                         AS anaesthetic_id,
       po.person_id                                                       AS person_id,
       po.visit_occurrence_id                                             AS visit_occurrence_id,
       po.visit_detail_id                                                 AS visit_detail_id,
       po.procedure_concept_id                                            AS anaesthesia_type_concept_id,
       po.procedure_datetime                                              AS start_datetime,
       po.procedure_end_datetime                                          AS end_datetime,
       DATEDIFF(minute, po.procedure_datetime, po.procedure_end_datetime) AS duration_minutes,
       DATEDIFF(day, p.birth_datetime, po.procedure_datetime)             AS age_days,
       -- Completed years and months by calendar date: a year is completed on the day and month of birth, so a child
       -- is one year old from the date of the first birthday. The day of the month is compared, never the count of
       -- year boundaries that DATEDIFF(year, ...) gives, and a child born on 29 February completes a year on 1 March in a year without that date.
       YEAR(po.procedure_datetime) - YEAR(p.birth_datetime)
         - CASE WHEN MONTH(po.procedure_datetime) < MONTH(p.birth_datetime)
                  OR (MONTH(po.procedure_datetime) = MONTH(p.birth_datetime) AND DAY(po.procedure_datetime) < DAY(p.birth_datetime))
                THEN 1 ELSE 0 END                                         AS age_years,
       (YEAR(po.procedure_datetime) - YEAR(p.birth_datetime)) * 12 + MONTH(po.procedure_datetime) - MONTH(p.birth_datetime)
         - CASE WHEN DAY(po.procedure_datetime) < DAY(p.birth_datetime) THEN 1 ELSE 0 END AS age_months,
       asa.value_as_concept_id                                            AS asa_class_concept_id,
       wt.value_as_number                                                 AS weight_kg,
       po.provider_id                                                     AS provider_id
FROM   omop.procedure_occurrence po
       JOIN omop.person p ON p.person_id = po.person_id
       LEFT JOIN (SELECT m.measurement_event_id, m.value_as_concept_id,
                         ROW_NUMBER() OVER (PARTITION BY m.measurement_event_id
                                            ORDER BY m.measurement_datetime, m.measurement_id) AS position
                  FROM   omop.measurement m
                  WHERE  m.measurement_concept_id = 4199571
                    AND  m.meas_event_field_concept_id = 1147082) asa
              ON asa.measurement_event_id = po.procedure_occurrence_id AND asa.position = 1
       LEFT JOIN (SELECT a.procedure_occurrence_id, w.value_as_number,
                         ROW_NUMBER() OVER (PARTITION BY a.procedure_occurrence_id
                                            ORDER BY w.measurement_datetime DESC, w.measurement_id DESC) AS position
                  FROM   omop.procedure_occurrence a
                         JOIN omop.measurement w ON w.person_id = a.person_id
                  WHERE  a.procedure_source_value = 'ANAESTHETIC'
                    AND  w.measurement_concept_id = 3025315
                    AND  w.unit_concept_id = 9529
                    AND  w.value_as_number IS NOT NULL
                    AND  w.measurement_datetime >= DATEADD(day, -7, a.procedure_datetime)
                    AND  w.measurement_datetime <= COALESCE(a.procedure_end_datetime, a.procedure_datetime)) wt
              ON wt.procedure_occurrence_id = po.procedure_occurrence_id AND wt.position = 1
WHERE  po.procedure_source_value = 'ANAESTHETIC'
  AND  po.visit_detail_id IS NOT NULL
