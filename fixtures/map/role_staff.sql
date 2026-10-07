-- role_staff for the invented world: one row for each line of the anaesthetic's staff list, which records each person
-- who took part with a role and the times at which that person took over and handed over the care of the patient.
-- The staff role 1 is taken to be the anaesthetist, as the only role that the team's handover query reads, and every
-- other role is other until a person gives its meaning. The invented world records no grade, so the view gives it empty.
SELECT st.ANAES_KEY                                              AS anaesthetic_key,
       st.STAFF_KEY                                              AS person_key,
       CASE WHEN st.ROLE_CAT = 1 THEN 'anaesthetist'
            WHEN st.ROLE_CAT IS NOT NULL THEN 'other' END        AS role,
       NULL                                                      AS grade,
       st.START_TS                                               AS present_from,
       st.END_TS                                                 AS present_to
FROM   ANAES_STAFF st
