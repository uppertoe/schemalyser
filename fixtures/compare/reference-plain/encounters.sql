-- VISIT_OCCURRENCE is recognised by the columns of the final SELECT, since the file's name names no OMOP table.
-- It is planted to agree with our conversion, although it joins the patient and ward tables directly where ours
-- reaches them through omop.person and omop.care_site.
WITH visits AS (
    SELECT v.VISIT_KEY, v.PERSON_KEY, v.WARD_KEY, v.ADMIT_TS,
           CASE WHEN v.DISCH_TS >= v.ADMIT_TS THEN v.DISCH_TS ELSE v.ADMIT_TS END AS END_TS
    FROM   dbo.VISIT v
    WHERE  v.ADMIT_TS IS NOT NULL
)
SELECT DENSE_RANK() OVER (ORDER BY vs.VISIT_KEY) AS visit_occurrence_id,
       DENSE_RANK() OVER (ORDER BY pm.PERSON_KEY) AS person_id,
       9201 AS visit_concept_id,
       CAST(vs.ADMIT_TS AS date) AS visit_start_date,
       vs.ADMIT_TS AS visit_start_datetime,
       CAST(vs.END_TS AS date) AS visit_end_date,
       vs.END_TS AS visit_end_datetime,
       32817 AS visit_type_concept_id,
       DENSE_RANK() OVER (ORDER BY w.WARD_KEY) AS care_site_id,
       CAST(vs.VISIT_KEY AS varchar(50)) AS visit_source_value
FROM   visits vs
       JOIN dbo.PERSON_MASTER pm ON pm.PERSON_KEY = vs.PERSON_KEY
       LEFT JOIN dbo.WARD_DEF w ON w.WARD_KEY = vs.WARD_KEY
