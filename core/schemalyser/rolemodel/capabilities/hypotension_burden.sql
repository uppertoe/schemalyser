-- capability: hypotension_burden
-- version: 1
-- For each anaesthetic, the minutes within a window during which the accepted readings of the chosen pressure kinds
-- lay beyond a threshold that depends on the patient's age, with the threshold and the number of readings counted.
-- The capability has the shape minutes_beyond_threshold, and hypoxaemia_burden is the same shape with saturation.
-- The neonatal low mean pressure audit (neonatal_low_mean_pressure.sql) is its first instance, with these settings:
-- the kinds map_arterial and then map_cuff, the direction below, one age band from 0 to 28 days with the threshold
-- 40, the window from the start to the stop, and a reading that stands for at most 5 minutes. The audit keeps its own
-- SQL, and a test holds the two to the same minutes for every neonatal anaesthetic.
-- The parameters, each written in the SQL below as its name between double braces:
--   kinds                  a table of (kind, preference): the kinds of reading that count, where readings of two kinds
--                          share a time, the kind with the lower preference is kept
--   direction              below or above the threshold
--   threshold_by_age_band  a table of (from_days, until_days, threshold): an anaesthetic takes the threshold of the band
--                          in which the patient's age in days at the start falls, counted by calendar date from the
--                          date of birth, from from_days up to but not including until_days, which is empty for no limit
--   window_from_minutes    where the window begins, in minutes after the anaesthetic's start
--   window_until_minutes   where the window ends, in minutes after the start, or empty for the anaesthetic's stop; the
--                          window never runs past a recorded stop
--   reading_stands_minutes the longest time for which one reading stands
-- A test patient is left out, as is an anaesthetic with no start or with a stop before its start.
-- A reading counts when it is accepted, has a value, and was taken within the window, both ends included.
-- Where two kept readings share a time, the one counted is of the preferred kind and, of those, the lower value for
-- below and the higher for above, because the role views carry no order of entry.
-- Each counted reading stands until the next counted reading of the same anaesthetic, or until the window's end for the
-- last, and for no longer than reading_stands_minutes. Where the anaesthetic has no recorded stop, the last reading
-- stands for no time, because nothing marks when it stopped applying.
-- The minutes beyond are the sum of the time for which the readings beyond the threshold stand. An anaesthetic with no
-- counted reading has no minutes, which is unknown and not zero, and one whose patient's age falls in no band has no
-- threshold and no minutes.
-- One row for each anaesthetic: its key, the patient's age in days at the start, the threshold, the readings counted,
-- and the minutes beyond the threshold.
WITH kinds AS (
    {{kinds}}
),
threshold_by_age_band AS (
    {{threshold_by_age_band}}
),
anaesthetic AS (
    SELECT a.anaesthetic_key,
           a.stop_time,
           DATEDIFF(day, p.birth_date, a.start_time) AS age_days,
           DATEADD(minute, {{window_from_minutes}}, a.start_time) AS window_from,
           CASE WHEN {{window_until_minutes}} IS NULL THEN a.stop_time
                WHEN a.stop_time IS NOT NULL AND a.stop_time < DATEADD(minute, {{window_until_minutes}}, a.start_time)
                THEN a.stop_time
                ELSE DATEADD(minute, {{window_until_minutes}}, a.start_time) END AS window_until,
           CASE WHEN a.stop_time IS NULL THEN 0 ELSE 1 END AS has_stop
    FROM   role_anaesthetic a
           JOIN role_patient p ON p.patient_key = a.patient_key
    WHERE  p.is_test = 0
      AND  a.start_time IS NOT NULL
      AND  (a.stop_time IS NULL OR a.stop_time >= a.start_time)
),
banded AS (
    SELECT n.anaesthetic_key,
           n.age_days,
           n.window_from,
           n.window_until,
           n.has_stop,
           t.threshold
    FROM   anaesthetic n
           LEFT JOIN threshold_by_age_band t
                  ON n.age_days >= t.from_days
                 AND (t.until_days IS NULL OR n.age_days < t.until_days)
),
reading AS (
    SELECT b.anaesthetic_key,
           CASE WHEN b.has_stop = 1 THEN b.window_until END AS last_until,
           b.threshold,
           r.reading_time,
           r.value,
           ROW_NUMBER() OVER (PARTITION BY b.anaesthetic_key, r.reading_time
                              ORDER BY k.preference,
                                       CASE WHEN {{direction}} = 'below' THEN r.value ELSE 0 - r.value END) AS kept_at_time
    FROM   banded b
           JOIN role_reading r
             ON r.anaesthetic_key = b.anaesthetic_key
            AND r.accepted = 1
            AND r.value IS NOT NULL
            AND r.reading_time >= b.window_from
            AND (b.window_until IS NULL OR r.reading_time <= b.window_until)
           JOIN kinds k ON k.kind = r.kind
),
stood AS (
    SELECT anaesthetic_key,
           threshold,
           value,
           reading_time,
           COALESCE(LEAD(reading_time) OVER (PARTITION BY anaesthetic_key ORDER BY reading_time), last_until) AS until_time
    FROM   reading
    WHERE  kept_at_time = 1
),
beyond AS (
    SELECT anaesthetic_key,
           COUNT(*) AS readings,
           SUM(CASE WHEN until_time IS NOT NULL
                     AND ((value < threshold AND {{direction}} = 'below') OR (value > threshold AND {{direction}} = 'above'))
                    THEN CASE WHEN DATEDIFF(second, reading_time, until_time) > 60 * {{reading_stands_minutes}}
                              THEN 60 * {{reading_stands_minutes}}
                              ELSE DATEDIFF(second, reading_time, until_time) END
                    ELSE 0 END) / 60.0 AS minutes_beyond
    FROM   stood
    GROUP  BY anaesthetic_key
)
SELECT b.anaesthetic_key,
       b.age_days,
       b.threshold,
       COALESCE(x.readings, 0) AS readings,
       CASE WHEN b.threshold IS NULL THEN NULL ELSE x.minutes_beyond END AS minutes_beyond
FROM   banded b
       LEFT JOIN beyond x ON x.anaesthetic_key = b.anaesthetic_key
