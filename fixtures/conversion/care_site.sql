-- CARE_SITE: one row for each ward or department.
SELECT DENSE_RANK() OVER (ORDER BY w.WARD_KEY)            AS care_site_id,
       LEFT(w.WARD_LABEL, 255)                             AS care_site_name,
       CAST(w.WARD_KEY AS varchar(50))                    AS care_site_source_value
FROM   WARD_DEF w
