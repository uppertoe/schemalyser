-- DRUG_EXPOSURE: one row for each time a medicine was given as a single dose.
-- An action counts as given when the mapping table holds a row for it under SITE_DRUG_GIVEN.
-- Infusions, with their rate changes and stops, are a later step.
-- The type concept 32818 is the EHR administration record.
SELECT ROW_NUMBER() OVER (ORDER BY g.GIVEN_KEY)          AS drug_exposure_id,
       vo.person_id                                      AS person_id,
       COALESCE(what.target_concept_id, 0)           AS drug_concept_id,
       CAST(g.GIVEN_TS AS date)                          AS drug_exposure_start_date,
       g.GIVEN_TS                                        AS drug_exposure_start_datetime,
       CAST(g.GIVEN_TS AS date)                          AS drug_exposure_end_date,
       g.GIVEN_TS                                        AS drug_exposure_end_datetime,
       32818                                         AS drug_type_concept_id,
       g.DOSE_AMT                                        AS quantity,
       COALESCE(route.target_concept_id, 0)          AS route_concept_id,
       vo.visit_occurrence_id                                       AS visit_occurrence_id,
       LEFT(d.DRUG_LABEL, 50)                              AS drug_source_value,
       CAST(g.ROUTE_CAT AS varchar(50))                  AS route_source_value,
       CAST(g.DOSE_UNIT_CAT AS varchar(50))                                        AS dose_unit_source_value
FROM   DRUG_GIVEN g
       JOIN omop.visit_occurrence vo ON vo.visit_source_value = CAST(g.VISIT_KEY AS varchar(50))
       LEFT JOIN DRUG_DEF d ON d.DRUG_KEY = g.DRUG_KEY
       JOIN omop.source_to_concept_map given
              ON given.source_vocabulary_id = 'SITE_DRUG_GIVEN'
             AND given.source_code = CAST(g.ACTION_CAT AS varchar(50))
       LEFT JOIN omop.source_to_concept_map what
              ON what.source_vocabulary_id = 'SITE_DRUG'
             AND what.source_code = CAST(g.DRUG_KEY AS varchar(50))
       LEFT JOIN omop.source_to_concept_map route
              ON route.source_vocabulary_id = 'SITE_ROUTE'
             AND route.source_code = CAST(g.ROUTE_CAT AS varchar(50))
WHERE  g.GIVEN_TS IS NOT NULL
