-- Among neonates who had an anaesthetic, how many minutes of the anaesthetic did each spend with a mean arterial pressure
-- below 40, and how many of the children died within 90 days of the anaesthetic?
-- A neonate is a child whose age_days at the start of the anaesthetic, in the anaesthetic table, is below 28.
-- The query reads two kinds of mean pressure that the monitor charts: the mean from the cuff, with the concept 21492241
-- (Mean blood pressure by Noninvasive), and the mean from an arterial line, with the concept 21490852 (Invasive Mean
-- blood pressure). It leaves out the mean that the conversion can calculate from a charted pressure, which has the
-- concept 3027598, because that mean was not measured.
-- A reading belongs to the anaesthetic when its measurement_event_id holds the anaesthetic_id and it was taken from the
-- start to the end of the anaesthetic. Where the anaesthetic has no recorded end, the window stays open after the start.
-- The conversion links a reading to its anaesthetic within a margin of 15 minutes before the start and after the end,
-- and this query then keeps only the readings taken from the start to the end.
-- Where a cuff mean and an arterial mean of the same anaesthetic have the same time, the query keeps the arterial one.
-- Where two readings of the same kind have the same time, it keeps the one with the lower measurement_id.
-- Each kept reading stands until the next kept reading of the same anaesthetic, or until the end of the anaesthetic for
-- the last reading, and for no longer than five minutes. The last reading of an anaesthetic with no recorded end stands
-- for no time, because nothing marks when it stopped applying.
-- The minutes below 40 are the sum of the time for which the readings below 40 stand.
-- The child died within 90 days when the death_date lies from the date of the start of the anaesthetic to 90 days after
-- that date, both days included.
-- The query counts anaesthetics, so that a child with two neonatal anaesthetics is counted once for each of them, and
-- it also counts children, so that a child is counted once in each band in which one of their anaesthetics falls.
-- It gives one row for each band of minutes below 40, in order: no mean pressure recorded, none, under 5 minutes,
-- 5 to 14 minutes, and 15 minutes or more, with the number of anaesthetics in the band, the number of those after
-- which the child died within 90 days, the number of children, and the number of those children who died within 90
-- days of one of those anaesthetics.
WITH neonatal AS (
    SELECT a.anaesthetic_id,
           a.person_id,
           a.start_datetime,
           a.end_datetime
    FROM   omop.anaesthetic a
    WHERE  a.age_days < 28
),
reading AS (
    SELECT n.anaesthetic_id,
           n.end_datetime,
           m.measurement_datetime,
           m.value_as_number,
           ROW_NUMBER() OVER (PARTITION BY n.anaesthetic_id, m.measurement_datetime
                              ORDER BY CASE WHEN m.measurement_concept_id = 21490852 THEN 0 ELSE 1 END, m.measurement_id) AS kept_at_time
    FROM   neonatal n
           JOIN omop.measurement m
             ON m.measurement_event_id = n.anaesthetic_id
            AND m.measurement_concept_id IN (21492241, 21490852)
            AND m.measurement_datetime >= n.start_datetime
            AND (n.end_datetime IS NULL OR m.measurement_datetime <= n.end_datetime)
),
stood AS (
    SELECT anaesthetic_id,
           value_as_number,
           measurement_datetime,
           COALESCE(LEAD(measurement_datetime) OVER (PARTITION BY anaesthetic_id ORDER BY measurement_datetime),
                    end_datetime) AS until_datetime
    FROM   reading
    WHERE  kept_at_time = 1
),
low AS (
    SELECT anaesthetic_id,
           SUM(CASE WHEN value_as_number < 40 AND until_datetime IS NOT NULL
                    THEN CASE WHEN DATEDIFF(second, measurement_datetime, until_datetime) > 300 THEN 300
                              ELSE DATEDIFF(second, measurement_datetime, until_datetime) END
                    ELSE 0 END) / 60.0 AS minutes_below_40
    FROM   stood
    GROUP  BY anaesthetic_id
),
banded AS (
    SELECT CASE WHEN l.anaesthetic_id IS NULL THEN 0
                WHEN l.minutes_below_40 = 0 THEN 1
                WHEN l.minutes_below_40 < 5 THEN 2
                WHEN l.minutes_below_40 < 15 THEN 3
                ELSE 4 END AS band,
           n.person_id,
           CASE WHEN d.person_id IS NOT NULL THEN 1 ELSE 0 END AS died
    FROM   neonatal n
           LEFT JOIN low l ON l.anaesthetic_id = n.anaesthetic_id
           LEFT JOIN omop.death d
                  ON d.person_id = n.person_id
                 AND d.death_date >= CAST(n.start_datetime AS date)
                 AND d.death_date <= DATEADD(day, 90, CAST(n.start_datetime AS date))
),
band AS (
    SELECT 0 AS band, 'no mean pressure recorded' AS minutes_below_40
    UNION ALL SELECT 1, 'none'
    UNION ALL SELECT 2, 'under 5 minutes'
    UNION ALL SELECT 3, '5 to 14 minutes'
    UNION ALL SELECT 4, '15 minutes or more'
)
SELECT b.minutes_below_40,
       COUNT(x.band) AS anaesthetics,
       COALESCE(SUM(x.died), 0) AS died_within_90_days,
       COUNT(DISTINCT x.person_id) AS children,
       COUNT(DISTINCT CASE WHEN x.died = 1 THEN x.person_id END) AS children_died_within_90_days
FROM   band b
       LEFT JOIN banded x ON x.band = b.band
GROUP  BY b.band, b.minutes_below_40
ORDER  BY b.band
