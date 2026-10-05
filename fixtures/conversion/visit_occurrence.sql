-- VISIT_OCCURRENCE: one row for each hospital visit of a person who was written to PERSON.
SELECT DENSE_RANK() OVER (ORDER BY v.VISIT_KEY)                                        AS visit_occurrence_id,
       p.person_id                                                                     AS person_id,
       9201                                                                            AS visit_concept_id,
       CAST(v.ADMIT_TS AS date)                                                        AS visit_start_date,
       v.ADMIT_TS                                                                      AS visit_start_datetime,
       CAST(CASE WHEN v.DISCH_TS >= v.ADMIT_TS THEN v.DISCH_TS ELSE v.ADMIT_TS END AS date) AS visit_end_date,
       CASE WHEN v.DISCH_TS >= v.ADMIT_TS THEN v.DISCH_TS ELSE v.ADMIT_TS END          AS visit_end_datetime,
       32817                                                                           AS visit_type_concept_id,
       cs.care_site_id                                                                 AS care_site_id,
       CAST(v.VISIT_KEY AS varchar(50))                                                AS visit_source_value
FROM   VISIT v
       JOIN omop.person p ON p.person_source_value = v.PERSON_KEY
       LEFT JOIN omop.care_site cs ON cs.care_site_source_value = CAST(v.WARD_KEY AS varchar(50))
WHERE  v.ADMIT_TS IS NOT NULL
