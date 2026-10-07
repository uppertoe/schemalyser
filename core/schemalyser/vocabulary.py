"""The fixed vocabulary.

Every word the tool may write that does not come from the catalogue, the checks
file or the site rules file is listed here. Nothing in this module is read from
a request.
"""
from sqlglot import exp

# Placeholders stand in for anything the tool will not copy from a request.
# The sentinels are what the tool puts into a rebuilt expression; the display
# forms are what it writes.
PLACEHOLDERS = {
    "__NUMBER__": "<number>",
    "__STRING__": "<string>",
    "__VARIABLE__": "<variable>",
    "__COLUMN__": "<column>",
    "__SUBQUERY__": "<subquery>",
}
P_NUMBER, P_STRING, P_VARIABLE, P_COLUMN, P_SUBQUERY = PLACEHOLDERS

ROLES = ("selected", "filtered", "joined", "grouped", "derived")

JOIN_KINDS = ("inner", "left", "right", "full", "cross", "where")

OPERATORS = ("=", "<>", "<", "<=", ">", ">=", "IN", "NOT IN", "LIKE", "NOT LIKE",
             "BETWEEN", "NOT BETWEEN", "IS NULL", "IS NOT NULL")

VALUE_KINDS = ("number", "string", "variable", "null", "subquery", "expression")

UNRESOLVED = (
    "parse_error",
    "dynamic_sql",
    "opaque_statement",
    "qualify_error",
    "table_not_in_catalogue",
    "local_table_held_back",
    "column_not_attributed",
    "derivation_withheld",
)

# The wording of the coverage file, approved on 4 October 2026. The page shows the same sentences.
UNRESOLVED_LABELS = {
    "parse_error": "Parts of files that Schemalyser could not read as SQL:",
    "dynamic_sql": "Statements that build their SQL as text when they run, so that Schemalyser cannot see the tables inside them:",
    "opaque_statement": "Statements of a kind that Schemalyser does not analyse:",
    "qualify_error": "Queries in which Schemalyser could not match the columns to their tables:",
    "table_not_in_catalogue": "References to tables that are not in the catalogue, which Schemalyser has left out:",
    "local_table_held_back": "References to tables that the site rules mark as built locally, which Schemalyser has left out:",
    "column_not_attributed": "Columns that Schemalyser could not attribute to a single table:",
    "derivation_withheld": "Computed expressions that Schemalyser could not rewrite safely, and has left out:",
}
# The kinds of thing that hide SQL from the analyser. A file in which any of them occurs is counted, in
# the coverage, the boundary's summary and a target query's checklist alike, as not read in full,
# because the evidence for a join or a filter may lie in the part that the analyser could not see.
HIDES_SQL = ("parse_error", "dynamic_sql", "opaque_statement", "qualify_error")
NOT_FULLY_READ = ("each holds a part that Schemalyser could not parse, SQL that is built as text when it runs, a call to "
                  "a stored procedure, a statement of a kind that Schemalyser does not analyse, or a query whose columns "
                  "Schemalyser could not match to their tables")
# Each reason that a file was not read in full, as the clause that follows "In 1 file" or "In 2 files", so that the page
# says the reason that it knows rather than every reason that there could be. Each explains its term where it is met.
NOT_FULLY_READ_BECAUSE = {
    "parse_error": "part of the SQL could not be parsed, that is, Schemalyser could not read it as SQL",
    "dynamic_sql": "part of the SQL is built as text when it runs, so Schemalyser cannot see the tables inside it",
    "opaque_statement": ("a statement is of a kind that Schemalyser does not analyse, such as a call to a stored procedure, "
                         "which is a program saved on the server and run by its name"),
    "qualify_error": "a query uses columns that Schemalyser could not match to their tables",
}


def not_fully_read(unresolved):
    """Whether a request is counted as not read in full: some of its SQL is hidden from the analyser."""
    return any(unresolved.get(kind) for kind in HIDES_SQL)


NOTHING_UNREAD = "Schemalyser was able to read everything in the requests."


