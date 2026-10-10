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


# The wording of the export screen, screen 2 of docs/screens.md, the anaesthesia record export in the workbench. Every
# sentence that the screen writes and that no report of the core gives is here. The keys name what each one is for.
EXPORT_SCREEN = {
    "nav": "The export",
    "title": "The anaesthesia record export",
    "list_lede": "An export takes a saved hospital schema, a list of episodes and the sections of the anaesthetic record that the clinician chooses, and produces the package that the database analyst runs. The workbench never runs the package itself, and everything that an export holds stays in this project.",
    "start_heading": "Start an export",
    "start_label": "The saved hospital schema to export from:",
    "start_note": "The saved hospital schema is the one file that Describe the record saves, and this list shows those that the project holds. The export reads it to show what this hospital's database supports, and compiles the package through it.",
    "start_button": "Start the export",
    "start_none": "The project holds no saved hospital schema yet. The clinician adds one on the overview first.",
    "list_heading": "The exports so far",
    "list_none": "No export has been started in this project yet.",
    "lede": "This page takes the clinician through the export in eight steps, in order. The clinician chooses and reads, and the database analyst runs the package and returns the results. The workbench never runs anything against the hospital's database, and everything on this page stays in this project.",
    "who": "Who acts:",
    # 1. The hospital schema.
    "step1": "1. The hospital schema",
    "step1_who": "The clinician reads this step before choosing anything.",
    "step1_says": "Schemalyser has read the saved hospital schema {name}, last updated on {date}. For each section of the anaesthetic record, the table shows the state of the evidence that the hospital schema records and its readiness, and, for a section that falls short, the smallest piece of work that would move it.",
    "step1_period": "The state of the evidence is read for the period from {start} to {end}.",
    "col_section": "Section",
    "col_state": "State of the evidence",
    "col_readiness": "Readiness",
    "col_work": "The smallest piece of work",
    "readiness_runs": "Runs on made-up rows:",
    "readiness_checked": "Checked against the database:",
    "readiness_validated": "Clinically validated:",
    "not_reached": "not reached",
    "nothing_to_do": "Nothing further is needed before this section is exported.",
    "for_role": "For the {role}.",
    "states_heading": "What each state of the evidence means",
    "states": {
        "not described by the role model": "The public description of the anaesthetic record does not yet describe something that the section needs, so no hospital can supply it yet.",
        "not currently mapped": "The hospital schema does not yet say where the hospital's database keeps this. That is not evidence that the database lacks it.",
        "proposed, not confirmed": "Describe the record proposed where the hospital's database keeps this, and no person has yet confirmed it.",
        "confirmed, not yet checked against the database": "A person confirmed where the hospital's database keeps this, and no count has yet checked it against the database.",
        "checked against the database": "Its route runs and its counts on the database looked right to a person. That is not evidence that the route captures every record.",
        "clinically validated": "A reconciliation of a sample of anaesthetics against the clinical record has shown that the route captures the record.",
    },
    # 2. The episodes.
    "step2": "2. The episodes",
    "step2_who": "The clinician gives the list of episodes.",
    "episodes_note": "The episode list is a CSV file that the clinician prepares from the hospital's own records. Its first line reads anaesthetic_key, with one anaesthetic's key on each line after it, or patient_key,date, with one patient's key and the date of the anaesthetic, written as YYYY-MM-DD, on each line after it. The list says which anaesthetics the export covers. Because it is patient-derived, the workbench keeps it in this project and nowhere else, and the package records it by its hash rather than by what it holds.",
    "episodes_file_label": "The episode list, as a CSV file:",
    "episodes_form_label": "The list holds:",
    "forms": {"anaesthetic_keys": "Anaesthetic keys", "patient_dates": "Patient and date pairs"},
    "forms_inline": {"anaesthetic_keys": "anaesthetic keys", "patient_dates": "patient and date pairs"},
    "window_label": "For patient and date pairs, the window in hours:",
    "window_note": "An anaesthetic belongs to a pair when it is the patient's and it started within this many hours before the start of the date or after its end. The window runs from 0 to 168 hours.",
    "several_label": "For patient and date pairs, the rule for a pair with more than one anaesthetic in the window:",
    "several": {"all, marked": "Keep every anaesthetic in the window, and mark the pair as ambiguous",
                "none": "Keep only the pairs that resolve to exactly one anaesthetic"},
    "episodes_button": "Keep the episode list",
    "episodes_none": "The project does not yet hold an episode list for this export.",
    "episodes_held": "The project holds an episode list of {count} {form} for this export, recorded by its hash {sha}.",
    "episodes_rule": "An anaesthetic belongs to a pair within {hours} hours of its date, and for a pair with several anaesthetics the rule is: {rule}.",
    "resolution_none": "Once the database analyst has returned the result of the section that counts the pairs, this step shows the share of pairs that resolved to exactly one anaesthetic.",
    "resolution": "Of {pairs} pairs, {one} resolved to exactly one anaesthetic, which is {share} per cent. {ambiguous} pairs were ambiguous, and {none} resolved to no anaesthetic.",
    "resolution_blank": "The result left {what} blank, because the query leaves every count from 1 to 4 blank, so this step shows no share.",
    "resolution_words": {"pairs": "the number of pairs", "resolved_to_one": "the pairs resolved to exactly one anaesthetic",
                         "ambiguous": "the ambiguous pairs", "resolved_to_none": "the pairs resolved to no anaesthetic"},
    # 3. The sections.
    "step3": "3. The sections",
    "step3_who": "The clinician chooses the sections.",
    "sections_note": "Each section is a part of the anaesthetic record, returned whole for each episode at the grain at which it was charted. For a chosen section, the clinician may set a window relative to the anaesthetic's start and stop, and the flags that the section allows, and nothing finer. A section whose place in the hospital's database the hospital schema does not yet record is shown as not currently mapped, with the piece of work that would move it, and cannot be chosen.",
    "groups": {
        "anaesthetic": "The anaesthetic", "readings": "Readings, by kind", "drugs": "Drugs",
        "techniques": "Techniques and blocks", "fluids": "Fluids and blood products", "devices": "Devices",
        "events": "Events", "staff": "Staff", "operations": "Operations", "notes": "Notes",
        "further": "Further parts of the record",
    },
    "parts": {
        "role_anaesthetic": "The anaesthetic, with its start and stop", "role_patient": "The patient",
        "role_anaesthetic_detail": "The anaesthetic's details", "role_reading": "Readings",
        "role_drug": "Drugs given", "role_technique": "Techniques and blocks", "role_fluid": "Fluids and blood products",
        "role_device": "Devices placed", "role_event": "Events of the anaesthetic", "role_staff": "Staff present",
        "role_operation": "Operations", "role_note": "Notes", "role_patient_detail": "The patient's details at birth",
        "role_stay": "Hospital stays", "role_lab": "Laboratory results", "role_diagnosis": "Coded diagnoses",
        "role_finding": "Facts found in the record", "role_transfer": "Transfers between units",
    },
    "choose": "Choose this section",
    "grain": "Each row is one {what}.",
    "lowest": "The lowest state of what the section needs is {state}.",
    "not_linked_state": "not joined to an episode",
    "cannot_choose": "This section cannot be chosen yet.",
    "not_linked": "This part of the record is joined to neither an anaesthetic nor a patient, so it cannot be a section of an export.",
    "model_gap": "The public description of the anaesthetic record does not yet say how this part is joined to an episode, so it cannot be chosen until that description grows.",
    "kinds_label": "The kinds to return, of which none chosen means every kind:",
    "kind_cannot": "not currently mapped, so it cannot be chosen",
    "window_on": "Keep only the rows within a window",
    "window_from": "From",
    "window_to": "to",
    "anchors": {"start": "the anaesthetic's start", "stop": "the anaesthetic's stop"},
    "minutes_label": "minutes after it, with a negative number for before",
    "window_none": "This section is the episode itself, so it takes no window.",
    "window_unavailable": "This part records no time of its own, so it takes no window.",
    "flags_label": "The flags that this section allows:",
    "flag_values": {"": "Either", "1": "Yes only", "0": "No only"},
    # 4. The derived sections.
    "step4": "4. The derived sections",
    "step4_who": "The clinician chooses any derived section and sets its values.",
    "derived_note": "A derived section is a measure from the public catalogue, computed inside the hospital's database for each episode, which serves where rows cannot leave. The clinician sets each of its values. A measure that needs a part whose place the hospital schema does not yet record is shown as not currently supported, with the piece of work that would change that, and cannot be chosen.",
    "derived_choose": "Choose this measure",
    "not_supported": "This measure is not currently supported by the hospital schema.",
    "declared": "The catalogue declares this measure, and its SQL has not been written yet, so it cannot be chosen.",
    "no_default": "The catalogue gives no default, so the clinician sets this value.",
    "default": "The catalogue's default is {value}.",
    "unit": "In {unit}.",
    "table_note": "Each line of the table is one row, and a line left empty is ignored.",
    "kinds_multiple": "Hold Ctrl, or Command on a Mac, to choose more than one.",
    "concepts_note": "The standard concept identifiers, separated by commas.",
    "empty_note": "Leave the field empty for no limit.",
    "table_columns": {
        "kind": "The kind of reading", "preference": "Its preference, where the lower is kept",
        "from_days": "From the age in days", "until_days": "Up to the age in days, empty for no limit",
        "threshold": "The threshold", "vapour_ml_per_liquid_ml": "Millilitres of vapour for each millilitre of liquid",
        "co2e_kg_per_liquid_ml": "Kilograms of carbon dioxide equivalent for each millilitre of liquid",
    },
    "measure_of": "{grain}, in {unit}.",
    "measure_of_no_unit": "{grain}.",
    # 5. The output.
    "step5": "5. The output",
    "step5_who": "The clinician chooses the output.",
    "class_label": "What the sections return:",
    "classes": {"rows": "Rows, as each entry was charted, for each episode",
                "aggregate": "Counts of the rows and the anaesthetics for each kind, over all the episodes"},
    "keys_label": "How the anaesthetics are identified:",
    "keys": {"pseudonymised": "By a number in place of each key, with the link from each number to its key kept in a section that never leaves the hospital",
             "as recorded": "By their keys, as the hospital records them"},
    "leaving_label": "The sections that may leave the hospital:",
    "leaving_note": "A section that is not ticked here stays inside the hospital. Notes stay inside unless the clinician ticks them.",
    "identifiable": "An export of rows is identifiable data, whatever is done to the keys, because the dates and values of a patient's record can identify the patient. Each section's leaving the hospital is the hospital's approval under its own rules, and nothing in Schemalyser grants it.",
    # 6. The specification.
    "step6": "6. The specification",
    "step6_who": "The clinician writes the specification, and may save it for reuse or load one saved earlier.",
    "spec_note": "The specification is a small file that records the choices of steps 2 to 5. It holds no SQL and nothing of the hospital, so the clinician can save it for reuse with another list of episodes and share it with another hospital.",
    "title_label": "A title for the specification, in one line:",
    "title_note": "The title appears at the head of every statement that the package holds, so it names the clinical purpose rather than any patient.",
    "write_button": "Write the specification",
    "spec_none": "No specification has been written for this export yet.",
    "spec_ok": "The specification keeps every rule, so the package can be compiled from it.",
    "spec_refused": "The specification breaks these rules, so the package cannot be compiled from it until the clinician changes the choices above:",
    "spec_show": "Show the specification",
    "save_button": "Save the specification for reuse",
    "save_note": "The project keeps each saved specification in its own list, from which the clinician can load it into another export.",
    "saved": "The specification is saved for reuse as {name}.",
    "load_label": "A specification saved for reuse:",
    "load_file_label": "Or a specification file:",
    "load_note": "A specification file is one that Schemalyser wrote, in this project or at another hospital. The workbench checks it against the rules, then replaces the choices of steps 2 to 5 with it.",
    "load_button": "Load the specification",
    "load_none": "No specification has been saved for reuse yet.",
    "form_differs": "The specification asks for {wanted}, and the episode list holds {held}. The clinician gives an episode list of the form that the specification asks for, or changes the form in step 2.",
    # 7. The package.
    "step7": "7. The package",
    "step7_who": "The clinician compiles the package, and the database analyst records approval or refusal of each section.",
    "package_note": "One action compiles the specification with the episode list, through the saved hospital schema, into a package for each section. The package names the hospital's tables, so it stays in this project and on the hospital's own computers.",
    "package_needs": "The package can be compiled once this export holds an episode list and a specification that keeps every rule.",
    "package_button": "Compile the package",
    "package_none": "No package has been compiled for this export yet.",
    "package_stale": "The specification or the episode list has changed since the package was compiled, so the package below is not the one that the choices above would give. The clinician compiles it again.",
    "package_built": "Schemalyser compiled the package on {date} from the saved hospital schema {schema}, for {count} {form}.",
    "outcomes": {
        "packaged": "Schemalyser wrote a package for this section.",
        "declared without SQL": "The catalogue declares this measure without SQL, so this section has no package.",
        "refused by the role policy": "The role policy refused this section's statement, so Schemalyser wrote no package for it.",
        "not packaged": "Schemalyser wrote no package for this section.",
    },
    "leaves": "This section may leave the hospital, with the hospital's approval under its own rules.",
    "stays": "This section stays inside the hospital.",
    "claims_heading": "The feasibility report",
    "claim_requirements": "That every requirement of this section is mapped and checked against the database:",
    "claim_coverage": "That the coverage of the recording pathways has been assessed for the period:",
    "claim_holds": "established",
    "claim_not": "not established",
    "requests_heading": "The evidence requests",
    "roles_sql": "Show the SQL over the parts of the record",
    "series_heading": "The steps after each of which the database analyst can stop:",
    "series": {
        "count": "A count of the episodes' anaesthetics over the period. If the count is not what the clinician expected, the database analyst stops here.",
        "coverage": "A count of how many of those anaesthetics reach each larger table through its link. If any count is not what the clinician expected, the database analyst stops here.",
        "rows": "The section's own result, reached from those anaesthetics by their keys.",
    },
    "series_none": "The script reads no large table, so it runs as one step.",
    "safety_heading": "The safety report",
    "safety_says": "The policy {outcome} on the final text of the script.",
    "manifest_heading": "The package and its manifest",
    "manifest_where": "The package is kept in the project at {path}.",
    "approval_heading": "Approval",
    "approval_note": "The database analyst records approval or refusal here, with their name, which the package records exactly as given. An approval binds to everything that the package was built from, so any later change voids it.",
    "approval_none": "No one has yet recorded approval or refusal of this section.",
    "voided": "A change has voided the approval and the plan review of this section.",
    "by_label": "The database analyst's name:",
    "approval_note_label": "A note, if any:",
    "approve_button": "Record approval",
    "refuse_button": "Record refusal",
    "approve_needs_name": "An approval or a refusal names the database analyst who gives it.",
    # 8. The results.
    "step8": "8. The results",
    "step8_who": "The database analyst returns the files of each section that ran, and the clinician adds them here.",
    "returned_note": "The workbench imports the outcome and the plan into the hospital schema as evidence, through its evidence import, which saves a new version of the hospital schema in this project. It keeps the result in a results package in this project, which is patient-derived and stays inside the hospital.",
    "outcome_label": "The outcome of the run:",
    "outcome_note": "The outcome is one row, with the columns rows_returned, seconds and outcome, that the database analyst writes once the section has run on the production database. It records how the run went, and it validates no part of the record.",
    "plan_label": "The estimated plan:",
    "plan_note": "The plan is the estimated plan that the database analyst saved from Management Studio as a .sqlplan file before the run. The hospital schema keeps it as evidence with this package's version.",
    "result_label": "The result:",
    "result_note": "The result is the section's output, as the database analyst saved it from the production run. It is patient-derived, so the workbench keeps it in a results package in this project, and Schemalyser writes one only for a package that the database analyst has approved.",
    "returned_by_label": "The name of the database analyst who ran the section:",
    "returned_button": "Add the returned files",
    "returned_nothing": "Choose at least one returned file to add.",
    "imported": "Schemalyser imported the {what} into the hospital schema, which is now saved in this project as {file}.",
    "import_what": {"plan": "estimated plan", "production outcome": "outcome of the run"},
    "evidence_schema": "The latest version of the hospital schema, with the evidence imported here, is saved in this project as {file}. The package still binds to {schema}, from which it was compiled.",
    "results_none": "No result has been returned for this section yet.",
    "results_written": "Schemalyser wrote the results package on {date}, from the result that {by} returned.",
    "shape": "The output holds {rows} {row_word} of {columns} {column_word}.",
    "shape_same": "Its columns are the ones that the package expected.",
    "shape_differs": "Its columns differ from the ones that the package expected, which were {expected}.",
    "shape_unread": "Schemalyser could not read the output as a CSV file, so this step does not show its shape.",
    "coverage_heading": "The coverage of the record for the period",
    "disclosure_heading": "What was rounded or suppressed",
    "protects": "What that protects:",
    "not_protects": "What it does not protect:",
    "protects_nothing": "Nothing beyond the approval itself.",
}


