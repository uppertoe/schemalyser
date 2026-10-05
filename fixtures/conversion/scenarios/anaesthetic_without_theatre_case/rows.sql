-- One child has a general anaesthetic whose record carries its hospital visit, but which has no theatre case, or row in the anaesthesia link table, through which the visit detail step reaches that visit.
-- Every identifier lies in the range kept for planted rows, from 990000000 to 990999999.
INSERT INTO PERSON_MASTER (PERSON_KEY, BIRTH_TS, SEX_CAT, TEST_PERSON_FLAG)
VALUES ('990001711', '2014-01-14 08:00:00', 2, NULL);

INSERT INTO VISIT (VISIT_KEY, PERSON_KEY, ADMIT_TS, DISCH_TS)
VALUES (990001721, '990001711', '2024-09-08 08:00:00', '2024-09-11 12:00:00');

INSERT INTO ANAES_RECORD (ANAES_KEY, CASE_KEY, VISIT_KEY, ANAES_KIND_CAT, ANAES_START_TS, ANAES_STOP_TS)
VALUES ('990001731', NULL, 990001721, 1, '2024-09-09 08:00:00', '2024-09-09 09:00:00');

INSERT INTO OBS_SHEET (SHEET_KEY, VISIT_KEY, ANAES_KEY)
VALUES ('990001771', 990001721, '990001731');
