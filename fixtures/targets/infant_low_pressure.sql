-- Of children under one year who had a general anaesthetic, what share had a systolic blood
-- pressure below 60 recorded during the anaesthetic?
-- The anaesthetic is a PROCEDURE_OCCURRENCE with the concept 4174669 (general anaesthesia). A
-- measurement belongs to it when its measurement_event_id holds the anaesthetic's
-- procedure_occurrence_id, which the conversion fills only for a reading taken from the start to the
-- end with a margin either side. The query keeps its own window, from the start to the end, so that
-- it counts only readings during the anaesthetic, and an anaesthetic with no recorded stop has an
-- empty end, so its window stays open. The systolic pressure has the concept 3004249. The child's age is
-- taken from person.birth_datetime at the start of the anaesthetic, by calendar date: a child is
-- under one year until the date of the first birthday, and a child born on 29 February turns one
-- on 1 March in a year without that date. This is the rule of anaesthetic.age_years. Each date is
-- written as the number yyyymmdd, and the child has completed a year once the difference reaches 10000.
SELECT COUNT(DISTINCT po.procedure_occurrence_id) AS anaesthetics,
       COUNT(DISTINCT CASE WHEN m.measurement_id IS NOT NULL THEN po.procedure_occurrence_id END) AS with_low_systolic
FROM   omop.procedure_occurrence po
       JOIN omop.person p ON p.person_id = po.person_id
       LEFT JOIN omop.measurement m
              ON m.measurement_event_id = po.procedure_occurrence_id
             AND m.measurement_concept_id = 3004249
             AND m.value_as_number < 60
             AND m.measurement_datetime >= po.procedure_datetime
             AND (po.procedure_end_datetime IS NULL OR m.measurement_datetime <= po.procedure_end_datetime)
WHERE  po.procedure_concept_id = 4174669
  AND  (YEAR(po.procedure_datetime) * 10000 + MONTH(po.procedure_datetime) * 100 + DAY(po.procedure_datetime))
       - (YEAR(p.birth_datetime) * 10000 + MONTH(p.birth_datetime) * 100 + DAY(p.birth_datetime)) < 10000
