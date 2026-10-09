-- VISIT_DETAIL is recognised by the file's name. It is planted to differ from our conversion in one join: the
-- anaesthetic reaches its hospital visit through the visit key on the anaesthetic record, where ours goes through
-- the theatre case.
SELECT DENSE_RANK() OVER (ORDER BY a.ANAES_KEY) AS visit_detail_id,
       vo.person_id AS person_id,
       vo.visit_concept_id AS visit_detail_concept_id,
       CAST(a.ANAES_START_TS AS date) AS visit_detail_start_date,
       a.ANAES_START_TS AS visit_detail_start_datetime,
       CAST(COALESCE(a.ANAES_STOP_TS, a.ANAES_START_TS) AS date) AS visit_detail_end_date,
       a.ANAES_STOP_TS AS visit_detail_end_datetime,
       32817 AS visit_detail_type_concept_id,
       pr.provider_id AS provider_id,
       CAST(a.ANAES_KEY AS varchar(50)) AS visit_detail_source_value,
       vo.visit_occurrence_id AS visit_occurrence_id
FROM   dbo.ANAES_RECORD a
       INNER JOIN cdm.visit_occurrence vo ON vo.visit_source_value = CAST(a.VISIT_KEY AS varchar(50))
       LEFT JOIN (SELECT s.ANAES_KEY, s.STAFF_KEY, ROW_NUMBER() OVER (PARTITION BY s.ANAES_KEY ORDER BY s.SEQ) AS rn
                  FROM   dbo.ANAES_STAFF s) first_staff ON first_staff.ANAES_KEY = a.ANAES_KEY AND first_staff.rn = 1
       LEFT JOIN cdm.provider pr ON pr.provider_source_value = first_staff.STAFF_KEY
WHERE  a.ANAES_START_TS IS NOT NULL
  AND  (a.ANAES_STOP_TS IS NULL OR a.ANAES_STOP_TS >= a.ANAES_START_TS)
