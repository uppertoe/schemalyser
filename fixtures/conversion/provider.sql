-- PROVIDER: one row for each member of staff. Names are left behind.
SELECT DENSE_RANK() OVER (ORDER BY s.STAFF_KEY)            AS provider_id,
       0                                   AS specialty_concept_id,
       CAST(s.STAFF_KEY AS varchar(50))                    AS provider_source_value
FROM   STAFF_MASTER s
