-- DRUG_EXPOSURE, infusions, over the roles: one row for each interval during which an infusion ran at one rate.
-- The step reads role_drug, map_drug_concept and the OMOP tables that the core and the anaesthesia layer have already
-- written, and nothing of the hospital's own tables, so that it runs unchanged at any hospital whose schema supplies
-- role_drug. It has replaced drug_exposure_infusion.sql, which wrote the infusions directly from the source tables
-- and is kept as a recorded alternative on the direct route.
-- The events of one infusion are the rows of role_drug that share an order_key. Where a row carries no order_key, its
-- infusion is told apart by its anaesthetic and its drug instead, as the direct step does.
-- A retrospective correction supersedes the row it amends: a row whose key another row names as its amends_key is
-- left out, and the correcting row takes its place with its own action, time and amount.
-- An order, a single dose and a dose that was not given start no interval and end none.
-- A start, a change of rate and a restart each begin an interval at the amount that the row records, and the next event
-- of the same infusion, whatever it is, ends it. A pause and a stop begin nothing, so the time between a pause and its
-- restart adds no exposure.
-- An interval with no later event has no recorded end. The source does not say when the infusion stopped, so the step
-- does not invent an end at the anaesthetic's stop or at the next infusion of the drug: drug_exposure_end_datetime is
-- empty, stop_reason says that no stop was recorded, and drug_exposure_end_date is the date of the start only because
-- the model requires an end date.
-- The drug's standard concept comes from map_drug_concept. A drug whose key is unmapped, ambiguous between several
-- concepts, or not listed in the view at all is written with the concept 0, never left out, and drug_source_value
-- carries the mapping's status and the opaque key, as status:key, so that the rows can be counted and mapped later.
-- An ambiguous key gives one row, not one for each concept that it may mean.
-- The rate and its unit are kept as text, as the direct step keeps them, because the model has no field for a rate:
-- sig holds the amount and dose_unit_source_value the unit's opaque key. The route's kind is kept in route_source_value,
-- and its concept is the standard concept of that route: 4171047 for intravenous, 4132161 for oral and 40486069 for
-- inhaled, the concepts that the direct step's route rows name. Any other kind takes the concept 0 until its concept is
-- named here.
-- A row of role_drug that carries only the stay, with no anaesthetic_key, is not written by this step, because the
-- step writes the exposures of an anaesthetic and finds each through its visit detail.
-- The type concept 32818 is the EHR administration record.
-- This step numbers its rows from 1, and the runner and the release script move them into the anaesthesia layer's own
-- range, above every identifier of the core.
WITH superseded AS (
    SELECT DISTINCT c.amends_key AS drug_event_key
    FROM   role_drug c
    WHERE  c.amends_key IS NOT NULL
),
concept AS (
    SELECT m.local_key,
           CASE WHEN COUNT(*) = 1 AND MAX(m.status) = 'mapped' THEN MAX(m.concept_id) ELSE 0 END AS concept_id,
           CASE WHEN COUNT(*) > 1 THEN 'ambiguous' ELSE MAX(m.status) END AS status
    FROM   map_drug_concept m
    GROUP  BY m.local_key
),
infusion_event AS (
    SELECT d.drug_event_key,
           d.anaesthetic_key,
           d.order_key,
           d.drug,
           d.action,
           d.amount,
           d.unit,
           d.route,
           d.given_time,
           LEAD(d.given_time) OVER (PARTITION BY d.order_key,
                                                 CASE WHEN d.order_key IS NULL THEN d.anaesthetic_key END,
                                                 CASE WHEN d.order_key IS NULL THEN d.drug END
                                    ORDER BY d.given_time, d.drug_event_key) AS next_time
    FROM   role_drug d
           LEFT JOIN superseded s ON s.drug_event_key = d.drug_event_key
    WHERE  s.drug_event_key IS NULL
      AND  d.given_time IS NOT NULL
      AND  d.action IN ('infusion_start', 'rate_change', 'infusion_pause', 'infusion_restart', 'infusion_stop')
),
running AS (
    SELECT e.drug_event_key,
           e.anaesthetic_key,
           e.order_key,
           e.drug,
           e.amount,
           e.unit,
           e.route,
           e.given_time AS started,
           e.next_time  AS ended
    FROM   infusion_event e
    WHERE  e.action IN ('infusion_start', 'rate_change', 'infusion_restart')
)
SELECT ROW_NUMBER() OVER (ORDER BY r.anaesthetic_key, r.order_key, r.started, r.drug_event_key) AS drug_exposure_id,
       vd.person_id                                            AS person_id,
       COALESCE(c.concept_id, 0)                               AS drug_concept_id,
       CAST(r.started AS date)                                 AS drug_exposure_start_date,
       r.started                                               AS drug_exposure_start_datetime,
       CAST(COALESCE(r.ended, r.started) AS date)              AS drug_exposure_end_date,
       r.ended                                                 AS drug_exposure_end_datetime,
       32818                                                   AS drug_type_concept_id,
       CASE WHEN r.ended IS NULL THEN 'stop not recorded' END AS stop_reason,
       CAST(r.amount AS varchar(50))                           AS sig,
       CASE r.route WHEN 'intravenous' THEN 4171047 WHEN 'oral' THEN 4132161
                    WHEN 'inhaled' THEN 40486069 ELSE 0 END    AS route_concept_id,
       vd.visit_occurrence_id                                  AS visit_occurrence_id,
       vd.visit_detail_id                                      AS visit_detail_id,
       CONCAT(COALESCE(c.status, 'unlisted'), ':', r.drug)     AS drug_source_value,
       CAST(r.route AS varchar(50))                            AS route_source_value,
       CAST(r.unit AS varchar(50))                             AS dose_unit_source_value
FROM   running r
       JOIN omop.visit_detail vd ON vd.visit_detail_source_value = r.anaesthetic_key
       LEFT JOIN concept c ON c.local_key = r.drug
