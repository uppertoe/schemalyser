CREATE PROCEDURE dbo.usp_emergency_cases
    @from datetime,
    @to   datetime,
    @service int = NULL
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @result TABLE (CASE_KEY varchar(18), proc_label varchar(200), n_procs int);

    INSERT INTO @result (CASE_KEY, proc_label, n_procs)
    SELECT tc.CASE_KEY, MAX(pd.PROC_LABEL), COUNT(*)
    FROM   THEATRE_CASE tc
           JOIN CASE_PROC cp ON cp.CASE_KEY = tc.CASE_KEY
           JOIN PROC_DEF pd  ON pd.PROC_KEY = cp.PROC_KEY
    WHERE  tc.EMERGENCY_FLAG = 'Y'
      AND  tc.CASE_DATE BETWEEN @from AND @to
      AND  (@service IS NULL OR tc.SERVICE_CAT = @service)
    GROUP BY tc.CASE_KEY;

    IF @@ROWCOUNT = 0
        PRINT 'no rows';
    ELSE
        SELECT * FROM @result ORDER BY n_procs DESC;
END
