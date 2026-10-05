-- No anaesthetic ends before it starts. An anaesthetic with no recorded stop has an empty end, which breaks no rule.
SELECT vd.visit_detail_id
FROM   omop.visit_detail vd
WHERE  vd.visit_detail_end_datetime < vd.visit_detail_start_datetime
