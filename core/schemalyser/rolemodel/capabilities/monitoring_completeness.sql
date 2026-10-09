-- capability: monitoring_completeness
-- version: 1
-- For each anaesthetic, how completely a kind of reading covers a window: the share of the window that lies within the
-- expected interval after a reading, and the longest gap in the window without one.
-- The parameters, each written in the SQL below as its name between double braces:
--   kinds                      a table of (kind): the kinds of reading that count, any of which covers the window
--   expected_interval_minutes  how long after a reading the window counts as covered, such as 5 for a cuff pressure
--   window_from_minutes        where the window begins, in minutes after the anaesthetic's start
--   window_until_minutes       where the window ends, in minutes after the start, or empty for the anaesthetic's stop;
--                              the window never runs past a recorded stop
-- A test patient is left out, as is an anaesthetic with no start or with a stop before its start.
-- A reading counts when it is accepted, has a value, and was taken within the window, both ends included, and two
-- readings at the same time count once.
-- The time from a reading to the next, or to the window's end for the last, is covered up to the expected interval, and
-- the time before the first reading is not covered. The share is the covered time over the window's length.
-- The gaps are the time from the window's start to the first reading, from each reading to the next, and from the last
-- reading to the window's end; with no reading, the whole window is one gap and none of it is covered.
-- Where the anaesthetic has no recorded stop, the window's end is not known, because the anaesthetic may have ended
-- before it, so the share and the longest gap are empty, which is unknown and not zero, and the readings are still
-- counted.
-- One row for each anaesthetic: its key, the window's length in minutes, the readings counted, the minutes covered,
-- the share covered, and the longest gap in minutes.
WITH kinds AS (
    {{kinds}}
),
anaesthetic AS (
    SELECT a.anaesthetic_key,
           DATEADD(minute, {{window_from_minutes}}, a.start_time) AS window_from,
           CASE WHEN a.stop_time IS NULL THEN NULL
                WHEN {{window_until_minutes}} IS NULL THEN a.stop_time
                WHEN a.stop_time < DATEADD(minute, {{window_until_minutes}}, a.start_time) THEN a.stop_time
                ELSE DATEADD(minute, {{window_until_minutes}}, a.start_time) END AS window_until
    FROM   role_anaesthetic a
           JOIN role_patient p ON p.patient_key = a.patient_key
    WHERE  p.is_test = 0
      AND  a.start_time IS NOT NULL
      AND  (a.stop_time IS NULL OR a.stop_time >= a.start_time)
),
reading AS (
    SELECT DISTINCT n.anaesthetic_key,
           n.window_from,
           n.window_until,
           r.reading_time
    FROM   anaesthetic n
           JOIN role_reading r
             ON r.anaesthetic_key = n.anaesthetic_key
            AND r.accepted = 1
            AND r.value IS NOT NULL
            AND r.reading_time >= n.window_from
            AND (n.window_until IS NULL OR r.reading_time <= n.window_until)
           JOIN kinds k ON k.kind = r.kind
),
spaced AS (
    SELECT anaesthetic_key,
           window_from,
           window_until,
           reading_time,
           COALESCE(LEAD(reading_time) OVER (PARTITION BY anaesthetic_key ORDER BY reading_time), window_until) AS next_time,
           ROW_NUMBER() OVER (PARTITION BY anaesthetic_key ORDER BY reading_time) AS position
    FROM   reading
),
summed AS (
    SELECT anaesthetic_key,
           COUNT(*) AS readings,
           SUM(CASE WHEN DATEDIFF(second, reading_time, next_time) > 60 * {{expected_interval_minutes}}
                    THEN 60 * {{expected_interval_minutes}}
                    ELSE DATEDIFF(second, reading_time, next_time) END) / 60.0 AS covered_minutes,
           MAX(DATEDIFF(second, reading_time, next_time)) AS longest_between_seconds,
           MAX(CASE WHEN position = 1 THEN DATEDIFF(second, window_from, reading_time) END) AS first_gap_seconds
    FROM   spaced
    GROUP  BY anaesthetic_key
),
measured AS (
    SELECT n.anaesthetic_key,
           CASE WHEN n.window_until IS NULL THEN NULL ELSE DATEDIFF(second, n.window_from, n.window_until) / 60.0 END
               AS window_minutes,
           COALESCE(s.readings, 0) AS readings,
           CASE WHEN n.window_until IS NULL THEN NULL ELSE COALESCE(s.covered_minutes, 0) END AS covered_minutes,
           CASE WHEN n.window_until IS NULL THEN NULL
                WHEN s.anaesthetic_key IS NULL THEN DATEDIFF(second, n.window_from, n.window_until) / 60.0
                WHEN s.first_gap_seconds > s.longest_between_seconds THEN s.first_gap_seconds / 60.0
                ELSE s.longest_between_seconds / 60.0 END AS longest_gap_minutes
    FROM   anaesthetic n
           LEFT JOIN summed s ON s.anaesthetic_key = n.anaesthetic_key
)
SELECT anaesthetic_key,
       window_minutes,
       readings,
       covered_minutes,
       CASE WHEN window_minutes > 0 THEN covered_minutes / window_minutes END AS share_covered,
       longest_gap_minutes
FROM   measured
