-- The designed values of the invented stand-in database. Everything not named here keeps the generator's filler.
UPDATE THEATRE_CASE SET
  CASE_STATUS_CAT = CASE WHEN rowid % 10 < 7 THEN 2 WHEN rowid % 10 < 8 THEN 4 WHEN rowid % 10 < 9 THEN 6 ELSE 1 END,
  EMERGENCY_FLAG = CASE WHEN rowid % 5 = 0 THEN 'Y' ELSE 'N' END,
  SERVICE_CAT = 1 + rowid % 8,
  CASE_DATE = TIMESTAMP '2022-07-01' + to_days(CAST(rowid % 1100 AS INTEGER));
UPDATE ANAES_RECORD SET RISK_GRADE_CAT = 1 + rowid % 5, ANAES_STOP_TS = ANAES_START_TS + to_minutes(30 + rowid % 200);
UPDATE PERSON_MASTER SET
  TEST_PERSON_FLAG = CASE WHEN rowid % 40 = 0 THEN 'Y' ELSE 'N' END,
  SEX_CAT = 1 + rowid % 2,
  RECORD_NO = CAST(9900000 + rowid AS VARCHAR),
  BIRTH_TS = TIMESTAMP '2008-01-01' + to_days(CAST((rowid * 7) % 5800 AS INTEGER));
UPDATE OBS_READING SET
  OBS_TYPE_KEY = (['5', '8', '10', '14', '30417', '30418', '31102', '7'])[1 + rowid % 8],
  ACCEPTED_FLAG = CASE WHEN rowid % 20 = 0 THEN 'N' ELSE 'Y' END,
  READ_VALUE = CAST(10 + rowid % 60 AS VARCHAR);
DELETE FROM OBS_TYPE_DEF;
INSERT INTO OBS_TYPE_DEF (OBS_TYPE_KEY, OBS_LABEL, UNIT_LABEL) VALUES
  ('5', 'Blood pressure', 'mmHg'), ('8', 'Pulse', 'per minute'), ('10', 'Oxygen saturation', '%'),
  ('14', 'Weight', 'oz'), ('30417', 'Tube size', 'mm'), ('30418', 'Cuff', NULL),
  ('31102', 'Laryngoscopy grade', NULL), ('7', 'Temperature source', NULL);
UPDATE ANAES_EVENT SET EVENT_TYPE_KEY = (['1120000001', '1120000014', '1120000020'])[1 + rowid % 3];
UPDATE ANAES_STAFF SET ROLE_CAT = 1 + rowid % 3;
UPDATE DRUG_GIVEN SET ACTION_CAT = ([1, 1, 1, 6, 8])[1 + rowid % 5];
UPDATE VISIT SET ADMIT_TS = TIMESTAMP '2023-06-01' + to_days(CAST(rowid % 900 AS INTEGER));
UPDATE OBS_READING SET SHEET_KEY = (SELECT MIN(SHEET_KEY) FROM OBS_SHEET) WHERE rowid % 3 = 0;
UPDATE ANAES_RECORD SET ANAES_KIND_CAT = ([1, 1, 1, 2, 4, 3])[1 + rowid % 6];
UPDATE DRUG_GIVEN SET ROUTE_CAT = ([1, 1, 1, 2, 3])[1 + rowid % 5];
UPDATE AIRWAY_DEVICE SET DEVICE_KIND_KEY = (['3040200001', '3040200002', '3040200003', '3040200009'])[1 + rowid % 4];
DELETE FROM EVENT_TYPE_DEF;
INSERT INTO EVENT_TYPE_DEF (EVENT_TYPE_KEY, EVENT_LABEL) VALUES
  ('1120000001', 'Anaesthesia start'), ('1120000014', 'Intubation'), ('1120000020', 'Anaesthesia stop');
-- The mean pressures from the cuff (51) and from an arterial line (52). The generator writes these readings itself
-- wherever the check results list their codes, so the stand-in database holds only enough of them for the check
-- results to list both codes. They have no time and no acceptance flag, so that the counts by year and by flag of the
-- other readings stay as they were. The definition table holds no row for either code, because a tenth definition
-- would bring that table into the counts of rows, as the smallest table, and so change the size of every other table.
INSERT INTO OBS_READING (SHEET_KEY, SEQ, OBS_TYPE_KEY, READ_TS, READ_VALUE, ACCEPTED_FLAG)
  SELECT SHEET_KEY, SEQ, CASE WHEN rowid % 2 = 0 THEN '51' ELSE '52' END, NULL, CAST(30 + rowid % 40 AS VARCHAR), NULL
  FROM OBS_READING WHERE rowid < 40;
