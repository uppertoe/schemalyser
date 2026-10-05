-- At least nine in ten of the source's anaesthetics that have a start, and no stop before it, became a visit detail.
-- An anaesthetic with no recorded stop is counted, because the layer writes it, and one whose stop is before its
-- start is not, because the layer leaves it out and the run counts it separately.
-- Fewer means that the steps no longer find most of the core's visits, for example because the core
-- has changed what it keeps in visit_source_value. The gate reads the source table, so it is specific to the site.
SELECT COUNT(*) AS anaesthetics, SUM(CASE WHEN vd.visit_detail_source_value IS NOT NULL THEN 1 ELSE 0 END) AS written
FROM   ANAES_RECORD ar
       LEFT JOIN (SELECT DISTINCT d.visit_detail_source_value FROM omop.visit_detail d) vd
              ON vd.visit_detail_source_value = CAST(ar.ANAES_KEY AS varchar(50))
WHERE  ar.ANAES_START_TS IS NOT NULL
  AND  (ar.ANAES_STOP_TS IS NULL OR ar.ANAES_STOP_TS >= ar.ANAES_START_TS)
HAVING SUM(CASE WHEN vd.visit_detail_source_value IS NOT NULL THEN 1 ELSE 0 END) < 0.9 * COUNT(*)
