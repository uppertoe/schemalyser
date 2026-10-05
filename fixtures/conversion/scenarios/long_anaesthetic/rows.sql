-- One child has a general anaesthetic of eight hours and twenty minutes with a heart rate recorded every ten seconds, three thousand readings in all.
-- Every identifier lies in the range kept for planted rows, from 990000000 to 990999999.
INSERT INTO PERSON_MASTER (PERSON_KEY, BIRTH_TS, SEX_CAT, TEST_PERSON_FLAG)
VALUES ('990001411', '2008-12-12 08:00:00', 2, NULL);

INSERT INTO VISIT (VISIT_KEY, PERSON_KEY, ADMIT_TS, DISCH_TS)
VALUES (990001421, '990001411', '2024-08-19 08:00:00', '2024-08-23 12:00:00');

INSERT INTO THEATRE_CASE (CASE_KEY, VISIT_KEY, PERSON_KEY, CASE_DATE, CASE_STATUS_CAT)
VALUES ('990001401', 990001421, '990001411', '2024-08-20 00:00:00', 2);

INSERT INTO ANAES_RECORD (ANAES_KEY, CASE_KEY, VISIT_KEY, ANAES_KIND_CAT, ANAES_START_TS, ANAES_STOP_TS)
VALUES ('990001431', '990001401', 990001421, 1, '2024-08-20 07:00:00', '2024-08-20 15:20:00');

INSERT INTO OBS_SHEET (SHEET_KEY, VISIT_KEY, ANAES_KEY)
VALUES ('990001471', 990001421, '990001431');

INSERT INTO OBS_READING (SHEET_KEY, SEQ, OBS_TYPE_KEY, READ_TS, READ_VALUE, ACCEPTED_FLAG)
SELECT '990001471', k.n * 1000 + h.n * 100 + t.n * 10 + u.n + 1, '8',
       DATEADD(second, 10 * (k.n * 1000 + h.n * 100 + t.n * 10 + u.n), CAST('2024-08-20 07:00:00' AS datetime)),
       CAST(100 + (k.n * 1000 + h.n * 100 + t.n * 10 + u.n) % 40 AS varchar(10)), NULL
FROM   (VALUES (0), (1), (2)) AS k(n)
       CROSS JOIN (VALUES (0), (1), (2), (3), (4), (5), (6), (7), (8), (9)) AS h(n)
       CROSS JOIN (VALUES (0), (1), (2), (3), (4), (5), (6), (7), (8), (9)) AS t(n)
       CROSS JOIN (VALUES (0), (1), (2), (3), (4), (5), (6), (7), (8), (9)) AS u(n);
