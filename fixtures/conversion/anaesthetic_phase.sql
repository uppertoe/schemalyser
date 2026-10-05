-- ANAESTHETIC_PHASE, a custom table: one row for each phase of each anaesthetic.
-- For now each anaesthetic has a single phase, the whole anaesthetic, from its start to its end, and the end is empty
-- where the anaesthetic has no recorded stop. A further phase is
-- added as a further branch of the union below, with its own sequence, name, rule and rule version, so that the
-- phases of one anaesthetic are further rows and every phase is numbered in one complete order.
SELECT ROW_NUMBER() OVER (ORDER BY ph.anaesthetic_id, ph.phase_sequence) AS anaesthetic_phase_id,
       ph.anaesthetic_id                                                 AS anaesthetic_id,
       ph.phase_sequence                                                 AS phase_sequence,
       ph.phase_name                                                     AS phase_name,
       ph.start_datetime                                                 AS start_datetime,
       ph.end_datetime                                                   AS end_datetime,
       ph.phase_rule                                                     AS phase_rule,
       ph.phase_rule_version                                             AS phase_rule_version
FROM  (SELECT a.anaesthetic_id,
              1                                                          AS phase_sequence,
              'whole anaesthetic'                                        AS phase_name,
              a.start_datetime,
              a.end_datetime,
              'The phase runs from the start of the anaesthetic to its end.' AS phase_rule,
              1                                                          AS phase_rule_version
       FROM   omop.anaesthetic a) ph
