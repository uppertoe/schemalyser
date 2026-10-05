-- role_anaesthetic for the invented world: one row for each anaesthetic record that reaches a hospital visit with an
-- admission time through its theatre case, as the existing conversion keeps it. The patient is the hospital visit's
-- patient, as the conversion takes it. A missing or misordered start or stop is kept here, so that the counts see it,
-- and the audit leaves such an anaesthetic out.
SELECT ar.ANAES_KEY      AS anaesthetic_key,
       v.PERSON_KEY      AS patient_key,
       ar.ANAES_START_TS AS start_time,
       ar.ANAES_STOP_TS  AS stop_time
FROM   ANAES_RECORD ar
       JOIN THEATRE_CASE tc ON tc.CASE_KEY = ar.CASE_KEY
       JOIN VISIT v ON v.VISIT_KEY = tc.VISIT_KEY
WHERE  v.ADMIT_TS IS NOT NULL
