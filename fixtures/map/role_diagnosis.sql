-- role_diagnosis for the invented world: one row for each diagnosis recorded for a hospital visit, with the patient of
-- that visit and the diagnosis as the local key of the mapping view of diagnoses. The invented map has not yet
-- translated the list's codes, so every recorded diagnosis gives the key unlisted until a person translates them. The
-- invented world records no time at which a diagnosis was noted or entered and no correction of one, so the view gives
-- those empty, and every row is of the source kind coded_record.
SELECT CONCAT(CAST(vd.VISIT_KEY AS varchar(254)), '-', CAST(vd.SEQ AS varchar(254))) AS diagnosis_key,
       v.PERSON_KEY                                                    AS patient_key,
       vd.VISIT_KEY                                                    AS stay_key,
       CASE WHEN vd.DIAG_KEY IS NOT NULL THEN 'unlisted' END           AS diagnosis,
       CASE WHEN vd.PRIMARY_FLAG = 'Y' THEN 1
            WHEN vd.PRIMARY_FLAG = 'N' THEN 0 END                      AS is_principal,
       NULL                                                            AS recorded_time,
       'coded_record'                                                  AS source_kind,
       NULL                                                            AS documented_time,
       NULL                                                            AS amends_key
FROM   VISIT_DIAGNOSIS vd
       JOIN VISIT v ON v.VISIT_KEY = vd.VISIT_KEY
