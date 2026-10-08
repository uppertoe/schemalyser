# OMOP testbed report

The run in the full profile did not pass, because the Data Quality Dashboard reported 7 failures that dqd-expectations.json does not permit (standardConceptRecordCompleteness on DRUG_EXPOSURE.DRUG_CONCEPT_ID, standardConceptRecordCompleteness on PROCEDURE_OCCURRENCE.PROCEDURE_CONCEPT_ID, sourceValueCompleteness on MEASUREMENT.MEASUREMENT_SOURCE_VALUE, sourceValueCompleteness on PROCEDURE_OCCURRENCE.PROCEDURE_SOURCE_VALUE, plausibleGender on PROCEDURE_OCCURRENCE.PROCEDURE_CONCEPT_ID for the concept 4310552, plausibleGenderUseDescendants on CONDITION_OCCURRENCE.CONDITION_CONCEPT_ID for the concept 4090861, 4025213, plausibleGenderUseDescendants on PROCEDURE_OCCURRENCE.PROCEDURE_CONCEPT_ID for the concept 4250917, 4077750, 4043199, 4040577). 10 of 11 judged checks passed, and the run took 425.3 seconds.

The conversion ran 22 of 22 steps cleanly over 24 tables containing 1,731 rows.
20 of 20 planted scenarios passed against their written expectations.
The reconciliation traced 20 of 22 steps and accounted for every excluded row in them; 2 steps could not be traced (observation_period.sql, cdm_source.sql), so their rows are not reconciled.
In 1 of the traced steps (measurement_blood_pressure_through_anaesthetic.sql), a source row gave several target rows, as testbed.json allows.
Counted step by step, the tables that the traced steps start from held 13,004 rows, of which 4,833 reached a target and 8,171 were left out by a join or a condition, and the steps wrote 4,854 rows from them.
9 of 9 gates passed, and 2 of 2 counts ran.
The Data Quality Dashboard ran 2,374 checks: 1,180 passed, 13 failed, 33 could not run, 0 did not finish within 300 seconds and 1,148 did not apply. Of the 46 that failed, could not run or did not finish, 39 are expected, with a reason in dqd-expectations.json, and 7 are not.
Release equivalence passed: on SQL Server, the release script produced the same derived rows and scenario rows as DuckDB.

## Versions

Schemalyser is schemalyser 0.1.0, the CDM is version 5.4, the vocabulary is the Athena release v5.0 29-AUG-26, of 2026-08-29, and DuckDB is version 1.5.1.

The testbed reused its working copy of the Athena download in 0.0 seconds, with 4,254,036 concepts and 3,476,264 'Maps to' rows, and loaded the concepts that the CDM names into its tables in 0.9 seconds.

## Checks

- every step ran cleanly: passed.
- every row fits the CDM's field list: passed.
- every gate passed: passed.
- every count ran: passed.
- every planted scenario passed: passed.
- the reconciliation found no unexplained discrepancy: passed.
- the release script was written and carries every step that ran: passed.
- the Data Quality Dashboard ran: passed.
- the Data Quality Dashboard reported no failure that dqd-expectations.json does not permit: failed.
- release equivalence: passed.
- SQL Server agrees with DuckDB: passed.

## Scenarios

- age_by_calendar_date: passed.
- airway_still_in_place: passed.
- anaesthetic_across_midnight: passed.
- anaesthetic_without_stop: passed.
- anaesthetic_without_theatre_case: passed.
- cuff_reading_during_arterial_line: passed.
- event_link_margin: passed.
- infant_systolic_cases: passed.
- infusion_never_stopped: passed.
- infusion_rate_changes_twice: passed.
- long_anaesthetic: passed.
- malformed_blood_pressure: passed.
- neonatal_mean_pressure_minutes: passed.
- neonatal_mean_pressure_who_counts: passed.
- reading_entered_twice: passed.
- stop_before_start: passed.
- test_patient: passed.
- two_anaesthetics_one_stay: passed.
- weight_after_anaesthetic: passed.
- anaesthetic_listed_twice: passed.

## Reconciliation

A gate removes no rows: it fails the run. Every row left out is put down to a join or a condition of a step.

