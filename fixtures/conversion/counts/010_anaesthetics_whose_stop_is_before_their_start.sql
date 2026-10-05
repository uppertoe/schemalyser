-- The number of anaesthetics that the anaesthesia layer has left out because the recorded stop is before the recorded start is {count}.
-- The count reads the source table, so it is specific to the site. It gives a number and never an identifier.
SELECT COUNT(*) AS anaesthetics
FROM   ANAES_RECORD ar
WHERE  ar.ANAES_START_TS IS NOT NULL
  AND  ar.ANAES_STOP_TS < ar.ANAES_START_TS
