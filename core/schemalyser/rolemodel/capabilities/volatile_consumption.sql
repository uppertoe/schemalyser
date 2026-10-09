-- capability: volatile_consumption
-- version: 1
-- For each anaesthetic and each volatile agent charted in it, the millilitres of liquid agent consumed, worked out from
-- the fresh gas flow and the expired concentration integrated over time, and the carbon dioxide equivalent of that
-- liquid.
-- The vapour delivered in a stretch of time is the fresh gas flow in litres a minute, times 1000, times the expired
-- concentration in per cent over 100, times the minutes. The expired concentration stands in for the delivered one,
-- as the department's list of measures asks. The liquid is the vapour over the agent's millilitres of vapour for each
-- millilitre of liquid, and the carbon dioxide equivalent is the liquid times the agent's kilograms for each millilitre.
-- Both factors are tables given as parameters, because their values are the department's choice and a published
-- source should be named where they are set; this file holds no factor of its own.
-- The parameters, each written in the SQL below as its name between double braces:
--   vapour_per_liquid        a table of (kind, vapour_ml_per_liquid_ml): the expired concentration's kind of each agent
--                            that counts, such as expired_sevoflurane, with its millilitres of vapour for each
--                            millilitre of liquid
--   co2e_per_liquid          a table of (kind, co2e_kg_per_liquid_ml): the kilograms of carbon dioxide equivalent for each
--                            millilitre of each agent's liquid; an agent without a row has no carbon dioxide equivalent
--   reading_stands_minutes   the longest time for which one reading of the flow or of a concentration stands
--   window_from_minutes      where the window begins, in minutes after the anaesthetic's start
--   window_until_minutes     where the window ends, in minutes after the start, or empty for the anaesthetic's stop; the
--                            window never runs past a recorded stop
-- A test patient is left out, as is an anaesthetic with no start or with a stop before its start.
-- A reading of fresh_gas_flow or of an agent's kind counts when it is accepted, has a value, and was taken within the
-- window, both ends included. Where two readings of one kind share a time, the lower is kept.
-- The time of an anaesthetic is cut at every kept reading of the flow and of the agent. In each stretch, from one cut
-- to the next, or to the window's end for the last, the flow and the concentration are those of the latest kept
-- reading of each at or before the stretch's start, and the stretch counts only while both readings still stand, for no
-- longer than reading_stands_minutes after each was taken. Where the anaesthetic has no recorded stop, the last
-- stretch counts for no time, because nothing marks when the readings stopped applying.
-- One row for each anaesthetic and each agent with at least one kept reading: the anaesthetic's key, the agent's kind,
-- the minutes during which both the flow and the agent's concentration stood, the millilitres of vapour, the
-- millilitres of liquid and the kilograms of carbon dioxide equivalent.
WITH vapour_per_liquid AS (
    {{vapour_per_liquid}}
),
co2e_per_liquid AS (
    {{co2e_per_liquid}}
),
anaesthetic AS (
    SELECT a.anaesthetic_key,
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
flow AS (
    SELECT n.anaesthetic_key,
           r.reading_time,
           MIN(r.value) AS value
    FROM   anaesthetic n
           JOIN role_reading r
             ON r.anaesthetic_key = n.anaesthetic_key
            AND r.kind = 'fresh_gas_flow'
            AND r.accepted = 1
            AND r.value IS NOT NULL
            AND r.reading_time >= n.window_from
            AND (n.window_until IS NULL OR r.reading_time <= n.window_until)
    GROUP  BY n.anaesthetic_key, r.reading_time
),
agent AS (
    SELECT n.anaesthetic_key,
           r.kind,
           r.reading_time,
           MIN(r.value) AS value
    FROM   anaesthetic n
           JOIN role_reading r
             ON r.anaesthetic_key = n.anaesthetic_key
            AND r.accepted = 1
            AND r.value IS NOT NULL
            AND r.reading_time >= n.window_from
            AND (n.window_until IS NULL OR r.reading_time <= n.window_until)
           JOIN vapour_per_liquid v ON v.kind = r.kind
    GROUP  BY n.anaesthetic_key, r.kind, r.reading_time
),
charted AS (
    SELECT DISTINCT anaesthetic_key, kind
    FROM   agent
),
cut AS (
    SELECT g.anaesthetic_key, g.kind, g.reading_time AS at_time
    FROM   agent g
    UNION
    SELECT c.anaesthetic_key, c.kind, f.reading_time
    FROM   charted c
           JOIN flow f ON f.anaesthetic_key = c.anaesthetic_key
),
stretch AS (
    SELECT t.anaesthetic_key,
           t.kind,
           t.at_time,
           COALESCE(LEAD(t.at_time) OVER (PARTITION BY t.anaesthetic_key, t.kind ORDER BY t.at_time),
                    CASE WHEN n.has_stop = 1 THEN n.window_until END) AS next_time
    FROM   cut t
           JOIN anaesthetic n ON n.anaesthetic_key = t.anaesthetic_key
),
flow_at AS (
    SELECT s.anaesthetic_key, s.kind, s.at_time, f.reading_time, f.value,
           ROW_NUMBER() OVER (PARTITION BY s.anaesthetic_key, s.kind, s.at_time ORDER BY f.reading_time DESC) AS position
    FROM   stretch s
           JOIN flow f ON f.anaesthetic_key = s.anaesthetic_key AND f.reading_time <= s.at_time
),
agent_at AS (
    SELECT s.anaesthetic_key, s.kind, s.at_time, g.reading_time, g.value,
           ROW_NUMBER() OVER (PARTITION BY s.anaesthetic_key, s.kind, s.at_time ORDER BY g.reading_time DESC) AS position
    FROM   stretch s
           JOIN agent g ON g.anaesthetic_key = s.anaesthetic_key AND g.kind = s.kind AND g.reading_time <= s.at_time
),
standing AS (
    SELECT s.anaesthetic_key,
           s.kind,
           fa.value AS flow,
           aa.value AS concentration,
           DATEDIFF(second, s.at_time, s.next_time) AS stretch_seconds,
           60 * {{reading_stands_minutes}} - DATEDIFF(second, fa.reading_time, s.at_time) AS flow_seconds_left,
           60 * {{reading_stands_minutes}} - DATEDIFF(second, aa.reading_time, s.at_time) AS agent_seconds_left
    FROM   stretch s
           JOIN flow_at fa ON fa.anaesthetic_key = s.anaesthetic_key AND fa.kind = s.kind AND fa.at_time = s.at_time
                          AND fa.position = 1
           JOIN agent_at aa ON aa.anaesthetic_key = s.anaesthetic_key AND aa.kind = s.kind AND aa.at_time = s.at_time
                           AND aa.position = 1
    WHERE  s.next_time IS NOT NULL
),
counted AS (
    SELECT anaesthetic_key,
           kind,
           flow,
           concentration,
           CASE WHEN stretch_seconds <= flow_seconds_left AND stretch_seconds <= agent_seconds_left THEN stretch_seconds
                WHEN flow_seconds_left <= agent_seconds_left THEN flow_seconds_left
                ELSE agent_seconds_left END AS seconds
    FROM   standing
),
summed AS (
    SELECT anaesthetic_key,
           kind,
           SUM(CASE WHEN seconds > 0 THEN seconds ELSE 0 END) / 60.0 AS minutes,
           SUM(CASE WHEN seconds > 0 THEN flow * 1000.0 * concentration / 100.0 * seconds / 60.0 ELSE 0 END) AS vapour_ml
    FROM   counted
    GROUP  BY anaesthetic_key, kind
)
SELECT c.anaesthetic_key,
       c.kind AS agent,
       COALESCE(s.minutes, 0) AS minutes,
       COALESCE(s.vapour_ml, 0) AS vapour_ml,
       COALESCE(s.vapour_ml, 0) / v.vapour_ml_per_liquid_ml AS liquid_ml,
       COALESCE(s.vapour_ml, 0) / v.vapour_ml_per_liquid_ml * e.co2e_kg_per_liquid_ml AS co2e_kg
FROM   charted c
       JOIN vapour_per_liquid v ON v.kind = c.kind
       LEFT JOIN co2e_per_liquid e ON e.kind = c.kind
       LEFT JOIN summed s ON s.anaesthetic_key = c.anaesthetic_key AND s.kind = c.kind