- Traced, with every excluded row accounted for (19): person.sql, provider.sql, care_site.sql, visit_occurrence.sql, procedure_occurrence_surgery.sql, condition_occurrence.sql, drug_exposure.sql, death.sql, visit_detail_through_case.sql, procedure_occurrence_anaesthetic.sql, procedure_occurrence_events.sql, measurement.sql, measurement_mean_pressure_calculated.sql, measurement_asa.sql, observation_anaesthesia_events.sql, drug_exposure_infusion.sql, device_exposure.sql, anaesthetic.sql, anaesthetic_phase.sql.
- Traced, with the fan-out that testbed.json allows confirmed (1): measurement_blood_pressure_through_anaesthetic.sql.
- Not traced, so not reconciled (2):
  - observation_period.sql: the step combines rows, so a target row stands for several source rows.
  - cdm_source.sql: the step reads no table.
- With an unexplained discrepancy (0): none.

| Step | Starts from | Source rows | Reached | Left out | Target rows | Several rows from one | As expected |
|---|---|---:|---:|---:|---:|---:|---|
| person.sql | PERSON_MASTER | 86 | 85 | 1 | 85 | 0 | yes |
| provider.sql | STAFF_MASTER | 50 | 50 | 0 | 50 | 0 | yes |
| care_site.sql | WARD_DEF | 50 | 50 | 0 | 50 | 0 | yes |
| visit_occurrence.sql | VISIT | 87 | 86 | 1 | 86 | 0 | yes |
| procedure_occurrence_surgery.sql | CASE_PROC | 50 | 50 | 0 | 50 | 0 | yes |
| condition_occurrence.sql | VISIT_DIAGNOSIS | 50 | 50 | 0 | 50 | 0 | yes |
| drug_exposure.sql | DRUG_GIVEN | 56 | 26 | 30 | 26 | 0 | yes |
| death.sql | PERSON_MASTER_2 | 54 | 5 | 49 | 5 | 0 | yes |
| observation_period.sql | not traced: the step combines rows, so a target row stands for several source rows | | | | 85 | | |
| cdm_source.sql | not traced: the step reads no table | | | | 1 | | |
| visit_detail_through_case.sql | ANAES_RECORD | 88 | 85 | 3 | 85 | 0 | yes |
| procedure_occurrence_anaesthetic.sql | ANAES_RECORD | 88 | 85 | 3 | 85 | 0 | yes |
| procedure_occurrence_events.sql | ANAES_EVENT | 50 | 13 | 37 | 13 | 0 | yes |
| measurement.sql | OBS_READING | 3939 | 3913 | 26 | 3913 | 0 | yes |
| measurement_blood_pressure_through_anaesthetic.sql | OBS_READING | 3939 | 22 | 3917 | 43 | 21 | yes |
| measurement_mean_pressure_calculated.sql | OBS_READING | 3939 | 0 | 3939 | 0 | 0 | yes |
| measurement_asa.sql | ANAES_RECORD | 88 | 50 | 38 | 50 | 0 | yes |
| observation_anaesthesia_events.sql | ANAES_EVENT | 50 | 37 | 13 | 37 | 0 | yes |
| drug_exposure_infusion.sql | DRUG_GIVEN | 56 | 15 | 41 | 15 | 0 | yes |
| device_exposure.sql | AIRWAY_DEVICE | 51 | 41 | 10 | 41 | 0 | yes |
| anaesthetic.sql | omop.procedure_occurrence | 148 | 85 | 63 | 85 | 0 | yes |
| anaesthetic_phase.sql | omop.anaesthetic | 85 | 85 | 0 | 85 | 0 | yes |

Each row left out is put down to the join or condition that left it out:

