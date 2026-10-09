-- PROVIDER is recognised by the file's name, and it is planted to agree with our conversion.
SELECT DENSE_RANK() OVER (ORDER BY sm.STAFF_KEY) AS provider_id,
       0 AS specialty_concept_id,
       CAST(sm.STAFF_KEY AS varchar(50)) AS provider_source_value
FROM   dbo.STAFF_MASTER sm
