WITH cases AS (
    SELECT ar.ANAES_KEY,
           ar.RISK_GRADE_CAT,
           DATEDIFF(DAY, pm.BIRTH_TS, tc.CASE_DATE) AS age_days
    FROM   ANAES_RECORD ar
           JOIN THEATRE_CASE tc  ON tc.CASE_KEY = ar.CASE_KEY
           JOIN PERSON_MASTER pm ON pm.PERSON_KEY = tc.PERSON_KEY
    WHERE  tc.CASE_STATUS_CAT = 2
),
banded AS (
    SELECT ANAES_KEY,
           RISK_GRADE_CAT,
           CASE WHEN age_days < 28 THEN 'neonate'
                WHEN age_days < 365 THEN 'infant'
                WHEN age_days < 365 * 5 THEN 'preschool'
                ELSE 'older' END AS age_band
    FROM   cases
)
SELECT b.age_band,
       rg.LABEL AS risk_grade,
       COUNT(*) AS n
FROM   banded b
       LEFT JOIN LK_RISK_GRADE rg ON rg.RISK_GRADE_CAT = b.RISK_GRADE_CAT
WHERE  b.RISK_GRADE_CAT IN (3, 4, 5)
GROUP BY b.age_band, rg.LABEL
HAVING COUNT(*) >= 5;
