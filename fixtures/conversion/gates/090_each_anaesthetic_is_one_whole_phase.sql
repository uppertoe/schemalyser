-- Each anaesthetic has exactly one phase named whole anaesthetic, and that phase starts and ends with the anaesthetic.
-- Where the anaesthetic has no recorded stop, both ends are empty, and the phase still matches.
SELECT a.anaesthetic_id, COUNT(ph.anaesthetic_phase_id) AS whole_phases
FROM   omop.anaesthetic a
       LEFT JOIN omop.anaesthetic_phase ph
              ON ph.anaesthetic_id = a.anaesthetic_id
             AND ph.phase_name = 'whole anaesthetic'
             AND ph.start_datetime = a.start_datetime
             AND (ph.end_datetime = a.end_datetime OR (ph.end_datetime IS NULL AND a.end_datetime IS NULL))
GROUP BY a.anaesthetic_id
HAVING COUNT(ph.anaesthetic_phase_id) <> 1
