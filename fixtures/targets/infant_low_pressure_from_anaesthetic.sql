-- Of children under one year who had a general anaesthetic, what share had a systolic blood
-- pressure below 60 recorded during the anaesthetic?
-- This is the question of infant_low_pressure.sql, asked of the custom table ANAESTHETIC, which holds
-- one row for each anaesthetic, joined to the raw readings in MEASUREMENT. A reading belongs to the
-- anaesthetic when its measurement_event_id holds the anaesthetic_id, which the conversion fills only for
-- a reading taken from the start to the end with a margin either side. The query keeps its own window,
-- from the start to the end, so that it counts only readings during the anaesthetic, and the window stays
-- open where the anaesthetic has no recorded stop and so an empty end. The systolic pressure has the
-- concept 3004249. A child is under one year when age_years, the whole years completed by calendar
-- date from the date of birth to the date of the start, is 0, so that a child is one year old from the
-- date of the first birthday. infant_low_pressure.sql uses the same rule, so the two queries agree.
SELECT COUNT(DISTINCT a.anaesthetic_id) AS anaesthetics,
       COUNT(DISTINCT CASE WHEN m.measurement_id IS NOT NULL THEN a.anaesthetic_id END) AS with_low_systolic
FROM   omop.anaesthetic a
       LEFT JOIN omop.measurement m
              ON m.measurement_event_id = a.anaesthetic_id
             AND m.measurement_concept_id = 3004249
             AND m.value_as_number < 60
             AND m.measurement_datetime >= a.start_datetime
             AND (a.end_datetime IS NULL OR m.measurement_datetime <= a.end_datetime)
WHERE  a.anaesthesia_type_concept_id = 4174669
  AND  a.age_years < 1
