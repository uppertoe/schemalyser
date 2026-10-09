-- capability: hypothermia
-- version: 1
-- For each anaesthetic, the minutes within a window during which the accepted temperatures lay below a threshold, the
-- lowest temperature in the window, and the temperature on arrival in recovery, taken as the first temperature of the
-- anaesthetic's record after its stop.
-- Until the events part is promoted, the recovery window is anchored to the anaesthetic's stop: it runs from just after
-- the stop to recovery_window_minutes after it. Once recovery-in and recovery-out events exist, the window is theirs,
-- and that change is a new version of this capability.
-- The parameters, each written in the SQL below as its name between double braces:
--   threshold_celsius        the threshold, in degrees Celsius, below which the minutes are counted
--   window_from_minutes      where the window begins, in minutes after the anaesthetic's start
--   window_until_minutes     where the window ends, in minutes after the start, or empty for the anaesthetic's stop; the
--                            window never runs past a recorded stop
--   reading_stands_minutes   the longest time for which one temperature stands
--   recovery_window_minutes  how long after the stop a temperature still counts as the one taken on arrival
-- A test patient is left out, as is an anaesthetic with no start or with a stop before its start.
-- A temperature counts when it is accepted, has a value, and was taken within the window, both ends included. Where two
-- temperatures share a time, the lower is kept, because the role views carry no order of entry.
-- Each kept temperature stands until the next kept temperature, or until the window's end for the last, and for no
-- longer than reading_stands_minutes. Where the anaesthetic has no recorded stop, the last temperature stands for no
-- time, because nothing marks when it stopped applying, and no temperature can be taken as the one on arrival.
-- An anaesthetic with no kept temperature has no minutes and no lowest temperature, which are unknown and not zero,
-- and one with no temperature in the recovery window has no temperature on arrival.
-- One row for each anaesthetic: its key, the temperatures counted, the minutes below the threshold, the lowest
-- temperature, and the time and value of the temperature on arrival.
WITH anaesthetic AS (
    SELECT a.anaesthetic_key,
           a.stop_time,
           DATEADD(minute, {{window_from_minutes}}, a.start_time) AS window_from,
           CASE WHEN {{window_until_minutes}} IS NULL THEN a.stop_time
                WHEN a.stop_time IS NOT NULL AND a.stop_time < DATEADD(minute, {{window_until_minutes}}, a.start_time)
                THEN a.stop_time
                ELSE DATEADD(minute, {{window_until_minutes}}, a.start_time) END AS window_until
    FROM   role_anaesthetic a
           JOIN role_patient p ON p.patient_key = a.patient_key
    WHERE  p.is_test = 0
      AND  a.start_time IS NOT NULL
      AND  (a.stop_time IS NULL OR a.stop_time >= a.start_time)
),
reading AS (
    SELECT n.anaesthetic_key,
           CASE WHEN n.stop_time IS NOT NULL THEN n.window_until END AS last_until,
           r.reading_time,
           r.value,
           ROW_NUMBER() OVER (PARTITION BY n.anaesthetic_key, r.reading_time ORDER BY r.value) AS kept_at_time
    FROM   anaesthetic n
           JOIN role_reading r
             ON r.anaesthetic_key = n.anaesthetic_key
            AND r.kind = 'temperature'
            AND r.accepted = 1
            AND r.value IS NOT NULL
            AND r.reading_time >= n.window_from
            AND (n.window_until IS NULL OR r.reading_time <= n.window_until)
),
stood AS (
    SELECT anaesthetic_key,
           value,
           reading_time,
           COALESCE(LEAD(reading_time) OVER (PARTITION BY anaesthetic_key ORDER BY reading_time), last_until) AS until_time
    FROM   reading
    WHERE  kept_at_time = 1
),
below AS (
    SELECT anaesthetic_key,
           COUNT(*) AS readings,
           MIN(value) AS lowest,
           SUM(CASE WHEN until_time IS NOT NULL AND value < {{threshold_celsius}}
                    THEN CASE WHEN DATEDIFF(second, reading_time, until_time) > 60 * {{reading_stands_minutes}}
                              THEN 60 * {{reading_stands_minutes}}
                              ELSE DATEDIFF(second, reading_time, until_time) END
                    ELSE 0 END) / 60.0 AS minutes_below
    FROM   stood
    GROUP  BY anaesthetic_key
),
arrival AS (
    SELECT n.anaesthetic_key,
           r.reading_time,
           r.value,
           ROW_NUMBER() OVER (PARTITION BY n.anaesthetic_key ORDER BY r.reading_time, r.value) AS position
    FROM   anaesthetic n
           JOIN role_reading r
             ON r.anaesthetic_key = n.anaesthetic_key
            AND r.kind = 'temperature'
            AND r.accepted = 1
            AND r.value IS NOT NULL
            AND n.stop_time IS NOT NULL
            AND r.reading_time > n.stop_time
            AND r.reading_time <= DATEADD(minute, {{recovery_window_minutes}}, n.stop_time)
)
SELECT n.anaesthetic_key,
       COALESCE(b.readings, 0) AS readings,
       b.minutes_below,
       b.lowest,
       v.reading_time AS arrival_time,
       v.value AS arrival_temperature
FROM   anaesthetic n
       LEFT JOIN below b ON b.anaesthetic_key = n.anaesthetic_key
       LEFT JOIN arrival v ON v.anaesthetic_key = n.anaesthetic_key AND v.position = 1