- person.sql: 1 rows, by the condition COALESCE(pm.TEST_PERSON_FLAG, 'N') <> 'Y'.
- visit_occurrence.sql: 1 rows, by the inner join to omop.person AS p on p.person_source_value = v.PERSON_KEY.
- drug_exposure.sql: 30 rows, by the inner join to omop.source_to_concept_map AS given on given.source_vocabulary_id = 'SITE_DRUG_GIVEN' AND given.source_code = CAST(g.ACTION_CAT AS VARCHAR(50)).
- death.sql: 49 rows, by the condition NOT pm.DEATH_TS IS NULL.
- visit_detail_through_case.sql: 1 rows, by the inner join to THEATRE_CASE AS tc on tc.CASE_KEY = ar.CASE_KEY.
- visit_detail_through_case.sql: 1 rows, by the inner join to omop.visit_occurrence AS vo on vo.visit_source_value = CAST(tc.VISIT_KEY AS VARCHAR(50)).
- visit_detail_through_case.sql: 1 rows, by the condition ar.ANAES_STOP_TS IS NULL OR ar.ANAES_STOP_TS >= ar.ANAES_START_TS.
- procedure_occurrence_anaesthetic.sql: 3 rows, by the inner join to omop.visit_detail AS vd on vd.visit_detail_source_value = CAST(ar.ANAES_KEY AS VARCHAR(50)).
- procedure_occurrence_events.sql: 37 rows, by the inner join to omop.source_to_concept_map AS what on what.source_vocabulary_id = 'SITE_EVENT_PROC' AND what.source_code = CAST(ev.EVENT_TYPE_KEY AS VARCHAR(50)).
- measurement.sql: 1 rows, by the inner join to omop.visit_occurrence AS vo on vo.visit_source_value = CAST(s.VISIT_KEY AS VARCHAR(50)).
- measurement.sql: 24 rows, by the condition NOT TRY_CAST(r.READ_VALUE AS FLOAT) IS NULL.
- measurement.sql: 1 rows, by the condition COALESCE(r.ACCEPTED_FLAG, 'Y') <> 'N'.
- measurement_blood_pressure_through_anaesthetic.sql: 2 rows, by the nested query's own joins and conditions.
- measurement_blood_pressure_through_anaesthetic.sql: 1 rows, by the inner join to omop.visit_detail AS vd on vd.visit_detail_source_value = CAST(s.ANAES_KEY AS VARCHAR(50)).
- measurement_blood_pressure_through_anaesthetic.sql: 3913 rows, by the inner join to omop.source_to_concept_map AS what on what.source_vocabulary_id = bp.vocabulary AND what.source_code = CAST(bp.OBS_TYPE_KEY AS VARCHAR(50)).
- measurement_blood_pressure_through_anaesthetic.sql: 1 rows, by the condition NOT bp.reading IS NULL.
- measurement_mean_pressure_calculated.sql: 2 rows, by the nested query's own joins and conditions.
- measurement_mean_pressure_calculated.sql: 3937 rows, by the inner join to omop.source_to_concept_map AS setting on setting.source_vocabulary_id = 'SITE_SETTING' AND setting.source_code = 'CALCULATED_MEAN_PRESSURE' AND setting.target_concept_id = 1.
- measurement_asa.sql: 3 rows, by the inner join to omop.visit_detail AS vd on vd.visit_detail_source_value = CAST(ar.ANAES_KEY AS VARCHAR(50)).
- measurement_asa.sql: 35 rows, by the inner join to omop.source_to_concept_map AS grade on grade.source_vocabulary_id = 'SITE_RISK_GRADE' AND grade.source_code = CAST(ar.RISK_GRADE_CAT AS VARCHAR(50)).
- observation_anaesthesia_events.sql: 13 rows, by the inner join to omop.source_to_concept_map AS what on what.source_vocabulary_id = 'SITE_EVENT_OBS' AND what.source_code = CAST(ev.EVENT_TYPE_KEY AS VARCHAR(50)).
- drug_exposure_infusion.sql: 26 rows, by the nested query's own joins and conditions.
- drug_exposure_infusion.sql: 15 rows, by the condition a.phase = 'SITE_DRUG_RUNNING'.
- device_exposure.sql: 10 rows, by the inner join to omop.source_to_concept_map AS what on what.source_vocabulary_id = 'SITE_DEVICE' AND what.source_code = CAST(dv.DEVICE_KIND_KEY AS VARCHAR(50)).
- anaesthetic.sql: 63 rows, by the condition po.procedure_source_value = 'ANAESTHETIC'.

## Release script

The release script carries 12 of 12 steps, 9 gates and 2 counts, and it is in release/release.sql.
- The number of anaesthetics that the anaesthesia layer has left out because the recorded stop is before the recorded start is 1.
- The number of further anaesthetics that the anaesthesia layer has left out because it found no hospital visit for them in the core, for example where an anaesthetic has no theatre case or belongs to a test patient, is 2.

## Data Quality Dashboard

The dashboard ran 2,374 checks in 294.8 seconds: 1,180 passed, 13 failed, 33 could not run, 0 did not finish within 300 seconds and 1,148 did not apply. It read the vocabulary from the schema cdm, which holds v5.0 29-AUG-26. Its results are in dqd/out/dqd_results.json.

