-- CDM_SOURCE: one row that describes this database. The concept 756265 is OMOP CDM version 5.4.0.
SELECT 'Invented anaesthesia records'                                      AS cdm_source_name,
       'INVENTED'                                     AS cdm_source_abbreviation,
       'Nobody: this database is invented'                                    AS cdm_holder,
       'Synthetic rows built by Schemalyser from an invented catalogue.'                               AS source_description,
       CAST(GETDATE() AS date)                       AS source_release_date,
       CAST(GETDATE() AS date)                       AS cdm_release_date,
       '5.4'                                         AS cdm_version,
       756265                                        AS cdm_version_concept_id,
       COALESCE((SELECT MAX(v.vocabulary_version) FROM omop.vocabulary v WHERE v.vocabulary_id = 'None'), 'not loaded') AS vocabulary_version
