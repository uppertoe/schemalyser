-- role_diagnosis for the invented world: one row for each diagnosis recorded for a hospital visit, with the patient of
-- that visit and the code and name from the list of diagnoses. The list's code is in the national classification, and
-- the invented world records no time at which a diagnosis was noted, so the view gives the time empty.
SELECT v.PERSON_KEY                                                    AS patient_key,
       vd.VISIT_KEY                                                    AS stay_key,
       dd.ICD_CODE                                                     AS code,
       'national classification'                                       AS code_system,
       dd.DIAG_LABEL                                                   AS name,
       CASE WHEN vd.PRIMARY_FLAG = 'Y' THEN 1
            WHEN vd.PRIMARY_FLAG = 'N' THEN 0 END                      AS is_principal,
       NULL                                                            AS recorded_time
FROM   VISIT_DIAGNOSIS vd
       JOIN VISIT v ON v.VISIT_KEY = vd.VISIT_KEY
       LEFT JOIN DIAG_DEF dd ON dd.DIAG_KEY = vd.DIAG_KEY