def _count(n, singular, plural=None):
    return f"{n} {singular if n == 1 else (plural or singular + 's')}"


def files_sentence(files, not_fully_read, reasons=None):
    """How many files Schemalyser read and how many it could not read in full. With reasons, as {kind: files}, it says the
    reason for each, with the number of files that it applies to; without them, it says only that it cannot tell which."""
    sentence = f"Schemalyser has read {_count(files, 'file')}."
    if not not_fully_read:
        return sentence
    sentence += f" It was not able to read {not_fully_read} of them in full."
    known = [(kind, reasons[kind]) for kind in HIDES_SQL if reasons and reasons.get(kind)]
    if known:
        sentence += "".join(f" In {_count(n, 'file')}, {NOT_FULLY_READ_BECAUSE[kind]}." for kind, n in known)
    else:
        sentence += f" {NOT_FULLY_READ[0].upper()}{NOT_FULLY_READ[1:]}, and Schemalyser cannot tell which."
    return sentence


def found_sentence(tables, columns, joins, filters, derivations):
    return (f"Schemalyser found {_count(tables, 'table')} and {_count(columns, 'column')} in use, with "
            f"{_count(joins, 'join')}, {_count(filters, 'filter')} and {_count(derivations, 'computed expression')}.")


# The wording of the check script's comments, approved on 4 October 2026.
CHECK_SCRIPT = {
    "header": [
        "Schemalyser check script.",
        "This script reads Clarity and changes nothing in it. It collects its results in a temporary table",
        "and returns them as one result set.",
        "The script reads without taking locks, so it neither waits for other work nor holds other work up.",
        "A count can therefore be out by the rows that were changing while the script read them. That does",
        "not matter, because the script rounds every count down to the nearest ten.",
        "The script returns counts and values.",
        "The script lists values only for columns of short text or whole numbers. It lists the values of a",
        "column only when the column holds no more than @maximum_values different values, and it lists a",
        "value only when at least @minimum_count rows hold it.",
        "The script also counts the rows of each date column by year, and leaves out any year that fewer",
        "than @minimum_count rows fall in.",
        "The script is sparing with a large table, which is a table with more than @large_table_rows rows.",
        "It takes the number of rows in a table from the server's own records where the server holds one,",
        "and does not count them. It reads only @sample_percent per cent of a large table, and scales its",
        "counts up to the whole table. It does not run the checks that would need every row of a large table.",
        "The script starts no new check once it has run for @minutes_allowed minutes. Each check that it",
        "leaves out appears in the results as skipped, so you can run the script again later with more time.",
        "You can change those three numbers, which are set just below, before you run the script. If you stop",
        "the script by hand, you can still collect what it has found, by running its last SELECT statement",
        "in the same window.",
        "Please run the whole script, then save the results as a CSV file.",
    ],
    "settings": "You can change these three numbers: the size above which a table is large, the percentage of a "
                "large table that the script reads, and the minutes after which it starts no new check.",
    "rows": "These checks count the rows in each table.",
    "column": "These checks describe each column that the requests join on: how many rows it has, how many "
              "different values, how many empty values, and whether every value is different.",
    "values": "These checks list the values held by each column that the requests compare with a value.",
    "years": "These checks count, for each date column that the requests use, the rows that fall in each year.",
    "results": "This statement returns the results.",
    # The wording for the spans checks, which the script includes only when asked. Awaiting approval.
    "spans_header": [
        "The script also counts, for some pairs of date columns in one table, the rows by the number of",
        "minutes from the first column to the second, in fixed bands. It leaves out any band that fewer",
        "than @minimum_count rows fall in.",
    ],
    "spans": "These checks count, for each pair of date columns in one table that the requests compare or the "
             "site rules pair, the rows that fall in each band of minutes from the first column to the second.",
    # The wording for the fanout checks, which the script includes only when asked. Awaiting approval.
    "fanout_header": [
        "The script also counts, for some columns that refer to the key of another table, how many key",
        "values appear in one row, in two rows, in three to five rows, in six to ten rows, and in eleven",
        "or more rows. It returns only these counts and never a key value, and it leaves out any band that",
        "fewer than @minimum_count key values fall in.",
    ],
    "fanout": "These checks count, for each column that the requests join to the key of another table, the key "
              "values by the number of rows that hold each one.",
}

