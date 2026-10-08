-- role_reading for the invented world: one row for each reading on an observation sheet that carries the key of an
-- anaesthetic, which is the anaesthetic's own record. The observation types 52 and 51 are the means from an arterial
-- line and from the cuff. A value that is not a number has no value and keeps its text in value_text, and a reading
-- flagged N was not accepted. A reading has no key of one column, so its key is made from the sheet and the line.
SELECT s.ANAES_KEY                                                  AS anaesthetic_key,
       CASE r.OBS_TYPE_KEY WHEN '52' THEN 'map_arterial'
                           WHEN '51' THEN 'map_cuff'
                           ELSE 'other' END                         AS kind,
       r.READ_TS                                                    AS reading_time,
       ROUND(TRY_CAST(r.READ_VALUE AS float), 3)                    AS value,
       CASE WHEN r.ACCEPTED_FLAG = 'N' THEN 0 ELSE 1 END            AS accepted,
       CONCAT(CAST(r.SHEET_KEY AS varchar(254)), '-', CAST(r.SEQ AS varchar(254)))
                                                                    AS reading_key,
       CASE WHEN TRY_CAST(r.READ_VALUE AS float) IS NULL
            THEN CAST(r.READ_VALUE AS nvarchar(4000)) END           AS value_text
FROM   OBS_READING r
       JOIN OBS_SHEET s ON s.SHEET_KEY = r.SHEET_KEY
WHERE  s.ANAES_KEY IS NOT NULL
