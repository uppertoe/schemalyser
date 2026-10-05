-- The number of further anaesthetics that the anaesthesia layer has left out because it found no hospital visit for them in the core, for example where an anaesthetic has no theatre case or belongs to a test patient, is {count}.
-- The count reads the source table, so it is specific to the site. It gives a number and never an identifier.
SELECT COUNT(*) AS anaesthetics
FROM   ANAES_RECORD ar
       LEFT JOIN (SELECT DISTINCT d.visit_detail_source_value FROM omop.visit_detail d) vd
              ON vd.visit_detail_source_value = CAST(ar.ANAES_KEY AS varchar(50))
WHERE  ar.ANAES_START_TS IS NOT NULL
  AND  (ar.ANAES_STOP_TS IS NULL OR ar.ANAES_STOP_TS >= ar.ANAES_START_TS)
  AND  vd.visit_detail_source_value IS NULL
