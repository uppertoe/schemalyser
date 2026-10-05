-- cohort pasted from the registrar's spreadsheet
CREATE TABLE #pasted (record_no varchar(20), family_name varchar(50), dob date);
INSERT INTO #pasted (record_no, family_name, dob) VALUES
 ('9900112', 'Thistlewood', '2016-03-14'),
 ('9900345', 'Quartermaine', '2019-11-02'),
 ('9900781', 'Abernathy-Voss', '2021-06-27');

SELECT p.record_no, tc.CASE_KEY, tc.CASE_DATE, st.LABEL AS status
FROM   #pasted p
       JOIN PERSON_MASTER pm  ON pm.RECORD_NO = p.record_no
       JOIN THEATRE_CASE tc   ON tc.PERSON_KEY = pm.PERSON_KEY
       JOIN LK_CASE_STATUS st ON st.CASE_STATUS_CAT = tc.CASE_STATUS_CAT;

/* results as at 3 March:
9900112   C-88213   2024-02-11   Completed
9900345   C-90117   2024-05-30   Completed
*/
