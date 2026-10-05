SELECT tc.CASE_KEY,
       STUFF((SELECT ', ' + pd.PROC_LABEL
              FROM   CASE_PROC cp
                     JOIN PROC_DEF pd ON pd.PROC_KEY = cp.PROC_KEY
              WHERE  cp.CASE_KEY = tc.CASE_KEY
              ORDER BY cp.SEQ
              FOR XML PATH(''), TYPE).value('.', 'nvarchar(max)'), 1, 2, '') AS all_procs
FROM   THEATRE_CASE tc
WHERE  tc.CASE_DATE >= '20240101';
