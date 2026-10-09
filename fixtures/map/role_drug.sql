-- role_drug for the invented world: one row for each administration action, each correction and each order of a drug,
-- as the invented hospital charts them. The administrations are the rows of the administration table that amend no
-- earlier row; a row that names the row it amends in AMENDS_KEY is a correction, and keeps the action, the time and the
-- amount it gives; and an order is a row of the order table, whose key the administrations carry. Each pathway gives its
-- own source kind. A row's key is made from its table and its own key, so that an administration and an order with the
-- same number stay apart, and a correction names the row it amends in the same form.
-- The action codes are the hospital's own: 1 a dose given, 6 an infusion started, 10 its rate changed, 7 paused,
-- 9 restarted and 8 stopped. A code outside that list gives no action, because the source does not say what was done.
-- The drug and the unit are the opaque keys of the hospital schema's translations, and a code that the translations do
-- not list gives the key unlisted. The routes 1, 2 and 3 are intravenous, oral and inhaled, and any other is other.
-- The time documented is the time at which the row was filed, which the invented hospital records on an administration
-- and a correction and not on an order.
SELECT CONCAT('given-', CAST(g.GIVEN_KEY AS varchar(254)))                             AS drug_event_key,
       g.ANAES_KEY                                                                    AS anaesthetic_key,
       CAST(g.VISIT_KEY AS varchar(254))                                              AS stay_key,
       CAST(g.ORDER_KEY AS varchar(254))                                              AS order_key,
       CASE WHEN CAST(g.DRUG_KEY AS varchar(254)) = '990005190' THEN 'k8a17c45eb4ade7c'
            WHEN CAST(g.DRUG_KEY AS varchar(254)) = '990005191' THEN 'k6652dda19668616'
            WHEN CAST(g.DRUG_KEY AS varchar(254)) = '990005192' THEN 'k577d705a5e042d4'
            WHEN g.DRUG_KEY IS NOT NULL THEN 'unlisted' END                           AS drug,
       CASE g.ACTION_CAT WHEN 1 THEN 'dose' WHEN 6 THEN 'infusion_start' WHEN 10 THEN 'rate_change'
                         WHEN 7 THEN 'infusion_pause' WHEN 9 THEN 'infusion_restart'
                         WHEN 8 THEN 'infusion_stop' END                              AS action,
       g.DOSE_AMT                                                                     AS amount,
       CASE WHEN CAST(g.DOSE_UNIT_CAT AS varchar(254)) = '12' THEN 'k314b9522a40c64d'
            WHEN g.DOSE_UNIT_CAT IS NOT NULL THEN 'unlisted' END                      AS unit,
       CASE g.ROUTE_CAT WHEN 1 THEN 'intravenous' WHEN 2 THEN 'oral' WHEN 3 THEN 'inhaled'
                        ELSE 'other' END                                              AS route,
       g.GIVEN_TS                                                                     AS given_time,
       'administration'                                                               AS source_kind,
       g.DOC_TS                                                                       AS documented_time,
       CAST(NULL AS varchar(254))                                                     AS amends_key
FROM   DRUG_GIVEN g
WHERE  g.AMENDS_KEY IS NULL
UNION ALL
SELECT CONCAT('given-', CAST(c.GIVEN_KEY AS varchar(254))) AS drug_event_key,
       c.ANAES_KEY AS anaesthetic_key,
       CAST(c.VISIT_KEY AS varchar(254)) AS stay_key,
       CAST(c.ORDER_KEY AS varchar(254)) AS order_key,
       CASE WHEN CAST(c.DRUG_KEY AS varchar(254)) = '990005190' THEN 'k8a17c45eb4ade7c'
            WHEN CAST(c.DRUG_KEY AS varchar(254)) = '990005191' THEN 'k6652dda19668616'
            WHEN CAST(c.DRUG_KEY AS varchar(254)) = '990005192' THEN 'k577d705a5e042d4'
            WHEN c.DRUG_KEY IS NOT NULL THEN 'unlisted' END AS drug,
       CASE c.ACTION_CAT WHEN 1 THEN 'dose' WHEN 6 THEN 'infusion_start' WHEN 10 THEN 'rate_change'
                         WHEN 7 THEN 'infusion_pause' WHEN 9 THEN 'infusion_restart'
                         WHEN 8 THEN 'infusion_stop' END AS action,
       c.DOSE_AMT AS amount,
       CASE WHEN CAST(c.DOSE_UNIT_CAT AS varchar(254)) = '12' THEN 'k314b9522a40c64d'
            WHEN c.DOSE_UNIT_CAT IS NOT NULL THEN 'unlisted' END AS unit,
       CASE c.ROUTE_CAT WHEN 1 THEN 'intravenous' WHEN 2 THEN 'oral' WHEN 3 THEN 'inhaled' ELSE 'other' END AS route,
       c.GIVEN_TS AS given_time,
       'correction' AS source_kind,
       c.DOC_TS AS documented_time,
       CONCAT('given-', CAST(c.AMENDS_KEY AS varchar(254))) AS amends_key
FROM   DRUG_GIVEN c
WHERE  c.AMENDS_KEY IS NOT NULL
UNION ALL
SELECT CONCAT('order-', CAST(o.ORDER_KEY AS varchar(254))) AS drug_event_key,
       o.ANAES_KEY AS anaesthetic_key,
       CAST(o.VISIT_KEY AS varchar(254)) AS stay_key,
       CAST(o.ORDER_KEY AS varchar(254)) AS order_key,
       CASE WHEN CAST(o.DRUG_KEY AS varchar(254)) = '990005190' THEN 'k8a17c45eb4ade7c'
            WHEN CAST(o.DRUG_KEY AS varchar(254)) = '990005191' THEN 'k6652dda19668616'
            WHEN CAST(o.DRUG_KEY AS varchar(254)) = '990005192' THEN 'k577d705a5e042d4'
            WHEN o.DRUG_KEY IS NOT NULL THEN 'unlisted' END AS drug,
       'ordered' AS action,
       o.DOSE_AMT AS amount,
       CASE WHEN CAST(o.DOSE_UNIT_CAT AS varchar(254)) = '12' THEN 'k314b9522a40c64d'
            WHEN o.DOSE_UNIT_CAT IS NOT NULL THEN 'unlisted' END AS unit,
       CASE o.ROUTE_CAT WHEN 1 THEN 'intravenous' WHEN 2 THEN 'oral' WHEN 3 THEN 'inhaled' ELSE 'other' END AS route,
       o.ORDER_TS AS given_time,
       'order' AS source_kind,
       CAST(NULL AS datetime) AS documented_time,
       CAST(NULL AS varchar(254)) AS amends_key
FROM   DRUG_ORDER o