# The OMOP layer screen, screen 3 of docs/screens.md, in the workbench. Like the export screen's, these words are the
# owner's vocabulary: the anaesthetic record and its parts in plain words, the saved hospital schema, the conversion to
# OMOP and its steps, the test on made-up rows and the release. What the core recorded, such as the reason for a direct
# step or a refusal of the release, is shown as recorded and set apart from these words.
OMOP_SCREEN = {
    "nav": "The OMOP layer",
    "title": "The OMOP layer",
    "lede": "This page shows the conversion of the anaesthetic record into the anaesthesia layer of the hospital's OMOP database, in four steps, in order. It runs the test on made-up rows and writes the release script that the database analyst runs. The workbench never runs anything against the hospital's database, and everything on this page stays in this project.",
    "who": "Who acts:",
    "choose_heading": "The conversion and the saved hospital schema",
    "world_label": "The world:",
    "world_note": "The world is the made-up hospital whose conversion this page shows and tests. The invented hospital comes with Schemalyser, and any other is a world that the project keeps among its worlds. Its conversion holds the steps that write the anaesthesia layer, and its made-up rows are what the test runs on.",
    "schema_label": "The saved hospital schema:",
    "schema_note": "The saved hospital schema is the one file that Describe the record saves, and this list shows those that the project holds. The page compiles each step over the parts of the record through it, so that the classes below and the release script name the hospital's own tables.",
    "schema_none": "The project holds no saved hospital schema yet, so the steps over the parts of the record are compiled through the world's own description of its tables, and no release script can be written. The clinician adds a saved hospital schema on the overview first.",
    "choose_button": "Show this conversion",
    # 1. The conversion.
    "step1": "1. The conversion",
    "step1_who": "The clinician reads this step. The steps are written and reviewed outside this page, and this step shows what they record.",
    "shares": "The conversion of {world} has {count} steps that read the hospital's record. Of these, {roles} {roles_verb} written over the parts of the record and {direct} {direct_verb} written directly from the source tables.",
    "release_shares": "The release script carries the {count} steps of the anaesthesia layer, of which {roles} {roles_verb} written over the parts of the record and {direct} {direct_verb} written directly from the source tables. The steps of the core stand in for the hospital's core OMOP, which its own team maintains, and the release does not carry them.",
    "unrecorded": "{count} of the steps {verb} no route.",
    "not_draft": "The conversion is not marked as a draft.",
    "draft_heading": "The conversion is a draft",
    "problems": "The release would refuse the following route records. Each is given in the words of the release command:",
    "classes_problem": "Schemalyser could not read the class of each step, so the table below shows no class. The release command gives the following reason:",
    "col_step": "Step",
    "col_writes": "What it writes",
    "col_route": "Route",
    "col_class": "Class under the policy",
    "layers": {"core": "The core, which the release does not carry", "anaesthesia": "The anaesthesia layer",
               "derived": "A table derived within the anaesthesia layer"},
    "route_roles": "Over the parts of the record.",
    "route_direct": "Directly from the source tables.",
    "route_none": "The step records no route.",
    "route_derived": "Neither route, because the step reads only the OMOP tables.",
    "reference": "It rests on the following reference:",
    "reason": "The reason that the conversion records:",
    "reviewed": "{by} accepted it on {on}.",
    "review_note": "The note of the review:",
    "not_reviewed": "The conversion records no review of this step.",
    "class": "Class {grade}.",
    "class_rules": "It breaks the rule {rules}.",
    "class_recorded": "The conversion records this class, with the following reason:",
    "class_unread": "No class has been read.",
    "alternative": "An alternative to this step, which the release does not carry:",
    "roles_step": "The step over the parts of the record that waits beside this step, which the release does not carry:",
    "gates_heading": "The quality gates",
    "gates": "The release script runs {count} quality gates after the steps, each of which lists the rows that break a rule.",
    # 2. The test on made-up rows.
    "step2": "2. The test on made-up rows",
    "step2_who": "The clinician starts the test and reads its report.",
    "test_note": "One action runs the test on made-up rows. Schemalyser builds the world's tables from made-up rows, plants the scenarios, runs the conversion over them, and sets what the conversion writes against rows that were written by hand beforehand and held apart from it. Nothing reaches the hospital's database.",
    "rows_label": "Rows for each source table:",
    "rows_note": "This many made-up rows are written to each table of the world. More rows take longer and find more.",
    "profile_label": "The profile:",
    "profiles": {"fast": "Fast, on DuckDB alone", "full": "Full, with the Data Quality Dashboard"},
    "profile_note": "The fast profile runs every check apart from the Data Quality Dashboard, in seconds. The full profile also loads the tables into an OMOP database on this computer and runs the Data Quality Dashboard, the public set of checks of an OMOP database, which takes some minutes and needs Docker, PostgreSQL and the Athena vocabulary. The full profile can pass only with SQL Server as well.",
    "engine_label": "The engine:",
    "engines": {"duckdb": "DuckDB", "sqlserver": "DuckDB and SQL Server"},
    "engine_note": "DuckDB runs on this computer. With SQL Server as well, the test also runs the release script on a SQL Server that the project's harness starts, and compares the two.",
    "vocabulary_label": "The vocabulary:",
    "vocabularies": {"": "As the profile chooses", "sample": "The sample of five public concepts", "athena": "The pinned Athena release"},
    "vocabulary_note": "The vocabulary is the set of standard concepts to which the conversion maps the hospital's codes. The sample holds five public concepts and needs nothing more. The Athena release is the full public vocabulary, which the owner downloads once from Athena; the workbench reads it where it lies and keeps its working copy in this project.",
    "test_button": "Run the test",
    "test_none": "No test on made-up rows has been run in this project yet.",
    "test_of": "The report below is that of the test {name}, on {world} at {rows} rows, in the {profile} profile on {engine}. Its own page shows every check and each step's reconciliation.",
    "test_open": "Open the test's own page",
    "test_no_report": "The test wrote no report.",
    "outcome": "The {profile} profile {outcome}, in {seconds} seconds.",
    "outcome_words": {"passed": "passed", "failed": "failed"},
    "scenarios_heading": "Every planted scenario against its held-out rows",
    "scenarios_note": "Each scenario plants rows in the made-up source. Its expected rows were written by hand and are held apart from the conversion, and the test sets what the conversion writes against them.",
    "scenarios_count": "{passed} of {count} planted scenarios passed.",
    "col_scenario": "Scenario",
    "col_outcome": "Outcome",
    "col_steps": "The steps that write what it reads",
    "met": "met",
    "not_met": "not met",
    "expected_found": "The rows expected were {expected}, and the test found {found}.",
    "roles_heading": "The scenarios over the parts of the record",
    "roles_note": "Each step written over the parts of the record also runs on made-up rows of the parts themselves, and what it writes is set against the rows held out for it.",
    "roles_none": "The conversion has no step over the parts of the record, so no scenario of this kind ran.",
    "roles_rows": "The scenario expects {expected} rows, and the step wrote {found}. {missing} expected rows were missing, and {unexpected} rows were written that it does not expect.",
    "roles_error": "The step could not run on the scenario's rows:",
    "reconciliation_heading": "The reconciliation, in its four groups",
    "reconciliation_note": "The reconciliation runs each step again with its joins and conditions applied one at a time, so that every source row that does not reach OMOP is put down to the join or the condition that left it out. It traced {traced} of {steps} steps. The test can pass only when the group of unexplained discrepancies is empty.",
    "groups": {"accounted": "Every excluded row accounted for", "fan_out_confirmed": "Several rows from one source row, as allowed",
               "not_traced": "Could not be traced", "unexplained": "Unexplained discrepancies"},
    "dqd_heading": "The Data Quality Dashboard",
    "dqd_not_run": "The dashboard did not run in this test, because {reason}.",
    "dqd_permitted": "The world permits {count} failures of the dashboard, each with its reason, and a full run sets what the dashboard finds against them.",
    "dqd_ran": "The dashboard ran {checks} checks: {passed} passed, {failed} failed, {could_not_run} could not run, {did_not_finish} did not finish and {not_applicable} did not apply. Of those that failed or could not run, {expected} are permitted and {unexpected} are not.",
    "dqd_unexpected": "The failures that the world does not permit:",
    "dqd_none_unexpected": "Every failure that the dashboard found is one that the world permits.",
    "dqd_expected": "The permitted failures, by their reason:",
    "rewrites_heading": "The rewrites between the two engines",
    "rewrites_note": "Each step runs on SQL Server as written and on DuckDB in a translated form. The translation applied the following rewrites, by name, beyond the change of dialect itself:",
    "rewrites_none": "The translation applied no rewrite beyond the change of dialect itself.",
    "rewrites_steps": "It applied this rewrite to {steps}.",
    "release_eq_heading": "Release equivalence",
    "release_eq_note": "Release equivalence runs the release script itself on SQL Server and compares what it writes with DuckDB's translated form, so that the artefact the database analyst runs is the one that was tested.",
    "package_eq_heading": "Package equivalence",
    "package_eq_note": "Package equivalence compares an execution package's script, run byte for byte on SQL Server, with DuckDB's translated form of the same text, result set by result set.",
    "states": {"passed": "passed", "failed": "failed", "not run": "not run", "not run on SQL Server": "not run on SQL Server"},
    # 3. The release.
    "step3": "3. The release",
    "step3_who": "The clinician writes the release script, and the database analyst runs it after the hospital's core OMOP has been refreshed.",
    "release_note": "The release script fills the anaesthesia tables beside the hospital's core OMOP tables and changes no core table. Schemalyser writes it from the world's conversion, compiled through the saved hospital schema chosen above, so it names the hospital's tables and stays in this project and on the hospital's own computers. The release refuses a step that records no route, a direct step without its review, and a step it carries that the policy places in class D.",
    "release_button": "Write the release script",
    "release_needs": "A release script is written once the project holds a saved hospital schema.",
    "release_none": "No release script has been written in this project yet.",
    "release_written": "Schemalyser wrote the release script on {date} from the conversion of {world}, compiled through the saved hospital schema {schema}. It is kept in this project as {path}.",
    "release_refused": "Schemalyser has not written the release script from the conversion of {world} through the saved hospital schema {schema}. The release command gave the following reason:",
    "header_heading": "The header of the script, which states its classes and its routes:",
    "printed_heading": "What the release command printed, with the line that runs the script:",
    "script_copy": "Copy the script",
    "script_show": "Show the release script, {lines} lines",
    "manifest": "Beside the script, source_manifest.csv lists the source tables and columns that the steps read, and step_classes.json holds the policy's report on each step and gate.",
    # 4. Equivalence per question.
    "step4": "4. Equivalence per question",
    "step4_who": "The clinician reads this step.",
    "equivalence_says": "For each question of the project, the table shows whether its answer over the parts of the record, from the hospital's source and from the hospital's OMOP database, has been shown to be the same. That equivalence is never assumed from the conversion passing its tests. It can be shown only once the parts of the record can be filled from the hospital's core OMOP, and no comparison has been made yet.",
    "col_question": "Question",
    "col_equivalence": "Equivalence of the answer from the source and from OMOP",
    "equivalence_states": {"not yet shown": "not yet shown"},
    "equivalence_none": "The project holds no question yet. The clinician adds one on the overview.",
}