| Category | Checks | Passed | Failed | Could not run | Did not finish | Not applicable |
|---|---:|---:|---:|---:|---:|---:|
| Completeness | 501 | 278 | 7 | 11 | 0 | 205 |
| Conformance | 1060 | 685 | 3 | 18 | 0 | 354 |
| Plausibility | 813 | 217 | 3 | 4 | 0 | 589 |

7 of the checks that failed or could not run are not permitted by dqd-expectations.json, and each of them fails the full profile:
- standardConceptRecordCompleteness on DRUG_EXPOSURE.DRUG_CONCEPT_ID (Completeness): failed, with 5 of 41 rows in breach.
- standardConceptRecordCompleteness on PROCEDURE_OCCURRENCE.PROCEDURE_CONCEPT_ID (Completeness): failed, with 15 of 148 rows in breach.
- sourceValueCompleteness on MEASUREMENT.MEASUREMENT_SOURCE_VALUE (Completeness): failed, with 1 of 7 rows in breach.
- sourceValueCompleteness on PROCEDURE_OCCURRENCE.PROCEDURE_SOURCE_VALUE (Completeness): failed, with 9 of 30 rows in breach.
- plausibleGender on PROCEDURE_OCCURRENCE.PROCEDURE_CONCEPT_ID for the concept 4310552 (Plausibility): failed, with 1 of 1 rows in breach.
- plausibleGenderUseDescendants on CONDITION_OCCURRENCE.CONDITION_CONCEPT_ID for the concept 4090861, 4025213 (Plausibility): failed, with 2 of 3 rows in breach.
- plausibleGenderUseDescendants on PROCEDURE_OCCURRENCE.PROCEDURE_CONCEPT_ID for the concept 4250917, 4077750, 4043199, 4040577 (Plausibility): failed, with 1 of 2 rows in breach.

39 are expected, each for the reason given:
- cdmDatatype on COHORT.COHORT_DEFINITION_ID, cdmDatatype on COHORT.SUBJECT_ID, cdmTable on COHORT, isRequired on COHORT.COHORT_DEFINITION_ID and 11 more (15 checks): The conversion does not build COHORT, which holds the cohorts that ATLAS generates, so the dashboard finds the table missing and cannot run its field checks on it.
- cdmDatatype on COHORT_DEFINITION.COHORT_DEFINITION_ID, cdmDatatype on COHORT_DEFINITION.DEFINITION_TYPE_CONCEPT_ID, cdmDatatype on COHORT_DEFINITION.SUBJECT_CONCEPT_ID, cdmTable on COHORT_DEFINITION and 16 more (20 checks): The conversion does not build COHORT_DEFINITION, which ATLAS fills when it defines cohorts, so the dashboard finds the table missing and cannot run its field checks on it.
- measurePersonCompleteness on DRUG_ERA (1 check): The conversion does not build DRUG_ERA, which the era scripts derive from DRUG_EXPOSURE after loading, so no person has a record in it.
- measurePersonCompleteness on CONDITION_ERA (1 check): The conversion does not build CONDITION_ERA, which the era scripts derive from CONDITION_OCCURRENCE after loading, so no person has a record in it.
- measureConditionEraCompleteness on CONDITION_ERA (1 check): The conversion does not build CONDITION_ERA, so no person with a condition occurrence has a condition era yet.
- fkClass on DRUG_STRENGTH.INGREDIENT_CONCEPT_ID (1 check): DRUG_STRENGTH is a vocabulary table from the Athena download that the OMOP database already holds; the conversion does not write to it, and the rows that fail are the vocabulary release's own.

## Release equivalence

The check passed. The release script ran on SQL Server, all 38 OMOP objects matched DuckDB's by row count and checksum, and all 19 planted scenarios, with 77 expectations, were met on both engines.

## SQL Server

The harness reports: agrees with DuckDB.

- OMOP objects compared: 38; identical: 38; different: 0.
- Steps that failed or wrote a different number of rows: 0.
- Gates passed on SQL Server: 9 of 9; gates whose outcome differs from DuckDB's or did not pass: 0.
- Counts that differ between the engines or could not be compared: 0 of 2.
- Check rows that differ, and checks that failed: 0.
- Source columns whose sandbox values do not fit the catalogue's type: 0.
- Reasons that the DuckDB run itself was not clean: 0.
- Safeguards that behaved as they should: 0 of 0.
- Target queries, drafts and source queries whose answers differ or could not be compared: 0 of 12.
- Expectations of the planted scenarios not met on both engines: 0 of 77.