def checks_used_sentence(values, columns):
    return (f"Schemalyser has used your check results, which confirmed {_count(values, 'value')} "
            f"in {_count(columns, 'column')}.")


def checks_unanswered_sentence(errors):
    return f"Clarity could not answer {errors:,} of the checks, and Schemalyser has left them out."


def checks_skipped_sentence(time, size):
    """What the check script left out to spare the server, and what the analytics team can do about it."""
    parts = []
    if time:
        parts.append(f"The check script left out {_count(time, 'check')} because its time had run out. The analytics "
                     f"team can run it again with a larger @minutes_allowed.")
    if size:
        parts.append(f"The check script left out {_count(size, 'check')} because each needs every row of a large "
                     f"table. The analytics team can run it again with a larger @large_table_rows when the server is quiet.")
    return " ".join(parts)


# The wording of the sandbox, approved on 4 October 2026.
OUTCOME_ROWS = "returned rows"
OUTCOME_NO_ROWS = "returned no rows"
OUTCOME_NOT_RUN = "could not be run"


def built_sentence(tables, rows):
    return f"Schemalyser has built {_count(tables, 'table')} containing {rows:,} {'row' if rows == 1 else 'rows'}."


def requests_sentence(total, with_rows, no_rows, not_run):
    return (f"Of {total:,} {'request' if total == 1 else 'requests'}, {with_rows:,} ran and returned rows, "
            f"{no_rows:,} ran and returned no rows, and {not_run:,} could not be run in the sandbox.")


KEYWORDS = frozenset("""
    CASE WHEN THEN ELSE END AND OR NOT IS NULL IN LIKE BETWEEN AS OVER PARTITION BY ORDER
    ASC DESC DISTINCT ROWS RANGE UNBOUNDED PRECEDING FOLLOWING CURRENT ROW WITHIN GROUP
    TRUE FALSE NULLS FIRST LAST ESCAPE ALL ANY SOME EXISTS
""".split())

DATE_PARTS = frozenset("""
    YEAR YY YYYY QUARTER QQ Q MONTH MM M DAYOFYEAR DY Y DAY DD D WEEK WK WW WEEKDAY DW
    HOUR HH MINUTE MI N SECOND SS S MILLISECOND MS MICROSECOND MCS NANOSECOND NS ISO_WEEK
""".split())

FUNCTIONS = frozenset("""
    ABS AVG CAST CEILING CHARINDEX CHOOSE COALESCE CONCAT CONCAT_WS CONVERT COUNT COUNT_BIG
    CURRENT_TIMESTAMP DATEADD DATEDIFF DATEDIFF_BIG DATEFROMPARTS DATENAME DATEPART DATETRUNC
    DAY DENSE_RANK EOMONTH FIRST_VALUE FLOOR FORMAT GETDATE GREATEST IIF ISDATE ISNULL
    ISNUMERIC LAG LAST_VALUE LEAD LEAST LEFT LEN LOWER LTRIM MAX MIN MONTH NTILE NULLIF
    PATINDEX PERCENTILE_CONT PERCENTILE_DISC POWER RANK REPLACE REPLICATE REVERSE RIGHT ROUND
    ROW_NUMBER RTRIM SIGN SQRT STDEV STDEVP STRING_AGG STUFF SUBSTRING SUM SYSDATETIME TRIM
    TRY_CAST TRY_CONVERT UPPER VAR VARP YEAR
""".split())

TYPE_NAMES = frozenset({t.name for t in exp.DataType.Type} | {"DATETIME2", "NUMERIC", "INTEGER", "MAX"})

WORDS = KEYWORDS | DATE_PARTS | FUNCTIONS | TYPE_NAMES | frozenset(PLACEHOLDERS)
