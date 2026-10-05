"""Ties the project to one target query: what the query rests on, what is known of it, and whether to run it.

A target query is one T-SQL SELECT against the OMOP tables, written as omop.<table>, as the steps
of a conversion are. It may read the conversion's custom tables, such as omop.anaesthetic, which
the derived layer builds from the OMOP rows, and the trace then runs through the derived steps to
the anaesthesia and core steps beneath them. The tool cannot see how the real source database stores an anaesthetic. The
conversion's steps are its hypothesis, written from public knowledge, and the data team's sample
queries, read by the analyser, are the evidence for or against that hypothesis. For one target
query this module

- reads the query, and reports the OMOP fields it uses and the concepts it compares them with;
- traces it back to the conversion steps that can write the rows it keeps, and then to every
  step that writes an OMOP table those steps read, marking each step's layer;
- derives, with the analyser itself, the source tables, columns, joins, filters and mapping
  vocabularies that those steps rest on, so that requirement and evidence share one vocabulary
  of findings;
- states each requirement as a row of a checklist, in the layout of questions.csv with four
  further columns, blocking, kind, intent and route, and works out from the inputs in hand how far
  each is answered;
- gives each join, filter and vocabulary the intent that the author of the steps wrote for it in
  intents.json; for a join that no sample query makes, the route by which the sample queries get
  between the same two tables; and, for a step that offers alternatives in conversion.json, which
  version the sample queries support best;
- writes a short statement of readiness; and
- on request, runs the conversion over the sandbox and then the query over the synthetic rows; and
- on request, writes a draft of the same question as one T-SQL query against the source tables
  alone, composed from the relevant steps, with each assumption it rests on numbered, for the
  analytics team to correct and run as the benchmark that the OMOP query must match. The draft
  names the hospital's tables and local codes, so it is for use inside the hospital only.

    python -m schemalyser.target WORLD CONVERSION TARGET.sql [--checks FILE] [--profile FILE] [--run] [--draft] --out FOLDER

Which steps the query depends on. A step is relevant when it writes a table that the query reads
and can write rows that the query keeps. The query keeps a row of an alias only if every field
that a condition on that alias needs is filled, so a step that does not write such a field is
left out. Where a condition compares a field with fixed values, such as a concept field with
concept ids, a step is kept when the expression it writes into that field can give one of those
values: a constant in the step, or the target concept of a mapping row under a vocabulary that
the step looks up. Where the tool cannot tell, because the field comes from a vocabulary whose
rows are only proposed from labels (derived_mappings.json), the step is kept as possible and the
readiness says so. Where the field comes straight from a source column and another step writes
the compared value itself, the reading step is taken to mean that other step. The same test then
runs from each relevant step to the steps before it that write an OMOP table it reads, until no
step is added. Only the steps that are certainly relevant contribute requirements.

What blocks. An item is blocking when, while it is open, the shadow database cannot simulate the
query at all or cannot be trusted to simulate it faithfully:

- a source table or column that a relevant step reads and the catalogue does not list;
- a join that a relevant step makes as an inner join and that no sample query makes;
- a concept that the query needs and to which no mapping row leads;
- a mapping vocabulary that a relevant step looks up as an inner join, which keeps only the
  rows that have a mapping row, when the vocabulary holds no rows at all;
- a core field that an anaesthesia step joins on with an inner join, and each OMOP field that the
  query itself reads from a table that only the core writes.

Everything else bears on how realistic the result is (filters, the meaning of values, durations
and rates, optional joins, the further vocabularies) and does not block. The shadow database is
ready for the query when no blocking item is open.

Every name in the checklist and the readiness comes from the catalogue, the site rules, the CDM
field list, the conversion's own step files and vocabularies, or the fixed wording below. No
value from the check results is ever written, and nothing from the text of a sample query. The
concept ids are those that the target query names, which are public.
"""
import argparse
import csv
import io
import json
import re
from collections import Counter
from pathlib import Path

import sqlglot
from sqlglot import exp

from . import checks as checking
from . import convert
from . import harness
from . import profile as core_profile
from . import concepts
from . import facts as facts_module
from . import questions as register
from . import sql_evidence
from . import routes as routing
from .extract import _passed_through, analyse_request, decode
from .translate import OMOP_SCHEMA
from .vocabulary import not_fully_read

# intent says, in the words of the step's author, what a join, filter or vocabulary is meant to do;
# route gives, for an open join, the joins by which the sample queries get between the same two tables.
# query_state, query_reason and query give, for an open item that check results would settle or help to
# settle, the plain query or queries that the analyst can run for it, why, and whether they can run yet.
# phase says which phase of the work needs the item: "source", the answer from the source database by the
# source draft alone, or "release", the later OMOP release. (The register's own stage is its A to E.)
LAYOUT = register.LAYOUT + ("blocking", "kind", "intent", "route", "query_state", "query_reason", "query", "phase")
PHASES = ("source", "unneeded", "release")
# The states of an item's queries: ready to run, waiting for the table sizes, not offered because a table is large,
# or run already and found nothing.
QUERY_STATES = ("ready", "waiting", "large", "ran")
BLOCKING = ("yes", "no")
KINDS = ("table", "column", "relationship", "filter", "codes", "meaning", "timing", "core")
# The register's people and mechanisms, with the sample queries from the data team added, because
# they are the evidence that this checklist weighs and the register has no word for them.
WHO = register.WHO
MECHANISMS = register.MECHANISMS + ("sample queries",)
SOURCES = register.SOURCES + ("sample queries", "a person")
STAGE = {"table": "A", "column": "A", "relationship": "A", "filter": "A", "codes": "D", "meaning": "B",
         "timing": "B", "core": "C"}

# All of the wording, in one place, so that it can be reviewed together. It is a draft until the
# clinical lead approves it. The rows reused from the register keep the register's own wording.
WORDING = {
    "table": {
        "question": "The steps behind this query read the table {table}, which the catalogue lists, and the data team's SQL shows whether the team reads it in the same way.",
        "decides": "Whether the shadow database can build {table}, and so whether it can simulate the query at all.",
        "evidence_needed": "The analytics team supplies a catalogue export that lists {table}, and sample queries from the data team that read it.",
        "who": "analytics team", "mechanism": "catalogue export"},
    # A table or column that the steps read and the catalogue does not hold. The catalogue is the evidence, so the
    # item asks for no evidence about the name itself: the step needs another route.
    "table-absent": {
        "question": "The steps behind this query read the table {table}, which the catalogue does not hold.",
        "decides": "Whether the conversion can make, at this site, the rows of the steps that read {table}.",
        "evidence_needed": "The person who keeps the conversion gives each step that reads {table} an alternative that reads only tables that the catalogue holds, or, if the site holds the same data under another name, writes that name into the step.",
        "who": "clinical lead", "mechanism": "a decision"},
    "column-absent": {
        "question": "The steps behind this query read the column {column}, which the catalogue does not hold.",
        "decides": "Whether the conversion can fill, at this site, what the steps take from {column}.",
        "evidence_needed": "The person who keeps the conversion gives each step that reads {column} an alternative that reads only columns that the catalogue holds, or, if the site holds the same data under another name, writes that name into the step.",
        "who": "clinical lead", "mechanism": "a decision"},
    # A step that gave way to one of its alternatives, because the catalogue does not hold what the step reads.
    "route": {
        "question": "The route that the conversion takes in place of {step} depends on what the catalogue holds.",
        "decides": "Which tables and columns the answer rests on at this site.",
        "evidence_needed": "The catalogue settles the route, so nothing more is needed. If the site holds {missing} under another name, the person who keeps the conversion writes that name into {step}.",
        "who": "clinical lead", "mechanism": "catalogue export"},
    "route-effect": {
        "question": "The route that the conversion takes in place of {step} depends on what the catalogue holds, and the route that it takes here changes the answer.",
        "decides": "Whether the answer can stand with the change that the route makes.",
        "evidence_needed": "The clinical lead decides whether the answer can stand with this change, or names the column that the site uses for the same purpose, so that the step can read it.",
        "who": "clinical lead", "mechanism": "a decision"},
    "column": {
        "question": "The steps behind this query read the column {column}, which the catalogue lists, and the data team's SQL shows whether the team reads it in the same way.",
        "decides": "Whether the shadow database can fill {column}, and so whether it can simulate the query at all.",
        "evidence_needed": "The analytics team supplies a catalogue export that lists {column}, and sample queries from the data team that use it.",
        "who": "analytics team", "mechanism": "catalogue export"},
    "relationship": {
        "question": "The steps behind this query join {left} to {right}, and whether the data team links these two columns in the same way is not yet known.",
        "decides": "Whether the rows that the query counts are linked in the shadow database as they are in the real one.",
        "evidence_needed": "The analytics team supplies sample queries from the data team that join {left} to {right}. If the data team links these tables through other columns, the clinical lead changes the conversion steps to match.",
        "who": "analytics team", "mechanism": "sample queries"},
    "filter": {
        "question": "The steps behind this query keep or leave out rows by the value of {column}, and whether the real database uses the values that the steps expect is not yet known.",
        "decides": "Which rows the shadow database passes on to the query.",
        "evidence_needed": "The analytics team supplies sample queries that filter {column}, and runs the query given with this item, or the whole check script, so that the check results list the values of that column.",
        "who": "analytics team", "mechanism": "check script"},
    "codes-concept": {
        "question": "The query looks for rows with {concept_named} in {field}, and whether a mapping row leads to that concept from a code that the real database holds is not yet known.",
        "decides": "Whether the shadow database holds any row that the query looks for. Without such a mapping row, the query finds nothing.",
        "evidence_needed": "The clinical lead writes a mapping row that leads to {concept_named}, under {vocabularies}, for each source code that means it, and the analytics team runs the query given with this item, or the whole check script, so that the check results list the codes that the real database holds.",
        "who": "clinical lead", "mechanism": "mapping rows"},
    "codes-vocabulary": {
        "question": "The steps behind this query look up {columns} under {vocabulary}, and whether every code that matters has a mapping row is not yet known.",
        "decides": "Which rows the steps keep, and the concepts that they write.",
        "evidence_needed": "The clinical lead writes a mapping row under {vocabulary} for each code that should be kept, and the analytics team runs the query given with this item, or the whole check script, so that the check results list the values of {columns}.",
        "who": "clinical lead", "mechanism": "mapping rows"},
    "codes-constant": {
        "question": "The steps behind this query look up a code of their own under {vocabulary}, and whether its mapping row is in place is not yet known.",
        "decides": "The concept that the steps write for that code.",
        "evidence_needed": "The clinical lead writes the mapping row under {vocabulary} that the steps look up, and confirms its concept.",
        "who": "clinical lead", "mechanism": "mapping rows"},
    "codes-possible": {
        "question": "Whether the step {step} also writes rows that the query keeps is not yet known.",
        "decides": "Whether the query counts the rows of {step} as well as those of the steps that it certainly rests on.",
        "evidence_needed": "The clinical lead reviews the mapping rows under {vocabularies} once they have been proposed, and confirms whether any of them leads to a concept that the query looks for.",
        "who": "clinical lead", "mechanism": "mapping rows"},
    "meaning-possible": {
        "question": "Whether the step {step} also writes rows that the query keeps is not yet known.",
        "decides": "Whether the query counts the rows of {step} as well as those of the steps that it certainly rests on.",
        "evidence_needed": "The clinical lead confirms whether {step} can write a row that the query keeps, and narrows the query or the step where it should not.",
        "who": "clinical lead", "mechanism": "a decision"},
    "meaning": {
        "question": "The query reads {field}, which the steps fill from {columns}, and what those columns hold, and in which units, has not yet been confirmed.",
        "decides": "Whether the values that the shadow database puts in {field} look like the real ones, and so whether the result of the query is realistic.",
        "evidence_needed": "The clinical lead gives {columns} a role in the site rules, with its unit and, where the column holds more than one kind of value, the code that selects each kind.",
        "who": "clinical lead", "mechanism": "site rules"},
    "core": {
        "question": "The query reads {field}, which only the core fills, and whether the real core fills this field is not yet known.",
        "decides": "Whether the query can be run against the real core as it is written.",
        "evidence_needed": "The central OMOP team runs the query of the core's own records given with this item, or the whole core profile script, and returns the result, which reports the tables of CDM 5.4 that the core holds and the fields that it lacks.",
        "who": "central OMOP team", "mechanism": "core profile script"},

    # The sentences that state the evidence in hand. Each holds only counts and names.
    "in_hand": {
        "table_listed": "The catalogue lists {table}.",
        "table_missing": "The catalogue does not list {table}.",
        "route_taken": "Schemalyser uses {chosen} in place of {step}, because the catalogue does not hold {missing}.",
        "route_passed": "Schemalyser passed over {alternative}, because the catalogue does not hold {missing} either.",
        "column_listed": "The catalogue lists {column}.",
        "column_missing": "The catalogue does not list {column}.",
        "used_none": "None of the {total} sample queries uses it.",
        "used_one": "1 of the {total} sample queries uses it.",
        "used_some": "{count} of the {total} sample queries use it.",
        "joined_none": "None of the {total} sample queries joins these two columns.",
        "joined_one": "1 of the {total} sample queries joins these two columns.",
        "joined_some": "{count} of the {total} sample queries join these two columns.",
        "join_steps": "The join is made in {steps}.",
        "join_optional": "Each step that makes this join keeps its rows when no match is found.",
        # The route by which the sample queries get between the two tables of an open join.
        "route": "The sample queries reach {end} from {start} through {through}, by {hops}.",
        "route_direct": "The sample queries join {start} to {end} directly, but through other columns, by {hops}.",
        "route_hop": "{left} = {right} ({queries})",
        "no_route": "The sample queries do not connect {start} and {end} at all, through any route of up to {bound} joins.",
        "unread_none": "The analyser read all {total} sample queries in full.",
        "unread_one": "1 of the {total} sample queries could not be read in full, because it holds a part that the analyser could not parse, SQL that is built as text when it runs, a call to a stored procedure, a statement of a kind that the analyser does not analyse, or a query whose columns it could not match to their tables. The evidence may lie in that part.",
        "unread_some": "{count} of the {total} sample queries could not be read in full, because each holds a part that the analyser could not parse, SQL that is built as text when it runs, a call to a stored procedure, a statement of a kind that the analyser does not analyse, or a query whose columns it could not match to their tables. The evidence may lie in those parts.",
        # A step that offers alternatives, weighed against the sample queries.
        "version_written": "The sample queries make {supported} of the {joins} that {step} makes as written.",
        "version_alternative": "The sample queries make {supported} of the {joins} that {alternative}, an alternative to {step}, makes.",
        "version_better": "The sample queries support the alternative {alternative} better than {step} as written.",
        "version_same": "The sample queries support {step} as written at least as well as any of its alternatives.",
        # A finding that the team's SQL showed in an earlier run, kept in sql_evidence.json, where the request files to hand do not show it.
        "seen_one": "Schemalyser saw this in 1 of the team's queries on {date}.",
        "seen_some": "Schemalyser saw this in {count} of the team's queries on {date}.",
        "filtered_none": "None of the {total} sample queries filters this column.",
        "filtered_one": "1 of the {total} sample queries filters this column.",
        "filtered_some": "{count} of the {total} sample queries filter this column.",
        "no_checks": "No check results have been supplied.",
        "listed": "The check results list the values of this column.",
        "not_listed": "The check results do not list the values of this column.",
        "ran_empty": "The query for this column has run and found no value that ten or more rows hold, so the check results list none.",
        "codes_ran_empty": "The query for {columns} has run and found no value that ten or more rows hold.",
        "no_fixed_value": "The steps compare this column with no fixed value, so its values do not need to be listed.",
        "literals_found": "The listed values include {found} of the {compared} fixed values that the steps compare this column with.",
        "concept_rows": "The conversion holds {rows} leading to this concept, under {vocabularies}.",
        "concept_no_rows": "No mapping row leads to this concept. The steps that could write {field} look up {vocabularies}.",
        "concept_no_vocabulary": "No mapping row leads to this concept, and no step writes {field} through a mapping vocabulary.",
        "concept_constant": "The step {step} writes this concept itself.",
        "codes_listed": "The check results list {listed} of {columns}, and {mapped} a mapping row leading to this concept.",
        "codes_not_listed": "The check results do not list the values of {columns}.",
        "vocabulary_rows": "The conversion holds {rows} under this vocabulary.",
        "vocabulary_gate": "The steps keep only the rows whose code has a mapping row under this vocabulary.",
        "vocabulary_listed": "The check results list {listed} of {columns}, and {mapped} a mapping row under this vocabulary.",
        "of_them_none": "none of them has",
        "of_them_one": "1 of them has",
        "of_them_some": "{count} of them have",
        "possible_derived": "The mapping rows under {vocabularies} are proposed from labels when the runner is given a vocabulary download, so the tool cannot tell in advance which concepts they lead to.",
        "possible_source": "The step fills {fields} from the source, so the tool cannot tell from the step's SQL and the mapping rows whether the step writes a row that the query keeps.",
        "roles": "The site rules give {column} these roles: {roles}.",
        "no_roles": "The site rules give {column} no role.",
        "no_profile": "No core profile has been supplied.",
        "core_present": "The core profile shows that the core's {table} table holds this field.",
        "core_field_absent": "The core profile shows that the core lacks this field.",
        "core_table_absent": "The core profile shows that the core does not have {table}.",
        "core_unknown": "The core profile does not say whether the core holds {table}.",
    },

    # The readiness statement.
    "readiness": {
        "title": "Readiness of the shadow database for this query",
        "derived_steps": "The query reads custom tables built by {count} of the derived layer: {steps}.",
        "anaesthesia_steps": "The query rests on {count} of the anaesthesia layer: {steps}.",
        "core_steps": "It also rests on {count} of the core layer, which the hospital's core OMOP database must supply: {steps}.",
        "possible_steps": "The tool could not rule out {count}, which may also write rows that the query keeps: {steps}.",
        "no_steps": "No step of the conversion writes rows that this query keeps, so the shadow database cannot simulate it.",
        "blocking": "Of the {total} items that would stop the shadow database from simulating this query, {answered} are answered, {partly} are partly answered and {open} are open.",
        "act": "{who} must act on {item}, {how}.",
        "ready": "The shadow database is ready for this query, because none of the items that would stop the simulation is open.",
        "ready_partly": "Of those items, {partly} are answered only in part, and further evidence may still change them.",
        "not_ready_one": "The shadow database is not yet ready for this query, because 1 item that would stop the simulation remains open.",
        "not_ready": "The shadow database is not yet ready for this query, because {open} items that would stop the simulation remain open.",
        "realism": "A further {total} items bear on how realistic the result is, such as filters, the meaning of values and durations, and {open} of them are open. They do not stop the simulation.",
        # The two stages: the answer from the source database, and the later OMOP release.
        "source_ready": "The question is ready to be answered from the source database, because none of the {total} blocking items that the source query rests on is open.",
        "source_not_ready_one": "The question is not yet ready to be answered from the source database, because 1 of the {total} blocking items that the source query rests on remains open.",
        "source_not_ready": "The question is not yet ready to be answered from the source database, because {open} of the {total} blocking items that the source query rests on remain open.",
        "unneeded": "The answer to this question does not depend on {count} of the checklist, of which {open} are not yet answered, so they do not stop it being answered from the source database. The OMOP release still needs them.",
        "release_ready": "The question is ready for the OMOP release, because none of its {total} blocking items is open.",
        "release_not_ready_one": "The question is not yet ready for the OMOP release, because 1 of its {total} blocking items remains open.",
        "release_not_ready": "The question is not yet ready for the OMOP release, because {open} of its {total} blocking items remain open.",
        # Awaiting approval: the planted scenarios, stated only when the query has been run.
        "scenarios_none": "None of the conversion's planted scenarios bears on the tables that this query reads.",
        "scenarios_met_one": "1 of the conversion's planted scenarios bears on the tables that this query reads, and the run met all {expectations} of its expectations.",
        "scenarios_met": "{count} of the conversion's planted scenarios bear on the tables that this query reads, and the run met all {expectations} of their expectations.",
        "scenarios_unmet": "{count} of the conversion's planted scenarios bear on the tables that this query reads, and the run did not meet {unmet} of their {expectations} expectations, so the result on the synthetic rows should not be relied on until it does.",
        # What to do about an open join that the sample queries make by another route, or that an alternative avoids.
        "advice_alternative": "If {alternative} makes the same rows as {step}, the clinical lead can settle {item} by making {alternative} the step in conversion.json, because the sample queries support it better.",
        "advice_route": "If the route that the sample queries take through {through} links the same rows, the clinical lead can settle {item} by changing {steps} to follow that route.",
        "advice_direct": "If the columns that the sample queries join link the same rows, the clinical lead can settle {item} by changing {steps} to join those columns instead.",
    },
    # How each open item is named in the readiness, and how the person who acts can settle it.
    "item": {
        "table": "the table {table}",
        "column": "the column {column}",
        "table-absent": "the table {table}, which the catalogue does not hold",
        "column-absent": "the column {column}, which the catalogue does not hold",
        "route-effect": "the change that the route of {chosen} makes to the answer",
        "relationship": "the join of {left} to {right}",
        "codes-concept": "a mapping row for {concept_named} in {field}",
        "codes-vocabulary": "the mapping rows under {vocabulary}",
        "codes-constant": "the mapping row under {vocabulary}",
        "core": "the core field {field}",
        "C-source-value": "the core field {field}, which the anaesthesia steps join on",
    },
    "how": {
        "catalogue export": "by supplying a catalogue export that includes it",
        "sample queries": "by supplying sample queries from the data team that make it",
        "mapping rows": "by writing the mapping rows",
        "core profile script": "by running the query given with this item, or the whole core profile script, and returning the result",
        "check script": "by running the query given with this item, or the whole check script, and returning the results",
        "site rules": "through the site rules",
        "a decision": "by a decision",
    },
    "who": {"analytics team": "The analytics team", "central OMOP team": "The central OMOP team",
            "clinical lead": "The clinical lead"},
    # The facts that a person confirmed, and the questions that a person can answer from knowledge.
    "facts": {
        "join_yes": "A person confirmed on {date} that these two columns join.",
        "join_no": "A person said on {date} that these two columns do not join, so {steps} must change.",
        "join_no_instead": "A person said on {date} that these two columns do not join and that {left} joins to {right} instead, so {steps} must change to join those columns.",
        "filter_yes": "A person confirmed on {date} that the steps compare this column with the right fixed values.",
        "filter_no": "A person said on {date} that the steps do not compare this column with the right fixed values, so the step that compares it must change.",
        "codes_yes": "A person gave on {date} {count} that mean this, which are now mapping rows of the site.",
        "ask_join": "Please confirm whether {left} joins to {right}, so that each row of {left_table} finds the row of {right_table} that it belongs with. If it does not, please name the columns of the two tables that do join.",
        "ask_filter_keep": "Please confirm whether the conversion is right to keep only the rows in which {column} holds {values}.",
        "ask_filter_leave": "Please confirm whether the conversion is right to leave out the rows in which {column} holds {values}.",
        "ask_codes": "Please give the local codes of {columns} that mean {concept}, so that they can be mapped under {vocabulary}.",
        "questions_head": "Questions about {name} for a colleague who knows the source database. Each can be answered from knowledge, without running a query.",
        "questions_foot": "Please answer each question with yes or no, or with the codes or columns that it asks for, and say who answered and on what date.",
        "this_question": "this question",
    },
    # The queries that the checklist offers with an item: why the item needs them, what state they are in,
    # and how the item can be answered without them where the tool allows it.
    "query": {
        "filter": "The query lists each value that {columns} holds in at least ten rows, because the steps keep or leave out rows by its value, and Schemalyser needs to see whether the values that they compare it with occur in the real database.",
        "codes-concept": "The query lists each code that {columns} holds in at least ten rows, so that Schemalyser can see whether a code that the real database holds has a mapping row that leads to this concept.",
        "codes-vocabulary": "The query lists each code that {columns} holds in at least ten rows, so that Schemalyser can see which of the codes that the real database holds have a mapping row under {vocabulary}.",
        "spans": "The query counts the rows of {table} by the time from {first} to {second}, in fixed bands, and the shadow database then draws its intervals in proportion to those counts.",
        "still_in_place": "The query counts the rows of {column} by year, so that the clinical lead can compare the rows that hold the sentinel date with the rows of the whole table.",
        "death_share": "The query counts the rows of {table} and the empty values of {column}, so that the clinical lead can work out the share of children who have a date of death.",
        "sampled": "Because {tables} holds more than {limit} rows, the query reads a sample of its pages and scales each count up to the whole table.",
        "waiting": "Schemalyser will show the query for this item once the check results give the size of {tables}, because it offers no query on a table whose size it does not know. The table sizes query at the head of this checklist gives it.",
        "count": "SQL Server keeps no record of the size of {tables}, as for a view, so the query given here counts its rows, up to just past {limit}. Once its result is in, Schemalyser will show the query that answers this item.",
        "unsampled": "Schemalyser offers no query for this item, because {tables} holds more than {limit} rows and SQL Server keeps no record of its size, as for a view, so a query cannot read a sample of it. The question for a colleague, where there is one, can settle the item instead.",
        "large": "Schemalyser offers no query for this item, because the count needs every row of {tables}, which holds more than {limit} rows. The whole check script still counts a table of up to {script_limit} rows, if the analytics team can run it when the server is quiet.",
        # The plain queries of the core profile, which the central OMOP team runs.
        "profile_core": "The query reads from SQL Server's own records whether the core holds {field}, and with which type, without reading any table.",
        "profile_match": "The query takes up to {sample} distinct keys from each source table that the anaesthesia steps join to {field}, and counts how many of them the core holds, so that Schemalyser can see whether the core keeps the source system's keys in this field.",
        "profile_tier_one": "The central OMOP team runs the query of the core's own records first, because Schemalyser offers no query on a core table whose size it does not know.",
        "profile_setting": "Schemalyser cannot offer the query that measures this join until source_prefix is set in release.json, because the query reaches the source tables through that prefix.",
        "profile_source_sizes": "The query for the join to {tables} waits for the size of that table, which the table sizes query at the head of this checklist gives.",
        "profile_source_sizes_many": "The queries for the joins to {tables} wait for the sizes of those tables, which the table sizes query at the head of this checklist gives.",
        "profile_absent": "The core profile shows that the core does not have {table}, so Schemalyser has no join to measure.",
        "profile_count": "SQL Server keeps no record of the size of {table}, so the query given here counts its rows and reads the whole of it.",
        "profile_core_side": "Because {table} holds more than {limit} rows, the query for {joins} measures the join from the core side: it takes up to {sample} distinct values from a sample of {table} and counts how many of them are keys in the source system, which gives an estimate.",
        "profile_large": "Schemalyser offers no query to measure the join, because the query would look keys up in {table}, which holds more than {limit} rows. The whole core profile script measures it, if the central OMOP team can run it when the server is quiet.",
        "profile_ran_empty": "The query for the join to {sources} has run and found no key in the source table.",
        "ran": "The query for this item has run and found nothing that ten or more rows hold, so Schemalyser does not offer it again.",
        "direct_codes": "If the clinical lead already knows which codes the real database holds, the clinical lead can write their mapping rows without the query, and the item then stays partly answered until the check results list one of those codes.",
        "direct_tuning": "If the clinical lead already knows the answer, the clinical lead can give {keys} under tuning in the site rules, which answers this item without the query.",
        # The table sizes query at the head of each checklist.
        "sizes": "This query reads the size of each table that the queries of this checklist read, from SQL Server's own records, without reading any of the tables. Schemalyser shows those queries once it knows the sizes, so that it never offers a query that reads the whole of a large table.",
        # The file of queries that the boundary writes for each target query.
        "profile_file": "The queries below are for the central OMOP team, to run on the OMOP database. Each result can be pasted into the page, which adds it to the core profile.",
        "file": "The queries that the checklist for {name} offers now, in the order in which to run them. Each result can be pasted into the page, which adds it to the check results.",
    },
    # Counted nouns: the singular, the plural, and the words for none.
    "nouns": {"item": ("item", "items", "no items"), "code": ("local code", "local codes", "no local codes"), "step": ("step", "steps", "no steps"), "mapping row": ("mapping row", "mapping rows", "no mapping rows"),
              "value": ("value", "values", "no values"), "further step": ("further step", "further steps", "no further steps"),
              "join": ("join", "joins", "no joins"), "query": ("query", "queries", "no queries")},
}

# The roles whose columns time a value, and the groups of tunable parameters that place them.
ROLE_TUNING = {"birth_date": ("ages",), "anaesthetic_start": ("anaesthetic_duration",),
               "anaesthetic_stop": ("anaesthetic_duration",), "administration_time": ("induction", "anaesthetic_duration"),
               "time_during_anaesthetic": ("anaesthetic_duration",), "placement_time": ("placement",),
               "removal_time": ("removal", "still_in_place"), "admission_time": ("admission",),
               "discharge_time": ("discharge",), "death_date": ("death_share", "death_timing")}
# A value timed by its "at" column falls before the anaesthetic for these roles, and during it for the rest.
BEFORE_ROLES = {"weight", "height"}
# Expressions inside which an empty field does not make a condition fail.
NULL_TOLERANT = (exp.Coalesce, exp.Case, exp.Nullif, exp.If)
REFUSED_NODES = tuple(getattr(exp, name) for name in (
    "Insert", "Update", "Delete", "Merge", "Drop", "Create", "TruncateTable", "Alter", "Command", "Into", "Execute", "Use")
    if hasattr(exp, name))
MAPPING_TABLE = "source_to_concept_map"
PUBLISHED_VARIABLE = "$(AnaesPubSchemaName)"


class TargetError(ValueError):
    """The target query, or an input beside it, could not be read."""


def _n(count, noun):
    one, many, none = WORDING["nouns"][noun]
    return none if count == 0 else f"{count:,} {one if count == 1 else many}"


_join = register._join


def _norm(value):
    """The form in which a fixed value is compared: a whole number as digits, and text in capitals."""
    text = str(value).strip()
    try:
        number = float(text)
        if number == int(number):
            return str(int(number))
    except (ValueError, OverflowError):
        pass
    return text.upper()


def _literal(node):
    """The value of a fixed literal, looking through brackets and a minus sign, or None."""
    while isinstance(node, exp.Paren):
        node = node.this
    if isinstance(node, exp.Neg) and isinstance(node.this, exp.Literal):
        return "-" + str(node.this.this)
    if isinstance(node, exp.Literal):
        return str(node.this)
    return None


def _conjuncts(node):
    if node is None:
        return
    if isinstance(node, exp.And):
        yield from _conjuncts(node.this)
        yield from _conjuncts(node.expression)
    elif isinstance(node, exp.Paren):
        yield from _conjuncts(node.this)
    else:
        yield node


def _own_tables(select):
    """The tables and derived tables in a SELECT's own FROM and joins, with the join that brings each in."""
    found = []
    source = select.args.get("from_")
    if source is not None:
        found.append((source.this, None))
    for join in select.args.get("joins") or []:
        found.append((join.this, join))
    return found


def _is_omop(table):
    return isinstance(table, exp.Table) and (table.db or "").upper() == OMOP_SCHEMA.upper()


def _side(join):
    return "" if join is None else (join.args.get("side") or "").upper()


# Reading a SELECT: the conditions that each OMOP alias must meet for its rows to be kept.

def _conditions(select, fields_of):
    """For each OMOP alias that a SELECT reads itself: the fields it must fill and the fixed values it must hold.

    fields_of(alias) gives the CDM fields of the alias's table, or None for an alias that is not an
    OMOP table. Returns {ALIAS: {"write": {field}, "values": {field: {value}}}}. A condition in the
    WHERE clause or in an inner join applies to every alias it names; one in a left join applies only
    to the table that the join brings in. A condition that holds OR, NOT or IS is left aside.
    """
    aliases = {}
    for node, _ in _own_tables(select):
        if isinstance(node, exp.Table) and fields_of(node.alias_or_name.upper()) is not None:
            aliases[node.alias_or_name.upper()] = {"write": set(), "values": {}}

    def owner(column):
        if column.table:
            return column.table.upper() if column.table.upper() in aliases else None
        holders = [a for a in aliases if column.name.lower() in (fields_of(a) or ())]
        return holders[0] if len(holders) == 1 else None

    clauses = [(c, None) for c in _conjuncts(select.args.get("where").this if select.args.get("where") else None)]
    for join in select.args.get("joins") or []:
        side = _side(join)
        applies = None if side == "" else {join.this.alias_or_name.upper()} if side == "LEFT" else set()
        clauses += [(c, applies) for c in _conjuncts(join.args.get("on"))]
    for clause, applies in clauses:
        if clause.find(exp.Or) or clause.find(exp.Not) or isinstance(clause, exp.Is) or clause.find(exp.Is):
            continue
        if clause.find(exp.Subquery, exp.Exists):
            continue
        fixed = None
        if isinstance(clause, exp.EQ):
            for one, other in ((clause.this, clause.expression), (clause.expression, clause.this)):
                column = _passed_through(one)
                value = _literal(other)
                if isinstance(column, exp.Column) and value is not None:
                    fixed = (column, {_norm(value)})
        elif isinstance(clause, exp.In) and not clause.args.get("query"):
            column = _passed_through(clause.this)
            values = [_literal(e) for e in clause.expressions]
            if isinstance(column, exp.Column) and values and all(v is not None for v in values):
                fixed = (column, {_norm(v) for v in values})
        if fixed is not None:
            alias = owner(fixed[0])
            if alias and (applies is None or alias in applies):
                entry = aliases[alias]
                field = fixed[0].name.lower()
                entry["write"].add(field)
                entry["values"][field] = entry["values"][field] & fixed[1] if field in entry["values"] else set(fixed[1])
            continue
        for column in clause.find_all(exp.Column):
            if any(isinstance(a, NULL_TOLERANT) for a in _ancestors(column, clause)):
                continue
            alias = owner(column)
            if alias and (applies is None or alias in applies):
                aliases[alias]["write"].add(column.name.lower())
    return aliases


def _ancestors(node, stop):
    node = node.parent
    while node is not None and node is not stop:
        yield node
        node = node.parent


# The target query.

def custom_fields(folder):
    """The custom tables of a conversion folder, in the form of convert.cdm_fields: table -> [(field, required, type)]."""
    if folder is None:
        return {}
    try:
        tables = convert.read_tables(folder)
    except convert.TablesError as error:
        raise TargetError(str(error)) from None
    found = {}
    for row in convert.custom_rows(tables):
        found.setdefault(row["table"], []).append((row["field"], row["required"] == "Y", convert.DUCK_TYPES.get(row["datatype"], "VARCHAR")))
    return found


def read_target(sql, custom=None):
    """Reads a target query. Returns a plain dictionary; raises TargetError for anything but one SELECT on OMOP tables.

    custom, when given, holds the conversion's custom tables, as custom_fields gives them, which the
    query may read as it reads a table of CDM 5.4. The dictionary holds the parsed tree, the fields
    that the query uses as (table, field), the concept ids that it compares each concept field with,
    and the conditions on each alias.
    """
    fields = dict(convert.cdm_fields(), **(custom or {}))
    try:
        trees = [tree for tree in sqlglot.parse(sql, dialect="tsql") if tree is not None]
    except sqlglot.errors.SqlglotError:
        raise TargetError("the target query cannot be read as SQL") from None
    if len(trees) != 1:
        raise TargetError(f"the target query must be exactly one statement, and this holds {len(trees)}")
    tree = trees[0]
    if not isinstance(tree, (exp.Select, exp.SetOperation)):
        raise TargetError("the target query must be one SELECT")
    for node in tree.walk():
        if isinstance(node, REFUSED_NODES) or (isinstance(node, exp.Select) and node.args.get("into")):
            raise TargetError("the target query may only read, and may not write or run anything")
    named = {cte.alias.upper() for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        if not table.db and table.name.upper() in named:
            continue
        if not _is_omop(table) or table.name.lower() not in fields:
            raise TargetError("the target query may read only the tables of CDM 5.4 and the conversion's custom tables, written as omop.<table>")

    used, concepts, conditions = set(), {}, {}
    for select in tree.find_all(exp.Select):
        own = {node.alias_or_name.upper(): node.name.lower() for node, _ in _own_tables(select) if _is_omop(node)}
        known = lambda alias: {f for f, _, _ in fields[own[alias]]} if alias in own else None  # noqa: E731
        for alias, entry in _conditions(select, known).items():
            conditions.setdefault((id(select), alias), (own[alias], entry))
        for column in select.find_all(exp.Column):
            if column.find_ancestor(exp.Select) is not select:
                continue
            table = own.get(column.table.upper()) if column.table else None
            if table is None and not column.table:
                holders = [t for t in own.values() if column.name.lower() in {f for f, _, _ in fields[t]}]
                table = holders[0] if len(set(holders)) == 1 else None
            if table is None:
                continue
            field = column.name.lower()
            if field not in {f for f, _, _ in fields[table]}:
                raise TargetError(f"{field} is not a field of {table}")
            used.add((table, field))
    for (_, alias), (table, entry) in conditions.items():
        for field, values in entry["values"].items():
            if field.endswith("_concept_id"):
                concepts.setdefault((table, field), set()).update(values)
    return {"tree": tree, "sql": sql, "fields": sorted(used), "tables": sorted({t for t, _ in used}),
            "concepts": {k: sorted(v, key=lambda x: (len(x), x)) for k, v in sorted(concepts.items())},
            "conditions": list(conditions.values())}


# The conversion's steps.

class _Mapping:
    """One alias of the mapping table in a step: its vocabularies, the source column it looks up, and whether it gates rows."""

    def __init__(self):
        self.vocabularies = set()
        self.unresolved = False
        self.columns = []
        self.constant = False
        self.inner = False


class _Step:
    def __init__(self, index, entry, sql, catalogue, fields=None):
        self.index, self.file, self.table, self.layer = index, entry["file"], entry["table"].lower(), entry.get("layer", "core")
        self.sql = sql
        self.fields = fields or convert.cdm_fields()
        self.earlier = []           # the steps before this one, which write the OMOP tables it reads
        self.alternatives = []      # the steps that conversion.json offers in this one's place
        try:
            self.tree = sqlglot.parse_one(sql, dialect="tsql")
        except sqlglot.errors.SqlglotError:
            self.tree = None
        self.outputs = {}
        self.mappings = {}
        self.omop_aliases = {}
        if not isinstance(self.tree, exp.Select):
            return
        for projection in self.tree.expressions:
            self.outputs[projection.alias_or_name.lower()] = projection.unalias()
        for table in self.tree.find_all(exp.Table):
            if _is_omop(table):
                self.omop_aliases[table.alias_or_name.upper()] = table.name.lower()
        for select in self.tree.find_all(exp.Select):
            self._mappings(select, catalogue)

    def _mappings(self, select, catalogue):
        for node, join in _own_tables(select):
            if not (_is_omop(node) and node.name.lower() == MAPPING_TABLE):
                continue
            alias = node.alias_or_name.upper()
            entry = self.mappings.setdefault(alias, _Mapping())
            entry.inner = entry.inner or _side(join) == ""
            clauses = list(_conjuncts(join.args.get("on"))) if join is not None else []
            clauses += list(_conjuncts(select.args.get("where").this if select.args.get("where") else None))
            for clause in clauses:
                if isinstance(clause, exp.In):
                    column = clause.this
                    if isinstance(column, exp.Column) and column.table.upper() == alias and column.name.lower() == "source_vocabulary_id":
                        entry.vocabularies.update(_literal(e) for e in clause.expressions if _literal(e) is not None)
                    continue
                if not isinstance(clause, exp.EQ):
                    continue
                for one, other in ((clause.this, clause.expression), (clause.expression, clause.this)):
                    if not (isinstance(one, exp.Column) and one.table.upper() == alias):
                        continue
                    if one.name.lower() == "source_vocabulary_id":
                        if _literal(other) is not None:
                            entry.vocabularies.add(_literal(other))
                        elif isinstance(other, exp.Column):
                            found = _carried_literals(select, other)
                            if found:
                                entry.vocabularies.update(found)
                            else:
                                entry.unresolved = True
                        else:
                            entry.unresolved = True
                    elif one.name.lower() == "source_code":
                        carried = _passed_through(other)
                        if _literal(other) is not None or isinstance(carried, exp.Literal):
                            entry.constant = True
                        elif isinstance(carried, exp.Column) and carried.table:
                            origin = core_profile._source(select, carried)
                            if origin:
                                spelled = register._spelling(catalogue, origin)
                                if spelled not in entry.columns:
                                    entry.columns.append(spelled)

    def omop_reads(self, needed=None):
        """Each OMOP table that the step reads, other than the mapping table, with the conditions on its alias.

        needed, for a derived step, is the set of its output fields that the query reads. A table that
        such a step brings in with a left join, and that feeds none of those fields, is then left out,
        so that a query which does not read an anaesthetic's weight does not rest on the weight's steps.
        """
        found = []
        if self.tree is None:
            return found
        fields = self.fields
        skipped = self._unneeded(needed) if needed is not None and self.layer == "derived" else set()
        for select in self.tree.find_all(exp.Select):
            if id(select) in skipped:
                continue
            own = {node.alias_or_name.upper(): node.name.lower() for node, _ in _own_tables(select)
                   if _is_omop(node) and node.name.lower() != MAPPING_TABLE and node.name.lower() in fields
                   and (select is not self.tree or node.alias_or_name.upper() not in skipped)}
            known = lambda alias: {f for f, _, _ in fields[own[alias]]} if alias in own else None  # noqa: E731
            conditions = _conditions(select, known)
            for alias, table in own.items():
                found.append((table, conditions.get(alias, {"write": set(), "values": {}})))
        return found

    def _unneeded(self, needed):
        """The aliases of the top SELECT, and the ids of the SELECTs inside them, that no needed field reaches through a left join."""
        own = _own_tables(self.tree)
        keep = {node.alias_or_name.upper() for node, join in own if _side(join) != "LEFT"}
        for field in needed:
            expression = self.outputs.get(field)
            if expression is not None:
                keep |= {c.table.upper() for c in [expression, *expression.find_all(exp.Column)] if isinstance(c, exp.Column) and c.table}
        changed = True
        while changed:
            changed = False
            for node, join in own:
                if node.alias_or_name.upper() in keep and join is not None:
                    for column in join.find_all(exp.Column):
                        if column.table and column.table.upper() not in keep:
                            keep.add(column.table.upper())
                            changed = True
        skipped = set()
        for node, _ in own:
            if node.alias_or_name.upper() not in keep:
                skipped.add(node.alias_or_name.upper())
                skipped |= {id(select) for select in node.find_all(exp.Select)}
        return skipped

    def sources(self, select, column, depth=0):
        """The source columns behind a column of this step, through derived tables and earlier OMOP tables."""
        return _columns_behind(self, select, column, depth)


def _carried_literals(select, column):
    """The text literals that a derived table gives for one of its columns, across every branch of a union."""
    for subquery in select.find_all(exp.Subquery):
        if subquery.alias.upper() != column.table.upper():
            continue
        inner = subquery.this
        branches = list(_branches(inner))
        found = set()
        for branch in branches:
            for projection in branch.expressions:
                if projection.alias_or_name.upper() == column.name.upper():
                    value = _literal(projection.unalias())
                    if value is None:
                        return set()
                    found.add(value)
        return found
    return set()


def _branches(node):
    if isinstance(node, exp.Subquery):
        yield from _branches(node.this)
    elif isinstance(node, exp.SetOperation):
        yield from _branches(node.left)
        yield from _branches(node.right)
    elif isinstance(node, exp.Select):
        yield node


def _cte_body(select, table):
    """The body of the common table expression that a table without a schema names, or None."""
    if table.db or select is None:
        return None
    for cte in select.root().find_all(exp.CTE):
        if cte.alias.upper() == table.name.upper():
            return cte.this
    return None


def _columns_behind(step, select, column, depth=0):
    """The (table, column) pairs of the source behind one column, or ("omop", table, field) where it reads an OMOP table."""
    if depth > 8 or not column.table:
        return []
    for node, _ in _own_tables(select) if select is not None else []:
        if node.alias_or_name.upper() != column.table.upper():
            continue
        if isinstance(node, exp.Table):
            if _is_omop(node):
                return [("omop", node.name.lower(), column.name.lower())]
            body = _cte_body(select, node)
            if body is None:
                return [(node.name, column.name)]
        if isinstance(node, exp.Subquery) or isinstance(node, exp.Table):
            found = []
            for branch in _branches(node.this if isinstance(node, exp.Subquery) else body):
                for projection in branch.expressions:
                    if projection.alias_or_name.upper() == column.name.upper():
                        for inner in projection.unalias().find_all(exp.Column):
                            found += _columns_behind(step, branch, inner, depth + 1)
            return found
    outer = select.find_ancestor(exp.Select) if select is not None else None
    return _columns_behind(step, outer, column, depth + 1) if outer is not None else []


def _steps(folder, catalogue):
    folder = Path(folder)
    try:
        entries = json.loads((folder / "conversion.json").read_text())
    except (OSError, ValueError) as error:
        raise TargetError("the conversion's conversion.json could not be read") from error
    custom = custom_fields(folder)
    problems = convert.layer_problems(entries, set(custom))
    if problems:
        raise TargetError("; ".join(problems))
    fields = dict(convert.cdm_fields(), **custom)
    steps = [_Step(i, entry, decode((folder / entry["file"]).read_bytes()), catalogue, fields) for i, entry in enumerate(entries)]
    for step, entry in zip(steps, entries):
        step.earlier = steps[:step.index]
        for name in convert.alternatives(entry):
            path = folder / name
            if not path.is_file():
                raise TargetError(f"{name}: the alternative of {step.file} is not in the conversion folder")
            alternative = _Step(step.index, dict(entry, file=name), decode(path.read_bytes()), catalogue, fields)
            alternative.earlier = step.earlier
            step.alternatives.append(alternative)
    return steps


def _mapping_rows(folder):
    """The conversion's mapping rows: vocabulary -> [(source code, target concept)]."""
    rows = {}
    for row in convert.mapping_dicts(folder):
        rows.setdefault((row.get("source_vocabulary_id") or "").strip(), []).append(
            ((row.get("source_code") or "").strip(), _norm(row.get("target_concept_id") or "")))
    return rows


# What the author of the steps says each join, filter and vocabulary is meant to do.

INTENTS_FILE = "intents.json"
INTENT_KINDS = ("join", "filter", "vocabulary")
PLAIN_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_$#@]{0,127}")


def _intent_sentence(value, where):
    """One plain sentence, on one line, with nothing that sqlcmd or a page would read as anything but text."""
    if not isinstance(value, str) or not value.strip() or len(value) > 300:
        raise TargetError(f"{where}: an intent is one sentence of text, of at most 300 characters")
    if "\r" in value or "\n" in value or "$(" in value:
        raise TargetError(f"{where}: an intent may not hold a line break or $(")
    if value != value.strip() or not value[0].isupper() or not value.endswith(".") or "?" in value or "!" in value \
            or "  " in value:
        raise TargetError(f"{where}: an intent is a plain sentence that begins with a capital letter and ends with a full stop")
    return value


def read_intents(folder, steps=None):
    """What the author of the steps says each join, filter and vocabulary is meant to do, from the folder's intents.json.

    The file holds a list of entries, each with one key that names what it describes and the intent
    itself, one plain sentence:

        {"join": ["ANAES_RECORD.VISIT_KEY", "VISIT.VISIT_KEY"], "intent": "This join finds ..."}
        {"filter": "OBS_READING.ACCEPTED_FLAG", "intent": "This filter leaves out ..."}
        {"vocabulary": "SITE_OBS_SYSTOLIC", "intent": "These mapping rows ..."}

    A join is an unordered pair of columns, written TABLE.COLUMN. Each table and column must be named
    in the conversion's steps or their alternatives, and each vocabulary must be one that the steps or
    the mapping rows use, so that an intent cannot name anything that the conversion does not hold.
    Returns {"join": {pair: text}, "filter": {(TABLE, COLUMN): text}, "vocabulary": {NAME: text}}, with
    names in capitals, and empty dictionaries when the folder has no intents.json. Raises TargetError.
    """
    folder = Path(folder)
    found = {kind: {} for kind in INTENT_KINDS}
    path = folder / INTENTS_FILE
    if not path.exists():
        return found
    try:
        data = json.loads(decode(path.read_bytes()))
    except ValueError:
        raise TargetError(f"{INTENTS_FILE} cannot be read as JSON") from None
    if not isinstance(data, list):
        raise TargetError(f"{INTENTS_FILE} holds a list of entries")
    steps = _steps(folder, None) if steps is None else steps
    versions = [version for step in steps for version in [step, *step.alternatives] if version.tree is not None]
    tables = {t.name.upper() for v in versions for t in v.tree.find_all(exp.Table) if not _is_omop(t)}
    columns = {c.name.upper() for v in versions for c in v.tree.find_all(exp.Column)}
    vocabularies = {name.upper() for name in _mapping_rows(folder)} | {name.upper() for name in register._derived(folder)}
    vocabularies |= {name.upper() for v in versions for entry in v.mappings.values() for name in entry.vocabularies}

    def column(value, where):
        parts = value.split(".") if isinstance(value, str) else []
        if len(parts) != 2 or not all(PLAIN_NAME.fullmatch(part) for part in parts):
            raise TargetError(f"{where}: a column is written TABLE.COLUMN, with plain names")
        if parts[0].upper() not in tables or parts[1].upper() not in columns:
            raise TargetError(f"{where}: no step of the conversion names {value}")
        return parts[0].upper(), parts[1].upper()

    for position, entry in enumerate(data, start=1):
        where = f"{INTENTS_FILE}, entry {position}"
        kinds = [kind for kind in INTENT_KINDS if isinstance(entry, dict) and kind in entry]
        if not isinstance(entry, dict) or len(kinds) != 1 or set(entry) != {kinds[0], "intent"}:
            raise TargetError(f"{where}: an entry has the intent and exactly one of join, filter or vocabulary")
        kind, text = kinds[0], _intent_sentence(entry["intent"], where)
        if kind == "join":
            pair = entry["join"]
            if not isinstance(pair, list) or len(pair) != 2:
                raise TargetError(f"{where}: a join names two columns")
            key = frozenset(column(value, where) for value in pair)
            if len(key) != 2:
                raise TargetError(f"{where}: a join names two different columns")
        elif kind == "filter":
            key = column(entry["filter"], where)
        else:
            name = entry["vocabulary"]
            if not isinstance(name, str) or not PLAIN_NAME.fullmatch(name) or name.upper() not in vocabularies:
                raise TargetError(f"{where}: {name!r} is not a vocabulary that the conversion uses")
            key = name.upper()
        if key in found[kind]:
            raise TargetError(f"{where}: this {kind} already has an intent")
        found[kind][key] = text
    return found


def _intent(row, intents):
    """The intent that belongs to a row of the checklist, or an empty string."""
    if row["kind"] == "relationship" and row.get("_pair") is not None:
        return intents["join"].get(row["_pair"], "")
    if row["kind"] == "filter" and row.get("_column") is not None:
        return intents["filter"].get(row["_column"], "")
    if row["kind"] == "codes":
        named = " ".join(str(value) for value in (row.get("_names") or {}).values())
        words = set(re.findall(r"[A-Za-z0-9_$#@]+", named.upper()))
        return " ".join(text for name, text in sorted(intents["vocabulary"].items()) if name in words)
    return ""


# The trace.

class _Possible:
    """What a step can write into one field: the values that are certain, and why any others cannot be told."""

    def __init__(self, known=(), unknown=()):
        self.known, self.unknown = set(known), set(unknown)

    def __or__(self, other):
        return _Possible(self.known | other.known, self.unknown | other.unknown)


def _possible(step, node, mapping, derived, depth=0):
    if isinstance(node, (exp.Paren, exp.Cast, exp.TryCast)):
        return _possible(step, node.this, mapping, derived, depth)
    if isinstance(node, exp.Null):
        return _Possible()
    if _literal(node) is not None:
        return _Possible({_norm(_literal(node))})
    if isinstance(node, exp.Coalesce):
        found = _possible(step, node.this, mapping, derived, depth)
        for item in node.expressions:
            found = found | _possible(step, item, mapping, derived, depth)
        return found
    if isinstance(node, exp.Case):
        found = _Possible()
        for branch in node.args.get("ifs") or []:
            found = found | _possible(step, branch.args.get("true"), mapping, derived, depth)
        default = node.args.get("default")
        return found | _possible(step, default, mapping, derived, depth) if default is not None else found
    if isinstance(node, exp.Column):
        alias = node.table.upper()
        entry = step.mappings.get(alias)
        if entry is not None and node.name.lower() == "target_concept_id":
            known = {target for vocabulary in entry.vocabularies for _, target in mapping.get(vocabulary, [])}
            unknown = {"derived"} if entry.unresolved or any(v in derived for v in entry.vocabularies) else set()
            return _Possible(known, unknown)
        if alias in step.omop_aliases:
            # A field copied from an OMOP table can hold what the earlier steps that write that field can.
            writers = [s for s in step.earlier if s.table == step.omop_aliases[alias] and node.name.lower() in s.outputs
                       and s.tree is not None]
            if not writers or depth > 4:
                return _Possible(unknown={"omop"})
            found = _Possible()
            for writer in writers:
                found = found | _possible(writer, writer.outputs[node.name.lower()], mapping, derived, depth + 1)
            return found
        return _Possible(unknown={"source"})
    return _Possible(unknown={"source"})


def _evaluate(step, condition, mapping, derived):
    """Whether a step can write rows that meet a condition. Returns ("excluded" | "definite" | "maybe", detail)."""
    needed = condition["write"] | set(condition["values"])
    for field in needed:
        if field not in step.outputs or isinstance(step.outputs[field], exp.Null):
            return "excluded", {}
    detail = {}
    for field, values in condition["values"].items():
        found = _possible(step, step.outputs[field], mapping, derived)
        hit = values & found.known
        if hit:
            detail[field] = ("match", hit)
        elif found.unknown:
            detail[field] = ("maybe", found.unknown)
        else:
            return "excluded", {}
    return ("maybe" if any(kind == "maybe" for kind, _ in detail.values()) else "definite"), detail


def _writers(table, condition, candidates, mapping, derived):
    """The steps among the candidates that write rows of a table that meet a condition: (definite, possible)."""
    judged = [(step, *_evaluate(step, condition, mapping, derived)) for step in candidates if step.table == table and step.tree is not None]
    matched = {field for _, kind, detail in judged if kind != "excluded" for field, (how, _) in detail.items() if how == "match"}
    definite, possible = [], []
    for step, kind, detail in judged:
        if kind == "definite":
            definite.append(step)
        elif kind == "maybe":
            # Where another step writes the compared value itself, a step that fills the field from
            # the source is not the one that the reading step means.
            waved = [f for f, (how, why) in detail.items() if how == "maybe" and f in matched and "derived" not in why]
            if not waved:
                possible.append((step, sorted(f for f, (how, _) in detail.items() if how == "maybe"),
                                 sorted({w for how, why in detail.values() if how == "maybe" for w in why})))
    return definite, possible


def _origins(step, field, value, mapping, derived, depth=0):
    """The (table, field, step) that gives a value in a step's field: the step itself, or the earlier steps it copies from."""
    expression = step.outputs.get(field)
    if expression is None:
        return []
    node = expression
    while isinstance(node, (exp.Paren, exp.Cast, exp.TryCast)):
        node = node.this
    if (step.layer == "derived" and depth < 4 and isinstance(node, exp.Column) and node.table.upper() in step.omop_aliases):
        table = step.omop_aliases[node.table.upper()]
        found = []
        for writer in step.earlier:
            if writer.table == table and node.name.lower() in writer.outputs and writer.tree is not None \
                    and value in _possible(writer, writer.outputs[node.name.lower()], mapping, derived).known:
                found += _origins(writer, node.name.lower(), value, mapping, derived, depth + 1)
        if found:
            return found
    return [(step.table, field, step)]


def trace(target, folder, catalogue=None):
    """The steps that a target query depends on, directly and through other steps.

    target is the result of read_target. Returns {"steps": [step], "possible": [(step, fields, reasons)],
    "coverage": {(table, field): {concept: [step]}}}, with the steps in the conversion's order.
    """
    steps = _steps(folder, catalogue)
    mapping = _mapping_rows(folder)
    derived = set(register._derived(folder))
    relevant, possible, coverage, needed = {}, {}, {}, {}
    queue = []
    used = {}
    for table, field in target["fields"]:
        used.setdefault(table, set()).add(field)
    for table, condition in target["conditions"]:
        definite, maybe = _writers(table, condition, steps, mapping, derived)
        for step in definite:
            if step.index not in relevant:
                relevant[step.index] = step
                queue.append(step)
            if step.layer == "derived":
                needed[step.index] = needed.get(step.index, set()) | used.get(table, set())
            for field, values in condition["values"].items():
                for value in _possible(step, step.outputs[field], mapping, derived).known & values:
                    # A value that a derived step copies from an OMOP table is credited to the step that wrote it there.
                    for origin_table, origin_field, origin in _origins(step, field, value, mapping, derived):
                        coverage.setdefault((origin_table, origin_field), {}).setdefault(value, []).append(origin)
        for step, fields, reasons in maybe:
            possible.setdefault(step.index, (step, fields, reasons))
        for field, values in condition["values"].items():
            if field.endswith("_concept_id"):
                entry = coverage.setdefault((table, field), {})
                for value in values:
                    entry.setdefault(value, [])
    for key in [key for key, by_value in coverage.items() if not any(by_value.values())
                and any(s.layer == "derived" and s.table == key[0] for s in steps)]:
        del coverage[key]   # a custom table's concept is listed where the earlier step writes it
    while queue:
        step = queue.pop(0)
        earlier = [s for s in steps if s.index < step.index]
        for table, condition in step.omop_reads(needed.get(step.index) if step.layer == "derived" else None):
            definite, maybe = _writers(table, condition, earlier, mapping, derived)
            for found in definite:
                if found.index not in relevant:
                    relevant[found.index] = found
                    queue.append(found)
            for found, fields, reasons in maybe:
                possible.setdefault(found.index, (found, fields, reasons))
    ordered = [relevant[i] for i in sorted(relevant)]
    maybe = [possible[i] for i in sorted(possible) if i not in relevant]
    return {"folder": Path(folder), "steps": ordered, "possible": maybe, "coverage": coverage, "all": steps, "mapping": mapping, "derived": derived,
            "needed": needed}


# The checklist.

def _row(row_id, kind, blocking, wording, status, currently_from, in_hand, **names):
    entry = WORDING[wording] if wording in WORDING else register.WORDING[wording]
    fill = lambda text: text.format(**names)  # noqa: E731
    row = {"question_id": row_id, "stage": STAGE[kind], "question": fill(entry["question"]),
           "decides": fill(entry["decides"]), "currently_from": currently_from,
           "evidence_needed": fill(entry["evidence_needed"]), "who": entry["who"], "mechanism": entry["mechanism"],
           "status": status, "evidence_in_hand": " ".join(part for part in in_hand if part),
           "blocking": BLOCKING[0] if blocking else BLOCKING[1], "kind": kind}
    assert row["status"] in register.STATUSES and row["who"] in WHO and row["mechanism"] in MECHANISMS
    assert row["currently_from"] in SOURCES
    row["_wording"], row["_names"] = wording, names
    return row


def _of_them(count):
    ih = WORDING["in_hand"]
    return ih["of_them_none"] if count == 0 else ih["of_them_one"] if count == 1 else ih["of_them_some"].format(count=count)


def _counted(prefix, count, total):
    key = f"{prefix}_none" if count == 0 else f"{prefix}_one" if count == 1 else f"{prefix}_some"
    return WORDING["in_hand"][key].format(count=count, total=total)


class _Evidence:
    """The sample queries, read by the analyser, as sets of findings, with what the team's SQL showed in earlier runs."""

    def __init__(self, world, catalogue, held_back, saved=None):
        self.saved = saved if saved is not None else sql_evidence.Saved()
        self.requests = []
        self.unread = 0             # the requests that the analyser could not read in full
        for path in world.request_files():
            result = analyse_request(decode(path.read_bytes()), catalogue, held_back, world.dialect)
            self.requests.append(result.findings)
            self.unread += not_fully_read(result.unresolved)
        self.total = len(self.requests)
        self._graph = None

    def graph(self):
        """The joins that the sample queries make, as a graph of tables.

        Returns {TABLE: [(OTHER, (table, column), (other, column), requests)]}, with an edge each way for
        every pair of columns in two different tables that at least one request joins, and the number of
        requests that join it. The names are the findings' own, which come from the catalogue.
        """
        if self._graph is None:
            spelled, counted = {}, Counter()
            for findings in self.requests:
                pairs = set()
                for f in findings:
                    if f[0] == "join" and f[1].upper() != f[3].upper():
                        spelled.setdefault(_pair(f), ((f[1], f[2]), (f[3], f[4])))
                        pairs.add(_pair(f))
                counted.update(pairs)
            # A join that only an earlier run saw counts with the number of the team's queries that showed it then.
            for one, other, files in self.saved.joins():
                pair = frozenset({(one[0].upper(), one[1].upper()), (other[0].upper(), other[1].upper())})
                if pair not in counted:
                    spelled.setdefault(pair, (one, other))
                    counted[pair] = files
            self._graph = {}
            for pair, (one, other) in sorted(spelled.items(), key=lambda item: item[1]):
                self._graph.setdefault(one[0].upper(), []).append((other[0].upper(), one, other, counted[pair]))
                self._graph.setdefault(other[0].upper(), []).append((one[0].upper(), other, one, counted[pair]))
        return self._graph

    def route(self, start, end, bound=None):
        """The best route by which the sample queries get from one table to another, or None.

        A route is a list of hops, each (from column, to column, requests), with no table visited twice
        and at most bound hops. The shortest route is best; among routes of one length, the one whose
        weakest hop more requests make, then the one whose hops more requests make in all.
        """
        bound = ROUTE_BOUND if bound is None else bound
        graph, goal, best = self.graph(), end.upper(), []

        def walk(table, seen, hops):
            if table == goal and hops:
                key = (len(hops), -min(h[2] for h in hops), -sum(h[2] for h in hops), [(h[0], h[1]) for h in hops])
                if not best or key < best[0]:
                    best[:] = [key, list(hops)]
                return
            if len(hops) == bound or (best and len(hops) >= best[0][0]):
                return
            for other, one, two, count in graph.get(table, []):
                if other not in seen:
                    walk(other, seen | {other}, hops + [(one, two, count)])

        if start.upper() != goal:
            walk(start.upper(), {start.upper()}, [])
        return best[1] if best else None

    def count(self, test):
        return sum(1 for findings in self.requests if any(test(f) for f in findings))

    def tables(self, table):
        return self.count(lambda f: f[0] == "table" and f[1].upper() == table.upper())

    def columns(self, table, column):
        key = (table.upper(), column.upper())
        return self.count(lambda f: f[0] == "column" and (f[1].upper(), f[2].upper()) == key)

    def joins(self, pair):
        return self.count(lambda f: f[0] == "join" and _pair(f) == pair)

    def filters(self, table, column):
        key = (table.upper(), column.upper())
        return self.count(lambda f: f[0] == "filter" and (f[1].upper(), f[2].upper()) == key)

    def earlier(self, kind, *names):
        """(files, date) where an earlier run saw a finding, or None. A join is given as its pair."""
        if kind == "join":
            item = self.saved.items.get(("join", names[0]))
            return (item["files"], item["date"]) if item else None
        return self.saved.get(kind, *names)

    def gathered(self, catalogue, date):
        """The findings to save in sql_evidence.json: those of the request files to hand, with the earlier ones they do not show."""
        return sql_evidence.Saved.gathered(self.requests, catalogue, self.saved, date)


def _seen(earlier):
    """The sentence for a finding that the team's SQL showed in an earlier run."""
    files, date = earlier
    return WORDING["in_hand"]["seen_one" if files == 1 else "seen_some"].format(count=files, date=date)


ROUTE_BOUND = 3      # the most joins in a route between the two tables of an open join


def _pair(finding):
    """A join as an unordered pair of columns, so that either direction is the same join."""
    return frozenset({(finding[1].upper(), finding[2].upper()), (finding[3].upper(), finding[4].upper())})


def _listed(checks, table, column):
    if checks is None:
        return None
    key = (table.upper(), column.upper())
    return next((values for (t, c), values in checks.values.items() if (t.upper(), c.upper()) == key), None)


def _ran_empty(checks, table, column):
    """Whether the plain query that lists a column's values has run and found nothing."""
    return bool(checks is not None and checking.ran_empty(checking.Check("values", table, column), checks))


def _not_listed(missing, checks):
    """The sentences for columns whose values the check results do not list, apart from those whose query found nothing."""
    ih = WORDING["in_hand"]
    empty = [name for name in missing if _ran_empty(checks, *name.split(".", 1))]
    rest = [name for name in missing if name not in empty]
    return ([ih["codes_not_listed"].format(columns=_join(rest))] if rest else []) + \
        ([ih["codes_ran_empty"].format(columns=_join(empty))] if empty else [])


def _mapped_count(listed, codes, catalogue, table, column):
    """How many listed values have a code among the given ones. Only the count is returned."""
    entry = catalogue.table(table)
    numeric = checking.is_numeric(entry.column(column)) if entry and entry.column(column) else False
    known = {checking.normalise(code, numeric) for code in codes}
    return sum(1 for value, _, _ in listed if checking.normalise(value, numeric) in known)


def _source_rows(steps, analysis, evidence, checks, confirmed, versions=None):
    """The rows for tables, columns, joins and filters, from the analyser's reading of the relevant steps."""
    catalogue = analysis.catalogue
    rows = []
    text = convert.as_request([(step.table, step.sql) for step in steps])
    found = analyse_request(text, catalogue, analysis.held_back, analysis.dialect, confirmed).findings
    mapped = {(t.upper(), c.upper()) for step in steps if step.tree is not None for t, c in convert.mapped_columns(step.tree)}

    # Names that the steps use and the catalogue does not list. They are spelled as the step files spell them.
    missing_tables, missing_columns = {}, {}
    for step in steps:
        if step.tree is None:
            continue
        named = {cte.alias.upper() for cte in step.tree.find_all(exp.CTE)}
        aliases = {}
        for table in step.tree.find_all(exp.Table):
            if _is_omop(table) or table.name.upper() in named or not table.name:
                continue
            aliases[table.alias_or_name.upper()] = table.name
            if catalogue.table(table.name) is None:
                missing_tables.setdefault(table.name.upper(), table.name)
        for column in step.tree.find_all(exp.Column):
            name = aliases.get(column.table.upper()) if column.table else None
            entry = catalogue.table(name) if name else None
            if entry is not None and entry.column(column.name) is None:
                missing_columns.setdefault((entry.name.upper(), column.name.upper()), f"{entry.name}.{column.name}")

    tables = sorted({f[1] for f in found if f[0] == "table"})
    ih = WORDING["in_hand"]
    for table in tables:
        used = evidence.tables(table)
        earlier = None if used else evidence.earlier("table", table)
        rows.append(_row(f"table-{table}", "table", True, "table", "answered" if used or earlier else "partly",
                         "sample queries" if used or earlier else "catalogue export",
                         [ih["table_listed"].format(table=table),
                          _seen(earlier) if earlier else _counted("used", used, evidence.total)], table=table))
    for key, table in sorted(missing_tables.items()):
        rows.append(_row(f"table-{table}", "table", True, "table-absent", "open", "catalogue export",
                         [ih["table_missing"].format(table=table)], table=table))

    columns = sorted({(f[1], f[2]) for f in found if f[0] == "column"})
    for table, column in columns:
        used = evidence.columns(table, column)
        earlier = None if used else evidence.earlier("column", table, column)
        name = f"{table}.{column}"
        rows.append(_row(f"column-{name}", "column", True, "column", "answered" if used or earlier else "partly",
                         "sample queries" if used or earlier else "catalogue export",
                         [ih["column_listed"].format(column=name),
                          _seen(earlier) if earlier else _counted("used", used, evidence.total)], column=name))
    for key, name in sorted(missing_columns.items()):
        rows.append(_row(f"column-{name}", "column", True, "column-absent", "open", "catalogue export",
                         [ih["column_missing"].format(column=name)], column=name))

    # The joins, as unordered pairs. A join of a column to itself, which arises where a key passes
    # through an OMOP table and back to the table it came from, says nothing and is left out.
    joins = {}
    for f in found:
        if f[0] == "join" and (f[1].upper(), f[2].upper()) != (f[3].upper(), f[4].upper()):
            joins.setdefault(_pair(f), []).append(f)
    made = _made_joins(steps, catalogue, analysis.held_back, analysis.dialect)
    makers = {}
    for step in steps:
        for pair in made.get(step.file, {}):
            makers.setdefault(pair, []).append(step.file)
    weighed = _versions(steps, made, evidence, catalogue)
    if versions is not None:
        versions.update(weighed)
    for pair, items in sorted(joins.items(), key=lambda item: sorted(item[0])):
        first = items[0]
        left, right = f"{first[1]}.{first[2]}", f"{first[3]}.{first[4]}"
        inner = any(f[5] in ("inner", "where", "cross") for f in items)
        count = evidence.joins(pair)
        earlier = None if count else evidence.earlier("join", pair)
        in_hand = [_seen(earlier) if earlier else _counted("joined", count, evidence.total)]
        count = count or (earlier[0] if earlier else 0)
        if makers.get(pair):
            in_hand.append(ih["join_steps"].format(steps=_join(makers[pair])))
        if not inner:
            in_hand.append(ih["join_optional"])
        route = None
        if not count:
            route = evidence.route(first[1], first[3])
            in_hand += _route_sentences(route, first[1], first[3], evidence)
            for file in makers.get(pair, []):
                if file in weighed:
                    in_hand += _version_sentences(file, weighed[file])
        row = _row(f"relationship-{left}={right}", "relationship", inner, "relationship",
                   "answered" if count else "open", "sample queries" if count else "a guess", in_hand,
                   left=left, right=right)
        row["route"] = "; ".join(f"{a[0]}.{a[1]} = {b[0]}.{b[1]} ({n})" for a, b, n in route or [])
        row["_pair"], row["_route"], row["_makers"] = pair, route, makers.get(pair, [])
        rows.append(row)

    filters = {}
    for f in found:
        if f[0] == "filter" and (f[1].upper(), f[2].upper()) not in mapped:
            filters.setdefault((f[1], f[2]), []).append(f)
    for (table, column), items in sorted(filters.items()):
        name = f"{table}.{column}"
        count = evidence.filters(table, column)
        earlier = None if count else evidence.earlier("filter", table, column)
        fixed = [f for f in items if f[4] in ("string", "number")]
        in_hand = [_seen(earlier) if earlier else _counted("filtered", count, evidence.total)]
        count = count or (earlier[0] if earlier else 0)
        listed = _listed(checks, table, column)
        if not fixed:
            in_hand.append(ih["no_fixed_value"])
            settled = True
        elif checks is None:
            in_hand.append(ih["no_checks"])
            settled = False
        elif listed is None:
            in_hand.append(ih["ran_empty"] if _ran_empty(checks, table, column) else ih["not_listed"])
            settled = False
        else:
            in_hand.append(ih["listed"])
            compared = {(f[3], f[4]) for f in fixed}
            known = {(f[3], f[4]) for f in fixed if f[5]}
            in_hand.append(ih["literals_found"].format(found=len(known), compared=len(compared)))
            settled = len(known) == len(compared)
        status = "answered" if count and settled else "partly" if count or settled else "open"
        row = _row(f"filter-{name}", "filter", False, "filter", status,
                   "check results" if fixed and listed is not None else "sample queries" if count else "a guess",
                   in_hand, column=name)
        row["_column"] = (table.upper(), column.upper())
        # The column whose values a query would list, where the steps compare it with fixed values.
        row["_columns"] = [(table, column)] if fixed else []
        rows.append(row)
    return rows


def _route_rows(rows, conversion, traced):
    """An item for each step behind the query that gave way to an alternative, and the sentences that say which route and why.

    The choices are those that routes.apply recorded in the conversion folder. An alternative whose author
    says that it changes the answer is partly answered, with that sentence, for the clinical lead to accept.
    """
    ih = WORDING["in_hand"]
    files = {s.file for s in traced["all"]}
    said = []
    for choice in routing.chosen(conversion):
        if choice["chosen"] not in files:
            continue
        missing = _join(choice["missing"])
        sentence = ih["route_taken"].format(chosen=choice["chosen"], step=choice["step"], missing=missing)
        in_hand = [sentence] + [ih["route_passed"].format(alternative=t["file"], missing=_join(t["missing"]))
                                for t in choice.get("tried") or [] if t.get("missing")]
        effect = choice.get("effect") or ""
        if effect:
            in_hand.append(effect)
        row = _row(f"route-{choice['step']}", "meaning", False, "route-effect" if effect else "route",
                   "partly" if effect else "answered", "catalogue export", in_hand,
                   step=choice["step"], chosen=choice["chosen"], missing=missing)
        rows.append(row)
        said.append(" ".join([sentence, effect]).strip())
    return said


def _made_joins(steps, catalogue, held_back, dialect):
    """The joins that each step adds to those of the steps before it, and that each of its alternatives would add in its place.

    Returns {file: {pair: inner}}, where inner says whether the file makes the join as an inner join. A
    join of a column to itself is left out, as it is from the checklist.
    """
    def analyse(items):
        return analyse_request(convert.as_request(items), catalogue, held_back, dialect).findings if items else set()

    def joins(findings):
        found = {}
        for f in findings:
            if f[0] == "join" and (f[1].upper(), f[2].upper()) != (f[3].upper(), f[4].upper()):
                found[_pair(f)] = found.get(_pair(f), False) or f[5] in ("inner", "where", "cross")
        return found

    made, items = {}, []
    earlier = set()
    for step in steps:
        own = analyse(items + [(step.table, step.sql)])
        if step.tree is not None:
            made[step.file] = joins(own - earlier)
        for alternative in step.alternatives:
            if alternative.tree is not None:
                made[alternative.file] = joins(analyse(items + [(alternative.table, alternative.sql)]) - earlier)
        items.append((step.table, step.sql))
        earlier = own
    return made


def joins_made(folder, catalogue=None, held_back=frozenset(), dialect="tsql"):
    """Every join between source columns that a step of a conversion, or one of its alternatives, makes.

    Returns {pair: [(file, layer)]}, where a pair is a frozenset of two (TABLE, COLUMN) in capitals, as
    read_intents keys them, so that a test can confirm that every join has an intent.
    """
    if catalogue is None:
        raise TargetError("the joins of a conversion are read against a catalogue")
    steps = _steps(folder, catalogue)
    layers = {version.file: step.layer for step in steps for version in [step, *step.alternatives]}
    found = {}
    for file, joins in _made_joins(steps, catalogue, held_back, dialect).items():
        for pair in joins:
            found.setdefault(pair, []).append((file, layers[file]))
    return found


def _support(joins, evidence):
    """How far the sample queries support one version of a step: (joins they make, joins in all, inner joins they do not make)."""
    made = {pair: evidence.joins(pair) > 0 or evidence.earlier("join", pair) is not None for pair in joins}
    return sum(made.values()), len(made), sum(1 for pair, inner in joins.items() if inner and not made[pair])


def _versions(steps, made, evidence, catalogue=None):
    """For each step that offers alternatives, how far the sample queries support it and each alternative, and which is best.

    Returns {file: {"written": (made, total, open inner), "alternatives": [(file, (made, total, open inner))],
    "best": the best supported alternative's file, or None where the step as written is supported at least as well,
    "joins": {file: {pair: inner}}}}. The fewest inner joins that no sample query makes is best, and then
    the largest share of joins that the sample queries make.
    """
    rank = lambda support: (support[2], -(support[0] / support[1]) if support[1] else 0)  # noqa: E731
    found = {}
    for step in steps:
        if not step.alternatives or step.file not in made:
            continue
        written = _support(made[step.file], evidence)
        # An alternative that reads what the catalogue does not hold is no choice at this site, so it is not weighed.
        others = [(alt.file, _support(made[alt.file], evidence)) for alt in step.alternatives
                  if alt.file in made and not (catalogue is not None and routing.missing(alt.sql, catalogue))]
        if not others:
            continue
        better = sorted((rank(support), file) for file, support in others if rank(support) < rank(written))
        found[step.file] = {"written": written, "alternatives": others, "best": better[0][1] if better else None,
                            "joins": {file: made[file] for file in [step.file] + [f for f, _ in others]}}
    return found


def _version_sentences(file, version):
    """The sentences that weigh a step as written against its alternatives."""
    ih = WORDING["in_hand"]
    made, total, _ = version["written"]
    found = [ih["version_written"].format(supported=made, joins=_n(total, "join"), step=file)]
    for alternative, (made, total, _) in version["alternatives"]:
        found.append(ih["version_alternative"].format(supported=made, joins=_n(total, "join"), alternative=alternative, step=file))
    found.append(ih["version_better"].format(alternative=version["best"], step=file) if version["best"]
                 else ih["version_same"].format(step=file))
    return found


def _route_sentences(route, start, end, evidence):
    """The sentences that give the route by which the sample queries get between two tables, or say that there is none."""
    ih = WORDING["in_hand"]
    if route:
        hops = _join(ih["route_hop"].format(left=f"{a[0]}.{a[1]}", right=f"{b[0]}.{b[1]}", queries=_n(n, "query"))
                     for a, b, n in route)
        through = _join(dict.fromkeys(b[0] for _, b, _ in route[:-1]))
        if len(route) == 1:
            return [ih["route_direct"].format(start=start, end=end, hops=hops)]
        return [ih["route"].format(start=start, end=end, through=through, hops=hops)]
    return [ih["no_route"].format(start=start, end=end, bound=ROUTE_BOUND), _counted("unread", evidence.unread, evidence.total)]


def _codes_rows(target, traced, analysis, checks):
    catalogue = analysis.catalogue
    mapping, derived = traced["mapping"], traced["derived"]
    steps = traced["steps"]
    ih = WORDING["in_hand"]
    rows = []
    # The vocabularies that feed each field of a step that the query constrains to fixed values.
    constrained = {}
    for table, condition in target["conditions"]:
        for field, values in condition["values"].items():
            constrained.setdefault((table, field), set()).update(values)
    for key, by_concept in traced["coverage"].items():
        constrained.setdefault(key, set()).update(by_concept)
    feeding = {}
    for step in steps:
        for field, expression in step.outputs.items():
            for column in expression.find_all(exp.Column) if not isinstance(expression, exp.Column) else [expression]:
                entry = step.mappings.get(column.table.upper())
                if entry is not None and column.name.lower() == "target_concept_id":
                    feeding.setdefault((step.index, field), set()).update(entry.vocabularies)

    covered_vocabularies = set()
    for (table, field), by_concept in traced["coverage"].items():
        for concept, writers in sorted(by_concept.items()):
            name = f"{table}.{field}"
            vocabularies, columns, constant_steps, codes = set(), [], [], set()
            for step in writers:
                hit = False
                for vocabulary in feeding.get((step.index, field), ()):
                    leading = [code for code, target in mapping.get(vocabulary, []) if target == concept]
                    if leading:
                        hit = True
                        vocabularies.add(vocabulary)
                        codes.update(leading)
                        for entry in step.mappings.values():
                            if vocabulary in entry.vocabularies:
                                columns += [c for c in entry.columns if c not in columns]
                if not hit:
                    constant_steps.append(step.file)
            covered_vocabularies |= vocabularies
            rows_leading = sum(1 for v in vocabularies for _, target in mapping.get(v, []) if target == concept)
            if not writers:
                # The vocabularies that a step could use, had it a row for this concept.
                candidates = sorted({v for step in traced["all"] if step.table == table
                                     for v in _feeding(step, field)})
                in_hand = [ih["concept_no_rows"].format(field=name, vocabularies=_join(candidates)) if candidates
                           else ih["concept_no_vocabulary"].format(field=name)]
                rows.append(_row(f"codes-{name}-{concept}", "codes", True, "codes-concept", "open", "a guess", in_hand,
                                 concept=concept, concept_named=concepts.named(traced.get("folder"), concept), field=name,
                                 vocabularies=_join(candidates) or "a vocabulary of the site"))
                continue
            in_hand, status = [], "partly"
            if vocabularies:
                in_hand.append(ih["concept_rows"].format(rows=_n(rows_leading, "mapping row"), vocabularies=_join(sorted(vocabularies))))
            for file in constant_steps:
                in_hand.append(ih["concept_constant"].format(step=file))
            if constant_steps and not columns:
                status = "answered"
            elif columns:
                names = _join(f"{t}.{c}" for t, c in columns)
                if checks is None:
                    in_hand.append(ih["no_checks"])
                else:
                    listed_all, mapped_all, missing = 0, 0, []
                    for t, c in columns:
                        listed = _listed(checks, t, c)
                        if listed is None:
                            missing.append(f"{t}.{c}")
                            continue
                        listed_all += len(listed)
                        mapped_all += _mapped_count(listed, codes, catalogue, t, c)
                    if missing:
                        in_hand += _not_listed(missing, checks)
                    if len(missing) < len(columns):
                        in_hand.append(ih["codes_listed"].format(listed=_n(listed_all, "value"), columns=names, mapped=_of_them(mapped_all)))
                    if mapped_all:
                        status = "answered"
            else:
                status = "answered"
            row = _row(f"codes-{name}-{concept}", "codes", True, "codes-concept", status, "mapping rows", in_hand,
                       concept=concept, concept_named=concepts.named(traced.get("folder"), concept), field=name,
                       vocabularies=_join(sorted(vocabularies)) or "a vocabulary of the site")
            row["_columns"] = list(columns)
            rows.append(row)

    # The further vocabularies that the relevant steps look up. A vocabulary that feeds only a
    # constrained field, and leads to none of the values the query keeps, does not bear on it.
    vocabularies = {}
    for step in steps:
        for alias, entry in step.mappings.items():
            for vocabulary in entry.vocabularies:
                fields = {f for (i, f), names in feeding.items() if i == step.index and vocabulary in names}
                targets = {target for _, target in mapping.get(vocabulary, [])}
                if fields and all((step.table, f) in constrained and not (targets & constrained[(step.table, f)]) and vocabulary not in derived
                                  for f in fields):
                    continue
                if vocabulary in covered_vocabularies:
                    continue
                item = vocabularies.setdefault(vocabulary, {"columns": [], "constant": False, "inner": False, "steps": []})
                item["columns"] += [c for c in entry.columns if c not in item["columns"]]
                item["constant"] = item["constant"] or entry.constant
                item["inner"] = item["inner"] or (entry.inner and not fields)
                if step.file not in item["steps"]:
                    item["steps"].append(step.file)
    for vocabulary, item in sorted(vocabularies.items()):
        codes = [code for code, _ in mapping.get(vocabulary, [])]
        in_hand = [ih["vocabulary_rows"].format(rows=_n(len(codes), "mapping row"))]
        if item["inner"]:
            in_hand.append(ih["vocabulary_gate"])
        blocking = item["inner"] and not codes
        if not item["columns"]:
            rows.append(_row(f"codes-{vocabulary}", "codes", blocking, "codes-constant", "answered" if codes else "open",
                             "mapping rows" if codes else "a guess", in_hand, vocabulary=vocabulary))
            continue
        names = _join(f"{t}.{c}" for t, c in item["columns"])
        status = "partly" if codes else "open"
        if checks is None:
            in_hand.append(ih["no_checks"])
        else:
            listed_all, mapped_all, missing = 0, 0, []
            for t, c in item["columns"]:
                listed = _listed(checks, t, c)
                if listed is None:
                    missing.append(f"{t}.{c}")
                    continue
                listed_all += len(listed)
                mapped_all += _mapped_count(listed, codes, catalogue, t, c)
            if missing:
                in_hand += _not_listed(missing, checks)
            if len(missing) < len(item["columns"]):
                in_hand.append(ih["vocabulary_listed"].format(listed=_n(listed_all, "value"), columns=names, mapped=_of_them(mapped_all)))
            if codes and mapped_all and not missing:
                status = "answered"
        row = _row(f"codes-{vocabulary}", "codes", blocking, "codes-vocabulary", status,
                   "mapping rows" if codes else "a guess", in_hand, vocabulary=vocabulary, columns=names)
        row["_columns"] = list(item["columns"])
        rows.append(row)

    # The steps that the trace could not rule out.
    for step, fields, reasons in traced["possible"]:
        if "derived" in reasons:
            named = sorted({v for f in fields for v in _feeding(step, f)} & derived) or sorted(derived)
            rows.append(_row(f"step-{step.file}", "codes", False, "codes-possible", "open", "a guess",
                             [ih["possible_derived"].format(vocabularies=_join(named))], step=step.file, vocabularies=_join(named)))
        else:
            rows.append(_row(f"step-{step.file}", "meaning", False, "meaning-possible", "open", "a guess",
                             [ih["possible_source"].format(fields=_join(f"{step.table}.{f}" for f in fields))], step=step.file))
    return rows


def _feeding(step, field):
    expression = step.outputs.get(field)
    if expression is None:
        return set()
    found = set()
    for column in [expression] if isinstance(expression, exp.Column) else expression.find_all(exp.Column):
        entry = step.mappings.get(column.table.upper())
        if entry is not None and column.name.lower() == "target_concept_id":
            found |= entry.vocabularies
    return found


def _behind(traced, table, field, depth=0):
    """The source columns that the relevant steps fill one OMOP field from, as (table, column)."""
    found = []
    if depth > 4:
        return found
    for step in traced["steps"]:
        if step.table != table or field not in step.outputs:
            continue
        expression = step.outputs[field]
        for column in [expression] if isinstance(expression, exp.Column) else expression.find_all(exp.Column):
            for origin in _columns_behind(step, step.tree, column):
                if origin[0] == "omop":
                    found += [o for o in _behind(traced, origin[1], origin[2], depth + 1) if o not in found]
                elif origin not in found:
                    found.append(origin)
    return found


def _spelled(catalogue, origin):
    table = catalogue.table(origin[0])
    column = table.column(origin[1]) if table else None
    return (table.name, column.name) if column is not None else None


def _meaning_and_timing(target, traced, analysis, checks):
    catalogue = analysis.catalogue
    every = traced["all"][0].fields if traced["all"] else convert.cdm_fields()
    fields = {(t, f): kind for t, rows in every.items() for f, _, kind in rows}
    ih = WORDING["in_hand"]
    rows, groups = [], []
    # The source codes whose mapping rows lead to a concept that the query keeps, by source column.
    wanted = {}
    for (table, field), by_concept in traced["coverage"].items():
        for concept, writers in by_concept.items():
            for step in writers:
                for entry in step.mappings.values():
                    codes = [code for v in entry.vocabularies for code, target in traced["mapping"].get(v, []) if target == concept]
                    for t, c in entry.columns:
                        wanted.setdefault((t.upper(), c.upper()), set()).update(_norm(code) for code in codes)

    def relevant_role(role):
        if not role.when_column:
            return True
        codes = wanted.get((role.table.upper(), role.when_column.upper()))
        return codes is None or _norm(role.when_value) in codes

    for table, field in target["fields"]:
        if field.endswith("_id") or field.endswith("_source_value"):
            continue
        origins = [o for o in (_spelled(catalogue, x) for x in _behind(traced, table, field)) if o]
        if not origins:
            continue
        if fields.get((table, field)) in ("DATE", "TIMESTAMP"):
            for t, c in origins:
                for role in analysis.roles:
                    if (role.table, role.column) == (t, c):
                        groups += [g for g in ROLE_TUNING.get(role.role, ()) if g not in groups]
                    elif (role.table, role.at_column) == (t, c) and relevant_role(role):
                        group = "measurement_before" if role.role in BEFORE_ROLES else "anaesthetic_duration"
                        if group not in groups:
                            groups.append(group)
            continue
        names = _join(f"{t}.{c}" for t, c in origins)
        in_hand, given = [], 0
        for t, c in origins:
            roles = sorted({r.role for r in analysis.roles if (r.table, r.column) == (t, c) and relevant_role(r)})
            given += bool(roles)
            in_hand.append(ih["roles"].format(column=f"{t}.{c}", roles=_join(roles)) if roles
                           else ih["no_roles"].format(column=f"{t}.{c}"))
        status = "answered" if given == len(origins) else "partly" if given else "open"
        rows.append(_row(f"meaning-{table}.{field}", "meaning", False, "meaning", status,
                         "site rules" if given else "a guess", in_hand, field=f"{table}.{field}", columns=names))

    if groups:
        wanted_ids = {f"B-tuning-{g}" for g in groups}
        for row in register.tuning_rows(analysis, checks):
            if row["question_id"] in wanted_ids:
                rows.append(dict(row, blocking="no", kind="timing", _wording=None, _names={}))
    return rows


def _core_rows(target, traced, folder, profile_text):
    rows = []
    ih = WORDING["in_hand"]
    written = {s.table for s in traced["all"] if s.layer == "anaesthesia"}
    found = None
    if profile_text is not None:
        try:
            found = core_profile.read(profile_text, folder)
        except core_profile.ProfileError as error:
            raise TargetError(f"the core profile could not be read: {error}") from error

    # The core fields that the relevant anaesthesia steps join on, with whether any join is an inner one.
    joined = {}
    for step in traced["steps"]:
        if step.layer != "anaesthesia" or step.tree is None:
            continue
        for core_table, field, _, _, _ in core_profile.joined_pairs(step.tree):
            if core_table in written:
                continue
            inner = False
            for select in step.tree.find_all(exp.Select):
                for node, join in _own_tables(select):
                    if _is_omop(node) and node.name.lower() == core_table and _side(join) == "":
                        inner = True
            joined[f"{core_table}.{field}"] = joined.get(f"{core_table}.{field}", False) or inner
    if joined:
        try:
            register_rows = {row["question_id"]: row for row in register._stages_c_e(folder, profile_text)}
        except core_profile.ProfileError as error:
            raise TargetError(f"the core profile could not be read: {error}") from error
        for name, inner in sorted(joined.items()):
            row = register_rows.get(f"C-source-value-{name}")
            if row is not None:
                rows.append(dict(row, blocking="yes" if inner else "no", kind="core", _wording="C-source-value",
                                 _names={"field": name}))

    # The fields that the query reads from tables that only the core writes, itself or through a derived step.
    core_only = {s.table for s in traced["steps"] if s.layer == "core"} - written
    read = list(target["fields"])
    for step in traced["steps"]:
        if step.layer == "derived" and step.tree is not None:
            for column in step.tree.find_all(exp.Column):
                table = step.omop_aliases.get(column.table.upper()) if column.table else None
                if table in core_only and (table, column.name.lower()) not in read:
                    read.append((table, column.name.lower()))
    for table, field in read:
        if table not in core_only:
            continue
        name = f"{table}.{field}"
        if found is None:
            status, source, in_hand = "open", "a guess", [ih["no_profile"]]
        else:
            entry = found.get("tables", {}).get(table)
            absent = field in found.get("absent_fields", {}).get(table, [])
            if entry is None:
                status, source, in_hand = "open", "a guess", [ih["core_unknown"].format(table=table)]
            elif not entry.get("present"):
                status, source, in_hand = "open", "core profile", [ih["core_table_absent"].format(table=table)]
            elif absent:
                status, source, in_hand = "open", "core profile", [ih["core_field_absent"]]
            else:
                status, source, in_hand = "answered", "core profile", [ih["core_present"].format(table=table)]
        rows.append(_row(f"core-{name}", "core", True, "core", status, source, in_hand, field=name, table=table))
    return rows


def _planned(world, analysis, traced):
    """The checks that the requests and the whole conversion call for, keyed by (KIND, TABLE, COLUMN), as the boundary plans them."""
    rules_text = world.rules_path.read_text() if world.rules_path else None
    analysis.add_request(" conversion", convert.as_request([(s.table, s.sql) for s in traced["all"]],
                                                           register._definitions(rules_text)))
    found = {}
    for check in analysis.planned_checks(include_years=True, include_spans=True, include_fanout=True):
        found.setdefault((check.kind, check.table.upper(), check.column.upper(), check.later.upper()), check)
    return found


def _needed(row, planned, analysis):
    """The checks whose results would settle or help to settle an item, with the wording that says why.

    Returns (checks, reason, direct), where direct says how the item can be answered without a query, or "".
    """
    q = WORDING["query"]
    catalogue, people = analysis.catalogue, {name.upper() for name in analysis.rules.person_tables}
    wording, names = row.get("_wording"), row.get("_names") or {}
    if row["kind"] in ("filter", "codes") and row.get("_columns"):
        found = [planned.get(("values", t.upper(), c.upper(), "")) for t, c in row["_columns"]]
        found = [check for check in found if check is not None]
        columns = _join(f"{c.table}.{c.column}" for c in found)
        if row["kind"] == "filter":
            return found, q["filter"].format(columns=columns), ""
        return found, q[wording].format(columns=columns, vocabulary=names.get("vocabulary", "")), q["direct_codes"]
    if row["kind"] != "timing":
        return [], "", ""
    group = row["question_id"].removeprefix("B-tuning-")
    keys = _join(register.TUNING_GROUPS.get(group, ()))
    direct = q["direct_tuning"].format(keys=keys) if keys else ""
    if group in register.SPAN_GROUPS:
        first, second = register._pair(analysis.roles, *register.SPAN_GROUPS[group][:2])
        check = planned.get(("spans", first.table.upper(), first.column.upper(), second.column.upper())) if first else None
        if check is None:
            return [], "", ""
        return [check], q["spans"].format(table=check.table, first=f"{check.table}.{check.column}",
                                          second=f"{check.table}.{check.later}"), direct
    if group in register.RATE_ROLES:
        role = next((r for r in analysis.roles if r.role == register.RATE_ROLES[group]), None)
        entry = catalogue.table(role.table) if role else None
        column = entry.column(role.column) if entry else None
        if column is None or entry.name.upper() in people:
            return [], "", ""
        name = f"{entry.name}.{column.name}"
        if group == "still_in_place":
            if checking._kind(column) not in checking.DATE_TYPES:
                return [], "", ""
            check = planned.get(("years", entry.name.upper(), column.name.upper(), "")) or \
                checking.Check("years", entry.name, column.name)
            return [check], q["still_in_place"].format(column=name), direct
        if not checking._countable(column):
            return [], "", ""
        check = planned.get(("column", entry.name.upper(), column.name.upper(), "")) or \
            checking.Check("column", entry.name, column.name)
        return [check], q["death_share"].format(table=entry.name, column=name), direct
    return [], "", ""


# What an answer depends on.

def _step_dependencies(step, needed_fields):
    """What one step's rows and needed outputs depend on, by column lineage within the step.

    Returns {"aliases": counted aliases, "columns": [(alias, column)], "omop": {table: fields}, "left_unneeded":
    [the left-joined aliases that no counted expression reads], "joins": [(left (alias, column), right (alias, column))]}.
    Every WHERE, HAVING, GROUP BY and window, every inner join's condition and the step's needed outputs count;
    a left join counts only where a counted expression reads one of its columns, and its condition then counts.
    A column of a common table expression or a derived table counts the expression that its SELECT gives it.
    """
    tree = step.tree
    tables, derived, kinds = {}, {}, {}
    for select in tree.find_all(exp.Select):
        for node, join in _own_tables(select):
            alias = node.alias_or_name.upper() if node is not None else ""
            if isinstance(node, exp.Table):
                tables[alias] = node
            elif isinstance(node, exp.Subquery):
                derived[alias] = node.this
            kinds[alias] = (_side(join), join)
    for cte in tree.find_all(exp.CTE):
        derived[cte.alias.upper()] = cte.this
    work = []
    top = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    for projection in top.expressions:
        if projection.alias_or_name.lower() in needed_fields:
            work.append(projection)
    for node in tree.find_all(exp.Where, exp.Having, exp.Group, exp.Window, exp.Qualify):
        work.append(node)
    for alias, (side, join) in kinds.items():
        if join is not None and side not in ("LEFT", "RIGHT", "FULL") and join.args.get("on") is not None:
            work.append(join.args["on"])
    counted, columns, seen, joins = set(), [], set(), []
    while work:
        expression = work.pop()
        if id(expression) in seen:
            continue
        seen.add(id(expression))
        if isinstance(expression, (exp.EQ,)) and all(isinstance(side, exp.Column) for side in (expression.this, expression.expression)):
            joins.append(((expression.this.table.upper(), expression.this.name), (expression.expression.table.upper(), expression.expression.name)))
        for column in [expression] if isinstance(expression, exp.Column) else list(expression.find_all(exp.Column)):
            alias = column.table.upper()
            names = [alias] if alias else [a for a, select in derived.items()
                                          if any(p.alias_or_name.upper() == column.name.upper() for p in select.expressions)]
            for name in names:
                if name in derived:
                    select = derived[name]
                    select = select if isinstance(select, exp.Select) else select.find(exp.Select)
                    for projection in select.expressions if select is not None else []:
                        if projection.alias_or_name.upper() == column.name.upper() or isinstance(projection, exp.Star):
                            work.append(projection)
                    for node in (select.find_all(exp.Where, exp.Having, exp.Group, exp.Window) if select is not None else []):
                        work.append(node)
                if name in tables:
                    columns.append((name, column.name))
                if name in kinds and name not in counted:
                    counted.add(name)
                    side, join = kinds[name]
                    if join is not None and join.args.get("on") is not None:
                        work.append(join.args["on"])
        for sub in expression.find_all(exp.Select):
            if sub is not expression:
                for node in sub.find_all(exp.Where):
                    work.append(node)
    omop = {}
    for alias, column in columns:
        node = tables.get(alias)
        if node is not None and _is_omop(node):
            omop.setdefault(node.name.lower(), set()).add(column.lower())
    left = [alias for alias, (side, join) in kinds.items() if side == "LEFT" and alias not in counted]
    return {"aliases": counted, "columns": columns, "omop": omop, "left_unneeded": left, "joins": joins, "tables": tables}


def answer_dependencies(conversion, target_sql, catalogue=None, traced=None):
    """What the ANSWER of a target query depends on, followed back through the steps by column lineage.

    Returns {"tables", "columns" (as "TABLE.COLUMN"), "joins" (frozensets of two "TABLE.COLUMN"), "filters" (as
    "TABLE.COLUMN"), "vocabularies", "steps" (files), "not_needed" (each left join that no part of the answer
    reads, as (step file, table), which is not needed for the answer provided that it does not multiply rows)}.
    Every name of a source table or column is spelled as the catalogue spells it where the catalogue holds it.
    """
    folder = Path(conversion)
    target = read_target(target_sql, custom_fields(folder))
    traced = traced or trace(target, folder, catalogue)
    needed = {}
    for table, field in target["fields"]:
        needed.setdefault(table, set()).add(field)
    for table, condition in target["conditions"]:
        needed.setdefault(table, set()).update(condition.get("values", {}).keys())
    found = {"tables": set(), "columns": set(), "joins": set(), "filters": set(), "vocabularies": set(),
             "steps": [], "not_needed": []}

    def spelled(table, column=None):
        entry = catalogue.table(table) if catalogue is not None else None
        name = entry.name if entry is not None else table
        if column is None:
            return name
        field = entry.column(column) if entry is not None else None
        return f"{name}.{field.name if field is not None else column}"

    for step in sorted(traced["steps"], key=lambda s: -s.index):
        fields = needed.get(step.table, set())
        if step.tree is None or not fields:
            continue
        deps = _step_dependencies(step, fields)
        found["steps"].append(step.file)
        for table, more in deps["omop"].items():
            needed.setdefault(table, set()).update(more)
        for alias in deps["aliases"]:
            node = deps["tables"].get(alias)
            if alias in step.mappings:
                found["vocabularies"] |= set(step.mappings[alias].vocabularies)
            elif node is not None and not _is_omop(node) and node.name.lower() != MAPPING_TABLE:
                found["tables"].add(spelled(node.name))
        sources = {alias: node.name for alias, node in deps["tables"].items() if not _is_omop(node)
                   and alias not in step.mappings}
        for alias, column in deps["columns"]:
            if alias in sources:
                found["columns"].add(spelled(sources[alias], column))
        for (a, x), (b, y) in deps["joins"]:
            if a in sources and b in sources and a != b:
                found["joins"].add(frozenset({spelled(sources[a], x), spelled(sources[b], y)}))
        for node in step.tree.find_all(exp.Where):
            for column in node.find_all(exp.Column):
                if column.table.upper() in sources:
                    found["filters"].add(spelled(sources[column.table.upper()], column.name))
        for alias in deps["left_unneeded"]:
            node = deps["tables"].get(alias)
            if node is not None:
                found["not_needed"].append((step.file, "source_to_concept_map" if alias in step.mappings
                                            else spelled(node.name) if not _is_omop(node) else f"omop.{node.name}"))
    found["steps"].reverse()
    return found


# Facts that a person confirmed, and the questions that a person can answer.

def _filter_values(steps, column):
    """The fixed values that the steps compare a source column with, and whether they keep or leave out those rows."""
    table, _, name = column.upper().partition(".")
    kept, left_out = [], []
    for step in steps:
        if step.tree is None:
            continue
        aliases = {t.alias_or_name.upper(): t.name.upper() for t in step.tree.find_all(exp.Table)}
        for node in step.tree.find_all(exp.EQ, exp.NEQ, exp.In):
            found = [node.this] if isinstance(node.this, exp.Column) else list(node.this.find_all(exp.Column))
            this = found[0] if len(found) == 1 else None
            if this is None or this.name.upper() != name or aliases.get(this.table.upper()) != table:
                continue
            values = [node.expression] if not isinstance(node, exp.In) else list(node.expressions)
            literals = [v.this for v in values if isinstance(v, exp.Literal)]
            negated = isinstance(node, exp.NEQ) or isinstance(node.parent, exp.Not)
            (left_out if negated else kept).extend(v for v in literals if v not in kept + left_out)
    return kept, left_out


def _apply_facts(rows, confirmed, traced):
    """Settles the items that a person's confirmed facts answer, and says plainly what a "no" means."""
    q = WORDING["facts"]
    for row in rows:
        names = row.get("_names") or {}
        fact, sentence = None, None
        if row["kind"] == "relationship":
            fact = confirmed.join(names.get("left", ""), names.get("right", ""))
            if fact is not None:
                steps = _join(row.get("_makers") or []) or WORDING["nouns"]["step"][1]
                if fact["answer"] == "yes":
                    sentence = q["join_yes"].format(date=fact["date"])
                elif fact.get("instead"):
                    sentence = q["join_no_instead"].format(date=fact["date"], steps=steps, left=fact["instead"][0], right=fact["instead"][1])
                else:
                    sentence = q["join_no"].format(date=fact["date"], steps=steps)
        elif row["kind"] == "filter":
            fact = confirmed.filter(names.get("column", ""))
            if fact is not None:
                sentence = q["filter_yes" if fact["answer"] == "yes" else "filter_no"].format(date=fact["date"])
        elif row["kind"] == "codes" and row.get("_wording") == "codes-concept":
            vocabularies = [v.strip() for v in str(names.get("vocabularies", "")).replace(" and ", ",").split(",") if v.strip()]
            found = [f for v in vocabularies for f in confirmed.codes(v, names.get("concept"))]
            if found:
                fact = dict(found[-1], answer="yes")
                sentence = q["codes_yes"].format(date=fact["date"], count=_n(sum(len(f["codes"]) for f in found), "code"))
        elif row["kind"] == "codes" and names.get("vocabulary"):
            found = confirmed.codes(names["vocabulary"])
            if found:
                fact = dict(found[-1], answer="yes")
                sentence = q["codes_yes"].format(date=fact["date"], count=_n(sum(len(f["codes"]) for f in found), "code"))
        if fact is None:
            continue
        row["evidence_in_hand"] = " ".join(part for part in (row["evidence_in_hand"], sentence) if part)
        if fact["answer"] == "yes":
            row["status"], row["currently_from"] = "answered", "a person"
        else:
            row["status"] = "open"
        row["_fact"] = fact["answer"]


def _questions(rows, traced, catalogue):
    """The questions that a colleague can answer from knowledge, one for each open item of the first phase of
    a kind that a person can settle, as row["_ask"], and the whole list as text to send."""
    q = WORDING["facts"]
    asked = []
    for row in rows:
        if row.get("phase") != "source" or row["status"] == "answered":
            continue
        names = row.get("_names") or {}
        ask = None
        if row["kind"] == "relationship":
            left, right = names.get("left", ""), names.get("right", "")
            tables = {}
            for name in (left, right):
                entry = catalogue.table(name.split(".")[0])
                if entry is not None:
                    tables[entry.name] = sorted(c.name for c in entry.columns.values())
            ask = {"kind": "join", "left": left, "right": right, "tables": tables,
                   "text": q["ask_join"].format(left=left, right=right, left_table=left.split(".")[0], right_table=right.split(".")[0])}
        elif row["kind"] == "filter":
            column = names.get("column", "")
            kept, left_out = _filter_values(traced["steps"], column)
            if kept or left_out:
                text = (q["ask_filter_keep"].format(column=column, values=_join(_quote(v) for v in kept)) if kept else
                        q["ask_filter_leave"].format(column=column, values=_join(_quote(v) for v in left_out)))
                ask = {"kind": "filter", "column": column, "text": text}
        elif row["kind"] == "codes" and row.get("_wording") == "codes-concept":
            columns = [f"{t}.{c}" for t, c in row.get("_columns") or []]
            vocabularies = [v.strip() for v in str(names.get("vocabularies", "")).replace(" and ", ",").split(",") if v.strip()]
            if columns and vocabularies and vocabularies[0] != "a vocabulary of the site":
                ask = {"kind": "codes", "vocabulary": vocabularies[0], "concept": str(names.get("concept", "")),
                       "column": columns[0], "text": q["ask_codes"].format(columns=_join(columns),
                                                                           concept=concepts.named(traced.get("folder"), names.get("concept", "")),
                                                                           vocabulary=vocabularies[0])}
        if ask is not None:
            row["_ask"] = ask
            asked.append(ask["text"])
    if not asked:
        return ""
    lines = [q["questions_head"].format(name=traced.get("name") or WORDING["facts"]["this_question"]), ""]
    lines += [f"{i}. {text}" for i, text in enumerate(asked, start=1)]
    lines += ["", q["questions_foot"]]
    return "\n".join(lines) + "\n"


def _stages(rows, dependencies):
    """Says, for each item, which phase needs it, from what the answer depends on.

    An item belongs to the first phase, answering from the source database, when the answer depends on its
    table, column, join, filter, vocabulary or step; it is "unneeded" when the answer does not, so that it
    does not block that phase, although the OMOP release still needs it. The core, the source keys that the
    core holds, and the meaning and timing that make the shadow database realistic belong to the release.
    """
    up = lambda text: str(text).upper()  # noqa: E731
    columns = {up(c) for c in dependencies["columns"]}
    tables = {up(t) for t in dependencies["tables"]}
    vocabularies = {up(v) for v in dependencies["vocabularies"]}
    joins = {frozenset(up(c) for c in pair) for pair in dependencies["joins"]}
    for row in rows:
        names = row.get("_names") or {}
        kind = row["kind"]
        if row["question_id"].startswith("route-"):
            row["phase"] = "source" if names.get("chosen") in dependencies["steps"] else "unneeded"
            continue
        if kind in ("core", "meaning", "timing") and not row["question_id"].startswith("step-"):
            row["phase"] = "release"
            continue
        if kind == "table":
            needed = up(names.get("table", "")) in tables
        elif kind == "column":
            needed = up(names.get("column", "")) in columns
        elif kind == "relationship":
            pair = frozenset({up(names.get("left", "")), up(names.get("right", ""))})
            needed = pair in joins or pair <= columns
        elif kind == "filter":
            needed = up(names.get("column", "")) in columns
        elif row.get("_wording") == "codes-concept":
            needed = True       # the target compares with the concept itself
        elif kind == "codes" and names.get("vocabulary"):
            needed = up(names["vocabulary"]) in vocabularies
        elif row["question_id"].startswith("step-"):
            needed = names.get("step") in dependencies["steps"]
        else:
            needed = True
        row["phase"] = "source" if needed else "unneeded"


def draft_facts(draft, catalogue):
    """What the page says about the source draft: the source tables it reads, and whether it returns only counts.

    It returns only counts when every output column of its last SELECT is an aggregate or one of the
    expressions that it groups by; otherwise it returns a row for each record.
    """
    tables = sorted({t.name for t in sqlglot.parse_one(draft, dialect="tsql").find_all(exp.Table)
                     if catalogue.table(t.name) is not None}, key=str.upper)
    tables = [catalogue.table(t).name for t in dict.fromkeys(tables)]
    tree = sqlglot.parse_one(draft, dialect="tsql")
    final = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    # A draft that is safe by default reads its answer from the expression named result, and only blanks it.
    result = next((cte.this for cte in tree.find_all(exp.CTE) if cte.alias.lower() == "result"), None)
    if result is not None:
        final = result if isinstance(result, exp.Select) else result.find(exp.Select)
    group = final.args.get("group")
    grouped = {g.sql(dialect="tsql") for g in group.expressions} if group else set()
    counts_only = True
    for projection in final.expressions:
        if projection.alias_or_name == "result_order":
            continue    # the column that keeps the order of a result that is safe by default
        value = projection.unalias()
        if value.sql(dialect="tsql") in grouped or (isinstance(value, exp.Column) and value.name in
                                                     {g.alias_or_name for g in (group.expressions if group else [])}):
            continue
        if isinstance(value, exp.Literal):
            continue
        if not value.find(exp.AggFunc) or value.find(exp.Window):
            counts_only = False
            break
    return {"tables": tables, "counts_only": counts_only}


def stage_counts(rows, stage=None):
    """The blocking items of one stage by status, or of every item where stage is None, as counts does."""
    return counts([row for row in rows if stage is None or row.get("phase") == stage])


def _home(table, column, analysis, checks):
    """The (table, column) that a source key refers to, with the kind of the key, "number" or "text".

    It is the other side of a join that the requests or the conversion make from the source key, where that
    column keys its own table's rows, by the catalogue, or else is unique by the check results; a key that
    is the first column of its table is preferred. A table of people is never chosen. Where there is none,
    the source column itself is used.
    """
    catalogue = analysis.catalogue
    people = {name.upper() for name in analysis.rules.person_tables}
    unique = {(t.upper(), c.upper()): v["unique"] for (t, c), v in checks.columns.items()} if checks is not None else {}
    findings = set().union(*(r.findings for r in analysis._requests.values())) if analysis._requests else set()
    candidates = []
    for f in findings:
        if f[0] != "join":
            continue
        for one, other in ((f[1:3], f[3:5]), (f[3:5], f[1:3])):
            if (one[0].upper(), one[1].upper()) != (table.upper(), column.upper()) or other[0].upper() in (table.upper(),) \
                    or other[0].upper() in people:
                continue
            row_key = checking._row_key(catalogue, *other)
            if row_key or unique.get((other[0].upper(), other[1].upper())) is True:
                candidates.append((not row_key, other[0], other[1]))
    home = tuple(sorted(candidates)[0][1:]) if candidates else (table, column)
    entry = catalogue.table(home[0])
    found = entry.column(home[1]) if entry is not None else None
    kind = checking._kind(found) if found is not None else ""
    whole = kind in checking.WHOLE_NUMBER_TYPES or (kind in checking.SCALED_NUMBER_TYPES and found.scale == 0)
    return home, ("number" if whole else "text" if kind in checking.TEXT_TYPES else None)


def _profile_queries(rows, traced, conversion, profile_text, checks, analysis=None):
    """Gives each open core item the plain queries of the core profile that would settle it.

    Each such row gains query_state, query_reason, query and _queries, as the items with checks do. Returns
    (the profile's queries, each once, as {"id", "sql", "state", "tier"}, tier one first; the source tables
    whose size a match query waits for, which the checklist's table sizes query then also asks for).
    """
    q = WORDING["query"]
    items = [row for row in rows if row["kind"] == "core" and row["status"] != "answered"]
    if not items:
        return [], []
    try:
        facts = core_profile.conversion_facts(conversion)
        settings = core_profile.plain_settings(conversion)
    except (core_profile.ProfileError, OSError, ValueError, KeyError):
        return [], []
    found = core_profile.read(profile_text, conversion) if profile_text is not None else None
    tables = (found or {}).get("tables", {})
    general = found is not None and found["format"] == "general"
    source_sizes = dict(checks.rows) if checks is not None else {}
    limit = f"{checking.PLAIN_EXACT_ROWS:,}"
    wanted_tables, wanted_fields, offered, waiting = [], [], {}, []
    plans = []
    for row in items:
        name = (row.get("_names") or {}).get("field", "")
        table, _, field = name.partition(".")
        if not field:
            continue
        sentences, ids, states = [], [], set()
        if table not in tables:
            wanted_tables.append(table)
            wanted_fields.append((table, field))
            ids.append("profile:tier-one")
            sentences.append(q["profile_core"].format(field=name))
        pairs = [(pair, entry) for pair, entry in facts["pairs"].items() if f"{pair[0]}.{pair[1]}" == name]
        if pairs and not general:
            sentences.append(q["profile_match"].format(sample=f"{core_profile.PLAIN_KEY_SAMPLE:,}", field=name))
            measured = {(m["core"], m["source"]): m for m in (found or {}).get("matches", [])}
            empty, unsized, core_side = [], [], []
            for pair, entry in pairs:
                done = measured.get((name, f"{pair[2]}.{pair[3]}"))
                if done is not None:
                    if not done["keys"]:
                        empty.append(f"{pair[2]}.{pair[3]}")
                    continue
                home, home_kind = _home(pair[2], pair[3], analysis, checks) if analysis is not None else (None, None)
                offer = core_profile.plain_match_offer(pair, entry["cast"], settings, tables, source_sizes, home, home_kind)
                if offer["state"] == "core-side":
                    core_side.append(f"{pair[2]}.{pair[3]}")
                states.add(offer["state"])
                if offer["sql"]:
                    ids.append(offer["id"])
                    offered.setdefault(offer["id"], {"id": offer["id"], "sql": offer["sql"], "state": offer["state"], "tier": 2})
                if offer["state"] == "sizes" and table not in wanted_tables:
                    wanted_tables.append(table)
                if offer["state"] == "source-sizes":
                    waiting.append(pair[2])
                    unsized.append(pair[2])
            if unsized:
                key = "profile_source_sizes" if len(unsized) == 1 else "profile_source_sizes_many"
                sentences.append(q[key].format(tables=_join(unsized)))
            if "sizes" in states:
                sentences.append(q["profile_tier_one"])
            if "setting" in states:
                sentences.append(q["profile_setting"])
            if "absent" in states:
                sentences.append(q["profile_absent"].format(table=table))
            if "count" in states:
                sentences.append(q["profile_count"].format(table=table))
            if core_side:
                sentences.append(q["profile_core_side"].format(table=table, limit=limit, joins=_join(core_side),
                                                               sample=f"{core_profile.PLAIN_KEY_SAMPLE:,}"))
            if "large" in states:
                sentences.append(q["profile_large"].format(table=table, limit=limit))
            if empty:
                sentences.append(q["profile_ran_empty"].format(sources=_join(empty)))
        if table not in tables and pairs and not general and (table, field) not in wanted_fields:
            wanted_fields.append((table, field))
        plans.append((row, sentences, list(dict.fromkeys(ids)), states, bool(empty)))
    queries = []
    if wanted_tables:
        sql = core_profile.plain_tier_one(wanted_tables, wanted_fields, settings)
        queries.append({"id": "profile:tier-one", "sql": sql, "state": "ready", "tier": 1})
    queries += list(offered.values())
    texts = {query["id"]: query["sql"] for query in queries}
    for row, sentences, ids, states, empty in plans:
        ids = [key for key in ids if key in texts]
        if not ids and not sentences:
            continue
        row["_queries"] = ids
        row["query_reason"] = " ".join(sentences)
        row["query"] = "\n\n".join(texts[key] for key in ids)
        row["query_state"] = ("ready" if ids else "waiting" if states & {"sizes", "source-sizes", "setting"}
                              else "large" if "large" in states else "ran" if empty else "")
    return queries, list(dict.fromkeys(waiting))


def _queries(rows, world, analysis, traced, checks, extra_sizes=(), planned=None):
    """Gives each open item that check results would settle or help to settle the plain queries that answer it.

    Each such row gains query_state, query_reason and query, and the private _queries, the identifiers of
    its queries. Returns {"sizes": the table sizes query and the tables it reads, or None, "queries": each
    distinct query once, as {"id", "sql", "state", "table"}, in the order in which the checklist first needs it}.
    """
    q = WORDING["query"]
    planned = planned if planned is not None else _planned(world, analysis, traced)
    catalogue = analysis.catalogue
    limit = f"{checking.PLAIN_EXACT_ROWS:,}"
    listed, unsized = {}, []
    for row in rows:
        if row["kind"] == "core":
            continue    # the core profile's queries, which _profile_queries gives
        row.update({"query_state": "", "query_reason": "", "query": "", "_queries": []})
        if row["status"] == "answered":
            continue
        wanted, reason, direct = _needed(row, planned, analysis)
        needed = [check for check in wanted if not checking.held(check, checks)]
        if not needed:
            # A query that has run and found nothing is recorded as asked, and is not offered again.
            if any(checking.ran_empty(check, checks) for check in wanted):
                row["query_state"] = "ran"
                row["query_reason"] = " ".join(part for part in (q["ran"], direct) if part)
            continue
        states, offered = {}, []
        for check in needed:
            state, queries = checking.offer(check, catalogue, checks)
            states.setdefault(state, []).extend(t for t in check.tables() if t not in states.get(state, []))
            for query_check, sql in queries:
                offered.append(query_check.key())
                listed.setdefault(query_check.key(), {"id": query_check.key(), "sql": sql, "state": state,
                                                      "table": query_check.table})
            if state == "sizes":
                unsized += [t for t in check.tables() if checking.size_of(t, checks) is None
                            and not checking.unrecorded(t, checks) and t not in unsized]
        sentences = [reason]
        if "sampled" in states:
            sentences.append(q["sampled"].format(tables=_join(states["sampled"][:1]), limit=limit))
        if "count" in states:
            counted = [t for t in states["count"] if checking.size_of(t, checks) is None]
            sentences.append(q["count"].format(tables=_join(counted), limit=limit))
        if "unsampled" in states:
            sentences.append(q["unsampled"].format(tables=_join(states["unsampled"][:1]), limit=limit))
        if "sizes" in states:
            waiting = [t for t in states["sizes"] if checking.size_of(t, checks) is None]
            sentences.append(q["waiting"].format(tables=_join(waiting)))
        if "large" in states:
            sentences.append(q["large"].format(tables=_join(states["large"][:1]), limit=limit,
                                               script_limit=f"{checking.LARGE_TABLE_ROWS:,}"))
        if direct:
            sentences.append(direct)
        row["_queries"] = list(dict.fromkeys(offered))
        row["query_state"] = "ready" if offered else "waiting" if "sizes" in states else "large"
        row["query_reason"] = " ".join(sentence for sentence in sentences if sentence)
        row["query"] = "\n\n".join(listed[key]["sql"] for key in row["_queries"])
    sizes = None
    unsized += [t for t in extra_sizes if analysis.catalogue.table(t) is not None
                and checking.size_of(t, checks) is None and not checking.unrecorded(t, checks)
                and not checking.too_large(t, checks) and t not in unsized]
    unsized = [analysis.catalogue.table(t).name for t in unsized]
    if unsized:
        sizes = {"id": "sizes", "sql": checking.size_query(catalogue, unsized), "reason": q["sizes"], "tables": unsized}
    return {"sizes": sizes, "queries": list(listed.values())}


def queries_file(name, offered):
    """The queries that a checklist offers now, as one T-SQL file: the table sizes query first, then the other
    queries for the analytics team, and then the core profile's queries for the central OMOP team."""
    lines = checking._comment([WORDING["query"]["file"].format(name=name)])
    parts = [offered["sizes"]["sql"]] if offered["sizes"] else []
    parts += [query["sql"] for query in offered["queries"]]
    text = "\n".join(lines) + "\n\n" + "\n\n".join(parts) + ("\n" if parts else "")
    profile = [query["sql"] for query in offered.get("profile") or []]
    if profile:
        text += "\n" + "\n".join(checking._comment([WORDING["query"]["profile_file"]])) + "\n\n" + "\n\n".join(profile) + "\n"
    return text


def checklist(world, conversion, target_sql, checks_csv=None, profile_text=None, facts_text=None, name=None, evidence_text=None):
    """The checklist for one target query, as (rows, trace). Each row is a dictionary in LAYOUT, with private keys that begin with _.

    evidence_text is sql_evidence.json, what the team's SQL showed in earlier runs, which settles an item
    that the request files to hand do not show.

    The trace gains "versions": for each relevant step that offers alternatives, how far the sample
    queries support it and each alternative, which readiness reports.
    """
    target = read_target(target_sql, custom_fields(conversion))
    try:
        analysis = world.analysis(checks_csv)
    except checking.ChecksError as error:
        raise TargetError("the check results file does not have the expected layout") from error
    checks = analysis.checks
    confirmed = checks.confirmed(analysis.catalogue) if checks else {}
    traced = trace(target, conversion, analysis.catalogue)
    intents = read_intents(conversion, traced["all"])
    try:
        saved = sql_evidence.Saved.from_json(evidence_text, analysis.catalogue) if evidence_text else None
    except sql_evidence.EvidenceError as error:
        raise TargetError("sql_evidence.json could not be read") from error
    evidence = _Evidence(world, analysis.catalogue, analysis.held_back, saved)
    rows, versions = [], {}
    if traced["steps"]:
        rows += _source_rows(traced["steps"], analysis, evidence, checks, confirmed, versions)
    traced["versions"] = versions
    rows += _codes_rows(target, traced, analysis, checks)
    rows += _meaning_and_timing(target, traced, analysis, checks)
    rows += _core_rows(target, traced, conversion, profile_text)
    traced["routes"] = _route_rows(rows, conversion, traced)
    for row in rows:
        row["intent"] = _intent(row, intents)
        row.setdefault("route", "")
    order = {kind: i for i, kind in enumerate(KINDS)}
    rows.sort(key=lambda r: (order[r["kind"]], r["blocking"] != "yes", r["question_id"]))
    try:
        confirmed = facts_module.Facts.from_json(facts_text, analysis.catalogue) if facts_text else facts_module.Facts()
    except facts_module.FactsError as error:
        raise TargetError("facts.json could not be read") from error
    _apply_facts(rows, confirmed, traced)
    for row in rows:
        row.update({"query_state": "", "query_reason": "", "query": "", "_queries": []})
    planned = _planned(world, analysis, traced)
    profile_queries, source_tables = _profile_queries(rows, traced, conversion, profile_text, checks, analysis)
    traced["queries"] = _queries(rows, world, analysis, traced, checks, source_tables, planned)
    traced["queries"]["profile"] = profile_queries
    traced["draft"], traced["draft_restructured"], traced["draft_reason"] = None, False, ""
    try:
        if traced["steps"]:
            found = source_query(conversion, target_sql, analysis.catalogue, target_name=name or "the target query")
            traced["draft"], traced["draft_restructured"], traced["draft_reason"] = found["sql"], found["restructured"], found["reason"]
    except Exception:   # noqa: BLE001 - a draft that cannot be composed leaves the stages to the kinds of item
        traced["draft"] = None
    try:
        traced["dependencies"] = answer_dependencies(conversion, target_sql, analysis.catalogue, traced)
    except Exception:   # noqa: BLE001 - where the lineage cannot be followed, every item of the first phase counts
        traced["dependencies"] = None
    if traced["dependencies"] is not None:
        _stages(rows, traced["dependencies"])
    else:
        for row in rows:
            row["phase"] = "release" if row["kind"] in ("core", "meaning", "timing") \
                and not row["question_id"].startswith("route-") else "source"
    traced["name"] = name
    traced["questions"] = _questions(rows, traced, analysis.catalogue)
    return rows, traced


def to_csv(rows):
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=LAYOUT, lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


def _item(row):
    """How an open blocking row is named in the readiness."""
    names = row.get("_names") or {}
    wording = row.get("_wording")
    template = WORDING["item"].get(wording) or WORDING["item"].get(row["kind"])
    try:
        return template.format(**names)
    except (KeyError, AttributeError):
        return row["question_id"]


def readiness(rows, traced, scenarios=None):
    """A short statement, in plain sentences, of what the query rests on and whether the shadow database is ready for it.

    scenarios, given only when the query has been run, is what run reports under "scenarios": the
    planted scenarios whose expectations read a table that the query reads, with how many of their
    expectations were met. The statement then ends with one sentence about them.
    """
    r = WORDING["readiness"]
    lines = [r["title"], ""]
    anaesthesia = [s.file for s in traced["steps"] if s.layer == "anaesthesia"]
    core = [s.file for s in traced["steps"] if s.layer == "core"]
    derived = [s.file for s in traced["steps"] if s.layer == "derived"]
    if not traced["steps"]:
        lines.append(r["no_steps"])
    if derived:
        lines.append(r["derived_steps"].format(count=_n(len(derived), "step"), steps=_join(derived)))
    if anaesthesia:
        lines.append(r["anaesthesia_steps"].format(count=_n(len(anaesthesia), "step"), steps=_join(anaesthesia)))
    if core:
        lines.append(r["core_steps"].format(count=_n(len(core), "step"), steps=_join(core)))
    if traced["possible"]:
        possible = [s.file for s, _, _ in traced["possible"]]
        lines.append(r["possible_steps"].format(count=_n(len(possible), "further step"), steps=_join(possible)))
    # The routes that the catalogue settled, where a step gave way to one of its alternatives.
    lines += traced.get("routes") or []
    lines.append("")
    blocking = [row for row in rows if row["blocking"] == "yes"]
    counts = Counter(row["status"] for row in blocking)
    lines.append(r["blocking"].format(total=len(blocking), answered=counts["answered"], partly=counts["partly"], open=counts["open"]))
    for row in blocking:
        if row["status"] == "open":
            lines.append(r["act"].format(who=WORDING["who"][row["who"]], item=_item(row), how=WORDING["how"][row["mechanism"]]))
    if not traced["steps"]:
        pass
    elif counts["open"] == 0:
        lines.append(r["ready"])
        if counts["partly"]:
            lines.append(r["ready_partly"].format(partly=counts["partly"]))
    elif counts["open"] == 1:
        lines.append(r["not_ready_one"])
    else:
        lines.append(r["not_ready"].format(open=counts["open"]))
    others = [row for row in rows if row["blocking"] != "yes"]
    if others:
        lines.append("")
        lines.append(r["realism"].format(total=len(others), open=sum(1 for row in others if row["status"] == "open")))
    # The two stages: what the source query rests on, and everything, which the OMOP release rests on.
    if any(row.get("phase") for row in rows):
        lines.append("")
        for stage, prefix in ((PHASES[0], "source"), (None, "release")):
            found = stage_counts(rows, stage)
            key = f"{prefix}_ready" if not found["open"] else f"{prefix}_not_ready_one" if found["open"] == 1 else f"{prefix}_not_ready"
            lines.append(r[key].format(total=found["total"], open=found["open"]))
        unneeded = [row for row in rows if row.get("phase") == "unneeded"]
        if unneeded:
            lines.append(r["unneeded"].format(count=_n(len(unneeded), "item"), open=sum(1 for row in unneeded if row["status"] != "answered")))
    # Each step that offers alternatives, weighed against the sample queries, and what to do about each
    # open join that the sample queries make by another route or that a better supported alternative avoids.
    versions = traced.get("versions") or {}
    weighed = [file for file in (s.file for s in traced["steps"]) if file in versions]
    if weighed:
        lines.append("")
        lines += [sentence for file in weighed for sentence in _version_sentences(file, versions[file])]
    advice = [_advice(row, versions) for row in blocking if row["status"] == "open" and row["kind"] == "relationship"]
    if any(advice):
        lines.append("")
        lines += [sentence for sentence in advice if sentence]
    if scenarios is not None:
        lines.append("")
        count, total, met = scenarios["count"], scenarios["expectations"], scenarios["met"]
        if count == 0:
            lines.append(r["scenarios_none"])
        elif met < total:
            lines.append(r["scenarios_unmet"].format(count=count, unmet=total - met, expectations=total))
        else:
            lines.append((r["scenarios_met_one"] if count == 1 else r["scenarios_met"]).format(count=count, expectations=total))
    return "\n".join(lines) + "\n"


def _advice(row, versions):
    """One sentence that tells the reader what to do about an open join, or None where there is nothing to suggest."""
    r = WORDING["readiness"]
    for file in row.get("_makers") or []:
        version = versions.get(file)
        if version and version["best"] and row.get("_pair") not in version["joins"].get(version["best"], {}):
            return r["advice_alternative"].format(alternative=version["best"], step=file, item=_item(row))
    route = row.get("_route")
    if route:
        steps = _join(row.get("_makers") or []) or WORDING["nouns"]["step"][1]
        if len(route) == 1:
            return r["advice_direct"].format(item=_item(row), steps=steps)
        return r["advice_route"].format(through=_join(dict.fromkeys(b[0] for _, b, _ in route[:-1])), item=_item(row), steps=steps)
    return None


def counts(rows):
    """The blocking items by status, for a test or a summary: {"total", "answered", "partly", "open"}."""
    found = Counter(row["status"] for row in rows if row["blocking"] == "yes")
    return {"total": sum(found.values()), **{status: found[status] for status in register.STATUSES}}


# Running the query.

def published(sql, conversion=None):
    """The target query with every OMOP table read from the release's published schema, for SQL Server.

    The release's own rewrite is used where it can be imported. Otherwise the text is produced here,
    in the same way, with the sqlcmd variable for the published schema in place of omop. A custom
    table of the conversion is read from its published view, as a table of CDM 5.4 is.
    """
    custom = custom_fields(conversion)
    read_target(sql, custom)
    try:
        from . import release
        written = set(convert.cdm_fields()) | set(custom)
        text, _ = release.rewrite(sql, written, where="target query")
        return text.replace(f"[{release.PLACE['published']}].", f"[{PUBLISHED_VARIABLE}].")
    except Exception as error:  # noqa: BLE001 - release.py is being changed elsewhere; its refusals still count
        if type(error).__name__ == "Refused":
            raise TargetError(str(error)) from error
    tree = sqlglot.parse_one(sql, dialect="tsql")
    marker = "SCHEMALYSER_PUBLISHED"
    for table in tree.find_all(exp.Table):
        if _is_omop(table):
            table.set("db", exp.to_identifier(marker))
    text = tree.sql(dialect="tsql", pretty=True, identify=True, comments=False)
    return text.replace(f"[{marker}].", f"[{PUBLISHED_VARIABLE}].")


def run(world, conversion, target_sql, rows=500, checks_csv=None, scenarios=None):
    """Builds the sandbox, runs the conversion and then the target query on the synthetic rows.

    Returns {"columns", "rows", "published", "failures", "scenarios"}. failures lists each reason that
    the conversion did not run cleanly, as convert.failures gives them. scenarios names the planted
    scenarios to run, as for convert.run, and the result's "scenarios" says how many of them bear on
    the tables that the query reads, because one of their expectations reads such a table, and how
    many of those scenarios' expectations were met.
    """
    from .translate import Unreadable, Unsupported, to_duckdb
    import duckdb
    target = read_target(target_sql, custom_fields(conversion))
    try:
        converted, report = convert.run(world, conversion, rows, checks=checks_csv, scenarios=scenarios)
    except convert.ScenarioError as error:
        raise TargetError(str(error)) from error
    tables = {table.name.lower() for table in target["tree"].find_all(exp.Table) if _is_omop(table)}
    bearing = [s for s in report["scenarios"] if tables & set(s["reads"])]
    # A scenario whose rows could not be planted counts as one expectation that was not met.
    summary = {"count": len(bearing), "expectations": sum(len(s["expectations"]) or 1 for s in bearing),
               "met": sum(1 for s in bearing for item in s["expectations"] if item["met"]),
               "names": [s["name"] for s in bearing]}
    try:
        statements = to_duckdb(target_sql, converted.sandbox.date_columns)
    except (Unreadable, Unsupported) as error:
        raise TargetError("the target query could not be translated for the sandbox") from error
    if len(statements) != 1:
        raise TargetError("the target query did not translate to a single statement")
    try:
        cursor = converted.con.execute(statements[0])
        found = cursor.fetchall()
    except duckdb.Error as error:
        raise TargetError(f"the target query did not run on the synthetic rows: {str(error).splitlines()[0]}") from error
    return {"columns": [d[0] for d in cursor.description], "rows": found, "published": published(target_sql, conversion),
            "failures": convert.failures(report), "conversion": converted, "scenarios": summary}


# The source-side draft.

# The wording of the draft, which the analytics team reads. It awaits the clinical lead's approval.
DRAFT_WORDING = {
    "header": [
        "This draft names the tables and local codes of the hospital's database, so it is for use inside the hospital only.",
        "DRAFT for the analytics team to correct. Schemalyser composed this query from the steps of the conversion, and it has not been run on a real database.",
        "The query answers the question below from the source tables alone, without any OMOP table, by composing the steps of the conversion that the question rests on. It builds every row of those steps before it keeps the rows of the question, so it is not suitable to run on a large database until it has been restructured.",
    ],
    "question": "The question, as the target query {name} states it:",
    "layout": "Each common table expression stands for one step of the conversion and carries that step's own comment. The mapping rows that the steps look up are written into the query as a table of values. The identifiers in this query are numbered for this query alone, and they need not match those in the OMOP tables.",
    "assumptions": "Each assumption that the query rests on is numbered below, and its number is marked on the line it affects. Where your own practice differs, please correct the query and return it.",
    "join": "The query pairs each row with the rows in which {left} equals {right}.",
    "first": "For each {partition}, the query takes the row that comes first by {order} as the one that counts.",
    "descending": "{column} in descending order",
    "filter": "The query keeps only the rows in which {columns} holds {values}.",
    "filter_out": "The query leaves out the rows in which {columns} holds {values}.",
    "blank": "The SELECT below leaves blank any count from 1 to 4, so that no small number can point to a child. It may be removed where the audit's approval allows exact small numbers.",
    "code": "The code {code} in {columns} means {description}, and the query reads it as the concept {concept}.",
    "code_constant": "The query looks up the code {code} under {vocabulary}, which means {description}, and reads it as the concept {concept}.",
    "step": "{file}, a step of the {layer} layer:",
    "mapping": "The mapping rows that the steps look up, one assumption on each row:",
    "empty": "No step that the question rests on writes {table}, so the query reads it as an empty table.",
    "target": "The question itself, asked of the tables above:",
    "marker_one": "assumption {numbers}",
    "marker_many": "assumptions {numbers}",
}
DRAFT_TYPES = {"integer": "BIGINT", "float": "FLOAT", "date": "DATE", "datetime": "DATETIME2", "varchar(max)": "VARCHAR(MAX)"}
MARK = "SCHEMALYSER_A"
COMPARISONS = (exp.EQ, exp.NEQ, exp.In)


def _draft_type(datatype):
    return DRAFT_TYPES.get(datatype, datatype.upper())


def _leading_comments(sql):
    """The comment lines at the top of a step or a target query, without their dashes."""
    found = []
    for line in sql.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("--"):
            break
        found.append(stripped[2:].strip())
    return found


def _cte_name(text):
    return "".join(c if c.isalnum() or c == "_" else "_" for c in text)


class _Assumptions:
    """The numbered assumptions of a draft, each given once, in the order in which the query meets them."""

    def __init__(self):
        self.items = {}

    def number(self, key, text):
        if key not in self.items:
            self.items[key] = (len(self.items) + 1, text)
        return self.items[key][0]

    def mark(self, node, numbers):
        numbers = sorted(set(numbers))
        if numbers:
            node.add_comments([f"{MARK}{'_'.join(map(str, numbers))}"])


def _quote(value):
    text = " ".join(str(value).split())
    return "'" + text.replace("'", "''") + "'"


def _finish_lines(text):
    """Turns the markers that sqlglot carried into a comment at the end of each line they stand on."""
    lines = []
    for line in text.splitlines():
        numbers = []
        for found in re.findall(rf"/\* ?{MARK}([0-9_]+) ?\*/", line):
            numbers += [int(n) for n in found.split("_")]
        line = re.sub(rf" ?/\* ?{MARK}[0-9_]+ ?\*/", "", line)
        if numbers:
            numbers = sorted(set(numbers))
            key = "marker_one" if len(numbers) == 1 else "marker_many"
            line += "  -- " + DRAFT_WORDING[key].format(numbers=register._join(str(n) for n in numbers))
        lines.append(line)
    return "\n".join(lines)


def _is_source_table(table):
    """A table node that reads a stored table, rather than a derived table, a function or a table variable."""
    return isinstance(table.this, exp.Identifier) and not table.name.startswith(("#", "@"))


def source_draft(conversion, target_sql, catalogue=None, mappings=None, target_name="the target query", blank=True):
    """One T-SQL query that answers the target question from the source tables alone, as text.

    The relevant steps are composed in the conversion's order as common table expressions, each
    OMOP table that a step reads becomes the union of the relevant steps before it that write that
    table, and the mapping rows become a table of values. Each anaesthesia step's identifiers are
    moved past the layer's offset as the runner moves them, so that they stay apart from the core's.
    Each join between source columns, each code that a filter compares a source column with, and
    each mapping row is a numbered assumption, marked on the line it affects. mappings, when given,
    are rows of source_to_concept_map, as dictionaries, in place of the folder's own.

    The draft names the source tables and columns and holds the mapping rows with their local codes,
    so it is for use inside the hospital only, and its first line says so. With blank, which is the default, it
    ends with an outer SELECT that leaves blank any count from 1 to 4.
    """
    folder = Path(conversion)
    custom = custom_fields(folder)
    target = read_target(target_sql, custom)
    traced = trace(target, folder, catalogue)
    steps = traced["steps"]
    definitions = {}
    for row in convert._definitions(convert.read_tables(folder)):
        definitions.setdefault(row["table"], []).append(row)
    settings = json.loads((folder / "release.json").read_text()) if (folder / "release.json").exists() else {}
    offset = settings.get("identifier_offset", convert.IDENTIFIER_OFFSET)
    assumptions = _Assumptions()
    spelled = lambda origin: f"{origin[0]}.{origin[1]}"  # noqa: E731

    # The mapping rows that the relevant steps look up, and the source columns that each vocabulary reads.
    if mappings is None:
        mappings = convert.mapping_dicts(folder)
    vocabularies, columns_of, unresolved, mapping_fields = set(), {}, False, ["source_code", "source_vocabulary_id",
                                                                              "source_code_description", "target_concept_id"]
    for step in steps:
        for alias, entry in step.mappings.items():
            vocabularies |= entry.vocabularies
            unresolved = unresolved or entry.unresolved
            for vocabulary in entry.vocabularies:
                columns_of.setdefault(vocabulary, [])
                columns_of[vocabulary] += [c for c in entry.columns if c not in columns_of[vocabulary]]
            if step.tree is not None:
                for column in step.tree.find_all(exp.Column):
                    if column.table.upper() == alias and column.name.lower() not in mapping_fields:
                        mapping_fields.append(column.name.lower())
    used_rows = [row for row in mappings if unresolved or str(row.get("source_vocabulary_id") or "") in vocabularies]
    row_numbers, by_vocabulary = [], {}
    for row in used_rows:
        vocabulary, code = str(row.get("source_vocabulary_id") or ""), str(row.get("source_code") or "")
        description = str(row.get("source_code_description") or "").strip() or vocabulary
        first = description.split(" ")[0]
        if first[:1].isupper() and first[1:].islower():
            description = description[0].lower() + description[1:]
        concept = str(row.get("target_concept_id") or "0")
        columns = columns_of.get(vocabulary)
        text = (DRAFT_WORDING["code"].format(code=code, columns=register._join(spelled(c) for c in columns), description=description,
                                             concept=concept) if columns
                else DRAFT_WORDING["code_constant"].format(code=code, vocabulary=vocabulary, description=description, concept=concept))
        number = assumptions.number(("code", vocabulary, code), text)
        row_numbers.append(number)
        by_vocabulary.setdefault(vocabulary, []).append((number, concept))

    def origins(step, column):
        """The source columns behind a column of a step or of the target query."""
        select = column.find_ancestor(exp.Select)
        found = []
        for origin in _columns_behind(step, select, column):
            if origin[0] == "omop":
                found += [o for o in _behind(traced, origin[1], origin[2]) if o not in found]
            elif origin not in found:
                found.append(origin)
        return [(_spelled(catalogue, o) or tuple(o)) if catalogue is not None else tuple(o) for o in found]

    def annotate(tree, step):
        """Marks each comparison of a step, or of the target query, with the assumptions it rests on."""
        mapping_aliases = {alias for alias in (step.mappings if step is not None else {})}
        for node in list(tree.find_all(*COMPARISONS)):
            sides = [node.this] + (list(node.expressions) if isinstance(node, exp.In) else [node.expression])
            columns = [[c for c in ([side] if isinstance(side, exp.Column) else side.find_all(exp.Column))] for side in sides]
            numbers = []
            # A lookup in the mapping table rests on the rows of its vocabularies.
            lookup = [c for group in columns for c in group if c.table.upper() in mapping_aliases]
            if lookup:
                alias = lookup[0].table.upper()
                if any(c.name.lower() == "source_code" for c in lookup):
                    numbers += [n for v in step.mappings[alias].vocabularies for n, _ in by_vocabulary.get(v, [])]
                assumptions.mark(node, numbers)
                continue
            literals = [_literal(side) for side in sides]
            ranked = _first_of(step, node, origins)
            if ranked is not None:
                numbers.append(assumptions.number(("first",) + ranked, DRAFT_WORDING["first"].format(partition=ranked[0], order=ranked[1])))
            elif isinstance(node, exp.EQ) and all(isinstance(side, exp.Column) for side in sides):
                left, right = (sorted({o for c in group for o in origins(step, c)}) for group in columns)
                if left and right and not set(left) & set(right):
                    pair = tuple(sorted((spelled(left[0]), spelled(right[0]))))
                    numbers.append(assumptions.number(("join", pair), DRAFT_WORDING["join"].format(left=pair[0], right=pair[1])))
            elif step is not None and columns[0] and all(value is not None for value in literals[1:]) and len(sides) > 1:
                found = sorted({o for c in columns[0] for o in origins(step, c)})
                values = [_literal(side) for side in sides[1:]]
                if found and (any(isinstance(side, exp.Literal) and side.is_string for side in sides[1:]) or not isinstance(node, exp.NEQ)):
                    shown = register._join(_quote(v) if isinstance(side, exp.Literal) and side.is_string else str(v)
                                           for v, side in zip(values, sides[1:]))
                    names = register._join(spelled(o) for o in found)
                    negated = isinstance(node, exp.NEQ) or isinstance(node.parent, exp.Not)
                    numbers.append(assumptions.number(("filter", names, shown), DRAFT_WORDING["filter_out" if negated else "filter"].format(values=shown, columns=names)))
            if step is None and any(c.name.lower().endswith("_concept_id") for c in columns[0]):
                # A concept that the question compares with rests on the mapping rows that lead to it.
                wanted = {_norm(v) for v in literals if v is not None}
                numbers += [n for rows in by_vocabulary.values() for n, concept in rows if _norm(concept) in wanted]
            assumptions.mark(node, numbers)

    def clean(tree):
        for node in tree.walk():
            node.comments = None
        return tree

    # Which OMOP tables each reader sees, so that one union stands for each distinct set of writers.
    writers_of = lambda table, before: tuple(s.index for s in steps if s.table == table and s.index < before)  # noqa: E731
    step_names = {s.index: _cte_name(f"step_{s.index + 1:02d}_{Path(s.file).stem}") for s in steps}
    unions = {}

    def union_name(table, writers):
        key = (table, writers)
        if key not in unions:
            same = [k for k in unions if k[0] == table]
            unions[key] = _cte_name(f"omop_{table}" + (f"_{len(same) + 1}" if same else ""))
        return unions[key]

    def place(tree, before):
        hoisted = []
        name = "with_" if "with_" in tree.arg_types else "with"
        ctes = tree.args.get(name)
        local = {}
        if ctes is not None:
            tree.set(name, None)
            for cte in ctes.expressions:
                local[cte.alias.upper()] = _cte_name(f"q{before:02d}_{cte.alias}")
                hoisted.append(cte)
        # The tables inside the hoisted expressions are renamed as well, since they may read the OMOP tables and each other.
        for table in [t for node in [tree, *(cte.this for cte in hoisted)] for t in node.find_all(exp.Table)]:
            if not table.db and table.name.upper() in local:
                table.set("this", exp.to_identifier(local[table.name.upper()]))
            elif _is_omop(table):
                target_name_ = "mapping_rows" if table.name.lower() == MAPPING_TABLE else union_name(table.name.lower(), writers_of(table.name.lower(), before))
                table.set("this", exp.to_identifier(target_name_))
                table.set("db", None)
                table.set("catalog", None)
        for cte in hoisted:
            cte.set("alias", exp.TableAlias(this=exp.to_identifier(local[cte.alias.upper()])))
        return hoisted

    parts = []
    highest = {}
    for step in steps:
        tree = clean(sqlglot.parse_one(step.sql, dialect="tsql"))
        fresh = _Step(step.index, {"file": step.file, "table": step.table, "layer": step.layer}, step.sql, catalogue, step.fields)
        fresh.tree, fresh.earlier = tree, step.earlier
        fresh.outputs = {p.alias_or_name.lower(): p.unalias() for p in tree.expressions}
        fresh.omop_aliases = step.omop_aliases
        fresh.mappings = step.mappings
        if step.layer == "derived" and traced["needed"].get(step.index) is not None:
            skipped = fresh._unneeded(traced["needed"][step.index])
            kinds = {row["field"]: row["datatype"] for row in definitions.get(step.table, [])}
            for join in list(tree.args.get("joins") or []):
                if join.this.alias_or_name.upper() in skipped:
                    join.pop()
            for projection in tree.expressions:
                if any(c.table.upper() in skipped for c in projection.find_all(exp.Column)):
                    projection.replace(exp.alias_(exp.cast(exp.null(), _draft_type(kinds.get(projection.alias_or_name, "varchar(max)")), dialect="tsql"),
                                                  projection.alias_or_name))
        annotate(tree, fresh)
        hoisted = place(tree, step.index)
        # Each source table is read WITH (NOLOCK), so that the query neither waits for other work nor holds it
        # up. The names that the draft itself gives, to its steps, unions, mapping rows and hoisted expressions,
        # are not source tables.
        own = set(unions.values()) | set(step_names.values()) | {"mapping_rows"} | {cte.alias for cte in hoisted}
        for node in [tree, *(cte.this for cte in hoisted)]:
            for table in node.find_all(exp.Table):
                if table.name and table.name not in own and not _is_omop(table) and _is_source_table(table):
                    table.set("hints", [exp.WithTableHint(expressions=[exp.Var(this="NOLOCK")])])
        body = tree.sql(dialect="tsql", pretty=True).replace("   WITH (NOLOCK)", " WITH (NOLOCK)")
        outputs = [p.alias_or_name for p in tree.expressions]
        key = next((row["field"] for row in definitions.get(step.table, []) if row["primary_key"] == "Y"), None)
        if step.layer == "anaesthesia" and key in outputs:
            earlier = highest.get(step.table, [])
            base = (f"COALESCE((SELECT MAX({key}) FROM ({' UNION ALL '.join(f'SELECT {key} FROM {step_names[i]}' for i in earlier)}) AS earlier), {offset})"
                    if earlier else str(offset))
            highest.setdefault(step.table, []).append(step.index)
            projected = ",\n  ".join(f"step.{c} + {base} AS {c}" if c == key else f"step.{c}" for c in outputs)
            body = f"SELECT\n  {projected}\nFROM (\n" + "\n".join("  " + line for line in body.splitlines()) + "\n) AS step"
        comment = [DRAFT_WORDING["step"].format(file=step.file, layer=step.layer)] + ["  " + line for line in _leading_comments(step.sql)]
        for cte in hoisted:
            parts.append(([], cte.alias, cte.this.sql(dialect="tsql", pretty=True)))
        parts.append((comment, step_names[step.index], body))
        step.draft_outputs = outputs

    # The question itself.
    target_tree = clean(sqlglot.parse_one(target_sql, dialect="tsql"))
    annotate(target_tree, None)
    hoisted = place(target_tree, len(traced["all"]))
    for cte in hoisted:
        parts.append(([], cte.alias, cte.this.sql(dialect="tsql", pretty=True)))
    final = target_tree.sql(dialect="tsql", pretty=True)

    # The unions, each placed before its first reader, and the empty tables.
    by_step = {s.index: s for s in steps}
    union_parts = []
    for (table, writers), name in unions.items():
        rows = definitions.get(table, [])
        if not writers:
            names = ",\n  ".join(f"CAST(NULL AS {_draft_type(r['datatype'])}) AS {r['field']}" for r in rows)
            union_parts.append(([DRAFT_WORDING["empty"].format(table=table)], name, f"SELECT\n  {names}\nWHERE\n  1 = 0"))
            continue
        written = {c for i in writers for c in by_step[i].draft_outputs}
        branches = []
        for i in writers:
            own = set(by_step[i].draft_outputs)
            names = ",\n  ".join((r["field"] if r["field"] in own else f"CAST(NULL AS {_draft_type(r['datatype'])}) AS {r['field']}")
                                 for r in rows if r["field"] in written)
            branches.append(f"SELECT\n  {names}\nFROM {step_names[i]}")
        union_parts.append(([], name, "\nUNION ALL\n".join(branches)))

    # The mapping rows, one line each.
    if used_rows:
        values = []
        for row, number in zip(used_rows, row_numbers):
            cells = []
            for field in mapping_fields:
                value = row.get(field)
                if value is None or value == "":
                    cells.append("NULL")
                elif field.endswith("concept_id") and _re_digits(value):
                    cells.append(str(int(str(value))))
                else:
                    cells.append(_quote(value))
            values.append(f"    ({', '.join(cells)}) /* {MARK}{number} */")
        mapping_body = ("SELECT\n  *\nFROM (VALUES\n" + ",\n".join(values) + f"\n) AS v ({', '.join(mapping_fields)})")
    else:
        mapping_body = "SELECT\n  " + ",\n  ".join(f"CAST(NULL AS VARCHAR(255)) AS {f}" for f in mapping_fields) + "\nWHERE\n  1 = 0"

    # The header, with the assumptions listed in order.
    lines = [f"-- {line}" for line in DRAFT_WORDING["header"]] + ["--", f"-- {DRAFT_WORDING['question'].format(name=target_name)}"]
    lines += [f"--   {line}" for line in _leading_comments(target_sql)] + ["--", f"-- {DRAFT_WORDING['layout']}", "--",
                                                                        f"-- {DRAFT_WORDING['assumptions']}"]
    for number, text in sorted(assumptions.items.values()):
        lines.append(f"--   {number}. {text}")
    ordered = [([DRAFT_WORDING["mapping"]], "mapping_rows", mapping_body)]
    # Each union goes after the last of its writers, and each step after the unions it reads.
    placed = set()
    for comment, name, body in parts:
        for item in union_parts:
            if item[1] not in placed and re.search(rf"\b{item[1]}\b", body):
                ordered.append(item)
                placed.add(item[1])
        ordered.append((comment, name, body))
    ordered += [item for item in union_parts if item[1] not in placed]
    lines.append("WITH")
    blocks = []
    for comment, name, body in ordered:
        indented = "\n".join("  " + line for line in body.splitlines())
        blocks.append("\n".join(f"-- {line}" for line in comment) + ("\n" if comment else "") + f"{name} AS (\n{indented}\n)")
    lines.append(",\n".join(blocks))
    inner, outer = blanking(sqlglot.parse_one(final, dialect="tsql"),
                            count_columns(sqlglot.parse_one(target_sql, dialect="tsql")) if blank else [])
    if outer:
        blocks.append(f"-- {DRAFT_WORDING['target']}\nresult AS (\n" + "\n".join("  " + line for line in inner.splitlines()) + "\n)")
        lines[-1] = ",\n".join(blocks)
        lines += [f"-- {DRAFT_WORDING['blank']}", outer]
    else:
        lines += [f"-- {DRAFT_WORDING['target']}", final]
    return _finish_lines("\n".join(lines)) + "\n"


# A specification for a person who writes the audit query himself, and a check of what he writes.

SPECIFICATION_WORDING = {
    "title": "Specification of {name}, for a person who writes the query against the source database",
    "inside": "This page names the tables and local codes of the hospital's database, so it is for use inside the hospital only.",
    "h_question": "1. The question and its rules",
    "h_depends": "2. The tables, columns and joins that the answer depends on",
    "tables": "The answer reads these tables: {tables}.",
    "columns": "From {table}, it reads {columns}.",
    "join": "{left} joins to {right}: {status}.",
    "filter_keep": "It keeps only the rows in which {column} holds {values}: {status}.",
    "filter_leave": "It leaves out the rows in which {column} holds {values}: {status}.",
    "not_needed": "The conversion's step {step} also joins {table}, which the answer does not read. A query need not join it, provided that the join does not multiply rows.",
    "by_sql": "confirmed by the data team's existing SQL",
    "by_person": "confirmed by a person",
    "by_checks": "confirmed by the check results",
    "unconfirmed": "not yet confirmed",
    "h_codes": "3. The local codes",
    "code": "The code {code} under {vocabulary} means {description}, which the question reads as {concept}{confirmed}.",
    "code_person": ", as a person confirmed",
    "no_codes": "The answer depends on no local code.",
    "h_shape": "4. The shape of the result",
    "shape": "The result has the columns {columns}, in that order.",
    "counts": "The columns {columns} are counts.",
    "h_small": "5. Small numbers",
    "small": "Any count from 1 to 4 is left blank in the result, so that no small number can point to a child, unless the audit's approval allows exact small numbers.",
    "h_cases": "6. Acceptance cases",
    "cases": "The synthetic database holds these planted cases, and a query that follows the rules gives what each one says. Schemalyser can check a query against them with python -m schemalyser.target ... --check-query FILE.",
    "case": "{description} {expectations}",
    "no_cases": "No planted case bears on the tables that this question reads.",
}


def specification(conversion, target_sql, rows, traced, catalogue, name="the target query"):
    """One page of plain text for a person who writes the audit query himself, from the checklist of the target.

    It states the question and its rules from the target's own header comment; the tables, columns, joins and
    filters that the answer depends on, each marked by what confirmed it; the local codes from the mapping rows;
    the shape of the result; the rule for small numbers; and the planted cases that bear on the question. It
    names source tables and local codes, so it is for use inside the hospital only.
    """
    w = SPECIFICATION_WORDING
    folder = Path(conversion)
    deps = traced.get("dependencies") or answer_dependencies(folder, target_sql, catalogue, traced)
    by_id = {row["question_id"]: row for row in rows}
    lines = [w["title"].format(name=name), "", w["inside"], "", w["h_question"], ""]
    lines += [line for line in _leading_comments(target_sql)] + ["", w["h_depends"], ""]
    lines.append(w["tables"].format(tables=_join(sorted(deps["tables"], key=str.upper))))
    by_table = {}
    for column in sorted(deps["columns"], key=str.upper):
        table, _, field = column.partition(".")
        by_table.setdefault(table, []).append(field)
    lines += [w["columns"].format(table=table, columns=_join(fields)) for table, fields in sorted(by_table.items())]

    def status(row):
        if row is None or row["status"] != "answered":
            return w["unconfirmed"]
        return {"a person": w["by_person"], "sample queries": w["by_sql"], "check results": w["by_checks"]}.get(
            row.get("currently_from"), w["by_sql"])

    lines.append("")
    # The joins: those within a step that the answer reads, and those that the checklist finds across the steps.
    pairs = {frozenset(c.upper() for c in pair): tuple(sorted(pair)) for pair in deps["joins"]}
    for r in rows:
        if r["kind"] == "relationship" and r.get("phase") == "source":
            names = r.get("_names") or {}
            pairs.setdefault(frozenset({names.get("left", "").upper(), names.get("right", "").upper()}),
                             (names.get("left", ""), names.get("right", "")))
    for key, (left, right) in sorted(pairs.items(), key=lambda item: item[1]):
        row = next((r for r in rows if r["kind"] == "relationship" and
                    frozenset({(r.get("_names") or {}).get("left", "").upper(), (r.get("_names") or {}).get("right", "").upper()}) == key), None)
        lines.append(w["join"].format(left=left, right=right, status=status(row)))
    for column in sorted(deps["columns"], key=str.upper):
        kept, left_out = _filter_values(traced["steps"], column)
        if kept or left_out:
            row = by_id.get(f"filter-{column}")
            key = "filter_keep" if kept else "filter_leave"
            lines.append(w[key].format(column=column, values=_join(_quote(v) for v in (kept or left_out)), status=status(row)))
    for step, table in dict.fromkeys(deps["not_needed"]):
        if table != MAPPING_TABLE:
            lines.append(w["not_needed"].format(step=step, table=table))
    lines += ["", w["h_codes"], ""]
    from . import facts as facts_module
    site = {(str(r.get("source_vocabulary_id")), str(r.get("source_code"))) for r in
            (list(csv.DictReader(open(folder / facts_module.SITE_MAPPINGS, newline="", encoding="utf-8")))
             if (folder / facts_module.SITE_MAPPINGS).exists() else [])}
    # Only the codes that lead to a concept that the question compares with, under any vocabulary the answer reads.
    target_read = read_target(target_sql, custom_fields(folder))
    compared = {_norm(v) for values in target_read["concepts"].values() for v in values}
    every = [row for row in convert.mapping_dicts(folder) if row.get("source_vocabulary_id") in deps["vocabularies"]
             and row.get("source_vocabulary_id") != "SITE_SETTING"]
    wanted = {str(row.get("source_code")) for row in every if _norm(row.get("target_concept_id") or "") in compared}
    codes = [row for row in every if str(row.get("source_code")) in wanted]
    for row in sorted(codes, key=lambda r: (r.get("source_vocabulary_id") or "", r.get("source_code") or "")):
        confirmed = w["code_person"] if (row.get("source_vocabulary_id"), row.get("source_code")) in site else ""
        lines.append(w["code"].format(code=row.get("source_code"), vocabulary=row.get("source_vocabulary_id"),
                                      description=(row.get("source_code_description") or "").strip() or row.get("source_vocabulary_id"),
                                      concept=concepts.named(folder, row.get("target_concept_id")), confirmed=confirmed))
    if not codes:
        lines.append(w["no_codes"])
    tree = sqlglot.parse_one(target_sql, dialect="tsql")
    final = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    lines += ["", w["h_shape"], "", w["shape"].format(columns=_join(p.alias_or_name for p in final.expressions))]
    counted = count_columns(tree)
    if counted:
        lines.append(w["counts"].format(columns=_join(counted)))
    lines += ["", w["h_small"], "", w["small"], "", w["h_cases"], ""]
    tables = {t.name.lower() for t in tree.find_all(exp.Table) if _is_omop(t)}
    bearing = [s for s in convert.chosen_scenarios(folder) if tables & set(s["reads"])]
    if bearing:
        lines.append(w["cases"])
        lines.append("")
        for scenario in bearing:
            said = " ".join(item["says"] for item in scenario["expectations"])
            lines.append(f"- {scenario['name']}: " + w["case"].format(description=scenario["description"].strip(), expectations=said))
    else:
        lines.append(w["no_cases"])
    return "\n".join(lines) + "\n"


CHECK_WORDING = {
    "agree": "The hand-written query gives the same table as the target query on the synthetic rows, with the planted cases.",
    "differ": "The hand-written query does not give the same table as the target query on the synthetic rows.",
    "columns": "The target's columns: {columns}.",
    "target": "The target query gives:",
    "query": "The hand-written query gives:",
    "differs": "The rows that differ:",
    "only_target": "only the target query gives",
    "only_query": "only the hand-written query gives",
    "scenarios": "These planted cases bear on the question, and their expectations say what each band should hold: {names}.",
}


def check_query(world, conversion, target_sql, query_sql, rows=500, scenarios=None):
    """Runs a query written by hand over the source tables on the synthetic database, beside the target's answer.

    Both run on the same rows, with the planted scenarios. Returns {"agree", "columns", "target", "query",
    "differs", "scenarios"}: whether the two tables agree, the two answers, the rows that differ, and the
    planted scenarios that bear on the question. A count that the hand-written query leaves blank is accepted
    where the target's count lies from 1 to 4. Nothing of the hand-written query is ever written to an output.
    """
    from .translate import Unreadable, Unsupported, to_duckdb
    import duckdb
    found = run(world, conversion, target_sql, rows=rows, scenarios=scenarios)
    converted = found["conversion"]
    try:
        statements = to_duckdb(query_sql, converted.sandbox.date_columns)
        if len(statements) != 1:
            raise TargetError("the query must be one statement")
        theirs = converted.con.execute(statements[0]).fetchall()
    except (Unreadable, Unsupported, duckdb.Error) as error:
        raise TargetError("the hand-written query did not run on the synthetic rows") from error

    def plain(value):
        if value is None:
            return None
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        return str(value)

    ours = [tuple(plain(v) for v in row) for row in found["rows"]]
    given = [tuple(plain(v) for v in row) for row in theirs]

    def matches(mine, other):
        if len(mine) != len(other):
            return False
        for a, b in zip(mine, other):
            if a == b:
                continue
            if b is None and a is not None and a.isdecimal() and 1 <= int(a) <= 4:
                continue    # a count that the hand-written query leaves blank
            return False
        return True

    unmatched = list(given)
    differs = []
    for row in ours:
        partner = next((g for g in unmatched if matches(row, g)), None)
        if partner is None:
            differs.append(("target", row))
        else:
            unmatched.remove(partner)
    differs += [("query", row) for row in unmatched]
    return {"agree": not differs, "columns": found["columns"], "target": ours, "query": given, "differs": differs,
            "scenarios": found["scenarios"]["names"]}


# A result that is safe by default.

def count_columns(select):
    """The output columns of a SELECT that are counts: a COUNT, or a SUM of an indicator that is only 0 or 1,
    looking through COALESCE and through a column of a common table expression or a derived table to its CASE."""
    root = select if isinstance(select, exp.Select) else select.find(exp.Select)
    derived = {cte.alias.upper(): cte.this for cte in select.find_all(exp.CTE)}
    for sub in select.find_all(exp.Subquery):
        if sub.alias:
            derived[sub.alias.upper()] = sub.this
    tables = {t.alias_or_name.upper(): t.name.upper() for t in select.find_all(exp.Table)}

    def indicator(node, depth=0):
        if depth > 5:
            return False
        if isinstance(node, exp.Literal):
            return node.this in ("0", "1")
        if isinstance(node, exp.Case):
            branches = [i.args.get("true") for i in node.args.get("ifs") or []] + [node.args.get("default")]
            return all(b is not None and indicator(b, depth + 1) for b in branches)
        if isinstance(node, exp.Column):
            name = tables.get(node.table.upper(), node.table.upper())
            source = derived.get(name)
            source = source if isinstance(source, exp.Select) or source is None else source.find(exp.Select)
            projection = next((p for p in (source.expressions if source is not None else [])
                               if p.alias_or_name.upper() == node.name.upper()), None)
            return projection is not None and indicator(projection.unalias(), depth + 1)
        return False

    found = []
    for projection in root.expressions:
        value = projection.unalias()
        while isinstance(value, exp.Coalesce):
            value = value.this
        if isinstance(value, exp.Count) or (isinstance(value, exp.Sum) and indicator(value.this)):
            found.append(projection.alias_or_name)
    return found


def blanking(select, counts=None):
    """A SELECT made safe by default, as (the inner SELECT, the outer SELECT), both as T-SQL text.

    The inner SELECT is the query itself without its ORDER BY, with a column that keeps its order. The outer
    SELECT reads it as result and leaves blank any count from 1 to 4. Where the query has no count, the outer
    SELECT is "" and nothing changes.
    """
    counts = count_columns(select) if counts is None else counts
    if not counts:
        return select.sql(dialect="tsql", pretty=True), ""
    tree = select.copy()
    order = tree.args.get("order")
    tree.set("order", None)
    if order is not None:
        window = exp.Window(this=exp.RowNumber(), order=order.copy())
        tree.expressions.append(exp.alias_(window, "result_order"))
    names = [p.alias_or_name for p in select.expressions]
    shown = [(f"CASE WHEN r.{name} BETWEEN 1 AND 4 THEN NULL ELSE r.{name} END AS {name}" if name in counts else f"r.{name}")
             for name in names]
    outer = "SELECT\n  " + ",\n  ".join(shown) + "\nFROM result AS r" + ("\nORDER BY\n  r.result_order" if order is not None else "")
    return tree.sql(dialect="tsql", pretty=True), outer


def blanked(sql):
    """A whole query, which may begin with common table expressions, with the blanking outer SELECT added."""
    tree = sqlglot.parse_one(sql, dialect="tsql")
    ctes = tree.args.get("with_") or tree.args.get("with")
    final = tree.copy()
    final.set("with_" if "with_" in final.arg_types else "with", None)
    inner, outer = blanking(final, count_columns(tree))
    if not outer:
        return sql
    parts = [cte.sql(dialect="tsql", pretty=True) for cte in (ctes.expressions if ctes is not None else [])]
    parts.append("result AS (\n" + "\n".join("  " + line for line in inner.splitlines()) + "\n)")
    return "WITH " + ",\n".join(parts) + f"\n-- {DRAFT_WORDING['blank']}\n" + outer


# The source query restructured to start from the cohort.

RESTRUCTURE_WORDING = {
    "starts": "This query starts from the cohort of the question: it is written to work out the question's own rows first from the small tables, and to reach each larger table through the records of that cohort and for the local codes that the question uses. That is the order of the text and not a promise about how SQL Server will run it, so the database administrator should see its plan before it is run on a large database. It joins on the source keys themselves.",
    "fallback": "Schemalyser could not restructure this query to start from the cohort, because {reason}. It is the composition of the steps as they are, which builds every row of those steps before it keeps the rows of the question, so it is not suitable to run on a large database.",
    "assumptions": "The assumptions that the answer rests on:",
    "layout": "-- Each common table expression stands for one step of the conversion, cut down to what the answer reads, and they are in the order in which each follows what it reads. The mapping rows that the steps look up are written into the query as a table of values. The identifiers in this query are the source keys themselves.",
}


def source_query(conversion, target_sql, catalogue, mappings=None, target_name="the target query", blank=True, traced=None):
    """The question as one query over the source tables, restructured to start from the cohort where that is safe.

    Returns {"sql", "restructured", "reason", "lines"}. Where a rewrite cannot be shown to keep the answer, the
    query is the present composition of source_draft, with a header that says so and why.
    """
    from . import restructure as rewriting
    folder = Path(conversion)
    present = source_draft(folder, target_sql, catalogue, mappings, target_name=target_name, blank=False)
    rows = mappings if mappings is not None else convert.mapping_dicts(folder)
    names = set(re.findall(r"^(q\d{2}_[A-Za-z0-9_]+) AS \(", present, re.M))
    body, reason = rewriting.restructure(present, names, catalogue, rows) if catalogue is not None else (None, "no catalogue was given")
    header = [line for line in present.split("\nWITH\n", 1)[0].splitlines()]
    if body is None:
        text = source_draft(folder, target_sql, catalogue, mappings, target_name=target_name, blank=blank)
        lines = text.splitlines()
        note = [f"-- {line}" for line in textwrap_lines(RESTRUCTURE_WORDING["fallback"].format(reason=reason))]
        text = "\n".join(lines[:3] + note + lines[3:]) + "\n"
        return {"sql": text, "restructured": False, "reason": reason, "lines": len(text.splitlines())}
    # The assumptions that the answer still rests on are those whose number still marks a line of the query.
    marks = lambda text: {int(n) for found in re.findall(r"/\* assumptions? ([\d, and]+) \*/", text)  # noqa: E731
                          for n in re.findall(r"\d+", found)}
    marked = marks(body)
    values = re.search(r"^mapping_rows AS \((.*?)^\)", body, re.M | re.S)
    rows_left = marks(values.group(1)) if values else set()
    kept = []
    for line in header[3:]:
        found = re.match(r"--   (\d+)\. (.*)", line)
        if found and int(found.group(1)) not in marked:
            continue
        # A mapping row is an assumption only while the query still holds it.
        if found and re.match(r"(The code \S+ in |The query looks up the code )", found.group(2)) and int(found.group(1)) not in rows_left:
            continue
        kept.append(line)
    kept = [RESTRUCTURE_WORDING["layout"] if line.startswith("-- Each common table expression stands for one step") else line
            for line in kept]
    lines = header[:2] + [f"-- {line}" for line in textwrap_lines(RESTRUCTURE_WORDING["starts"])] + kept
    text = "\n".join(lines) + "\n" + (blanked(body) if blank else body)
    return {"sql": _finish_lines(text) + "\n", "restructured": True, "reason": "", "lines": len(text.splitlines())}


def textwrap_lines(sentence, width=110):
    import textwrap
    return textwrap.wrap(sentence, width)


def _first_of(step, node, origins):
    """For a comparison of ROW_NUMBER() with 1, the source columns it partitions and orders by, as two phrases, or None."""
    if not isinstance(node, exp.EQ):
        return None
    for one, other in ((node.this, node.expression), (node.expression, node.this)):
        if not (isinstance(one, exp.Column) and one.table and _literal(other) == "1"):
            continue
        select = one.find_ancestor(exp.Select)
        for found, _ in _own_tables(select) if select is not None else []:
            if not (isinstance(found, exp.Subquery) and found.alias_or_name.upper() == one.table.upper()):
                continue
            for branch in _branches(found.this):
                for projection in branch.expressions:
                    window = projection.unalias()
                    if projection.alias_or_name.upper() != one.name.upper() or not isinstance(window, exp.Window) \
                            or not isinstance(window.this, exp.RowNumber):
                        continue
                    named = lambda column: register._join(f"{t}.{c}" for t, c in origins(step, column))  # noqa: E731
                    partition = register._join(named(c) for c in window.args.get("partition_by") or [] if isinstance(c, exp.Column))
                    ordered = []
                    for item in (window.args.get("order").expressions if window.args.get("order") else []):
                        column = item.this if isinstance(item, exp.Ordered) else item
                        if isinstance(column, exp.Column) and named(column):
                            text = named(column)
                            ordered.append(DRAFT_WORDING["descending"].format(column=text) if item.args.get("desc") else text)
                    if partition and ordered:
                        return partition, register._join(ordered)
    return None


def _re_digits(value):
    text = str(value).strip()
    return text.lstrip("-").isdigit()


def result_csv(result):
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(result["columns"])
    writer.writerows([["" if value is None else value for value in row] for row in result["rows"]])
    return out.getvalue()


# The review of a target query against the data dictionary.

# The wording of the review's findings, which the clinical lead, the analysts and an LLM read. It awaits the clinical lead's approval.
REVIEW_WORDING = {
    "unknown_table": "The query reads {table}, which is neither a table of CDM 5.4 nor a custom table of the conversion.",
    "table": "The query reads {table}, which the data dictionary does not describe, because neither the anaesthesia layer nor the derived layer writes it.",
    "not_cdm": "The query reads {table}.{field}, which is not a field of {table} in CDM 5.4.",
    "custom": "The query reads {table}.{field}, which tables.json does not define for the custom table {table}.",
    "field": "The query reads {table}.{field}, which no step of the conversion writes, so the field is empty in every row that the dictionary describes.",
    "concept_never": "The query compares {table}.{field} with the concept {concept}, which the conversion never writes in that field or in any other, so the comparison finds no rows.",
    "concept_elsewhere": "The query compares {table}.{field} with the concept {concept}{named}, which the conversion writes only in {where}, so the comparison finds no rows in {table}.",
    "named": " ({name}, a concept of the {domain} domain)",
    "link": "The query relates {table} to {other} through {fields}, and a row of {table} belongs to an anaesthetic only through the documented links, which are {links}.",
    "units": "The query compares {table}.{field} with a number, and the conversion writes that field in more than one unit ({units}) for the rows that the query keeps, so the comparison mixes values in different units unless the query also restricts {table}.unit_concept_id.",
    "no_unit": "no unit",
    # Advice, which does not stop the query.
    "advise_people": "The query counts records, and it could also count people, as COUNT(DISTINCT person_id), so that a child with more than one record is counted once. This is advice, and it does not stop the query.",
    "advise_rows": "The query returns a row for each record, so its result holds patient-level data and belongs under the approval of the audit itself. This is advice, and it does not stop the query.",
}
# What the review says when it finds nothing, which is not a statement that the query is right.
REVIEW_EMPTY = "The review found nothing in the query that the data dictionary contradicts, which does not mean that the query is right."
# The tables whose rows stand for an anaesthetic, to which a measurement or an observation is linked.
ANAESTHETIC_SIDE = ("visit_detail", "procedure_occurrence")
READING_TABLES = {"measurement": ("measurement_concept_id", "value_as_number"),
                  "observation": ("observation_concept_id", "value_as_number")}


def _scope_tables(select, tree):
    """Each alias that a SELECT, or a SELECT around it, reads: alias -> OMOP table, or alias -> {column: (table, field)} for a derived table."""
    found = {}
    scope = select
    while scope is not None:
        for node, _ in _own_tables(scope):
            alias = node.alias_or_name.upper()
            if alias in found:
                continue
            if _is_omop(node):
                found[alias] = node.name.lower()
            elif isinstance(node, exp.Subquery):
                carried = {}
                for branch in _branches(node.this):
                    inner = _scope_tables(branch, tree)
                    for projection in branch.expressions:
                        column = _passed_through(projection.unalias())
                        if isinstance(column, exp.Column) and column.table and isinstance(inner.get(column.table.upper()), str):
                            carried.setdefault(projection.alias_or_name.lower(), (inner[column.table.upper()], column.name.lower()))
                found[alias] = carried
        scope = scope.find_ancestor(exp.Select)
    return found


def _resolve(column, aliases, fields):
    """(alias, table, field) for a column of the query, or None where it reads no OMOP table."""
    if column.table:
        held = aliases.get(column.table.upper())
        if isinstance(held, str):
            return column.table.upper(), held, column.name.lower()
        if isinstance(held, dict) and column.name.lower() in held:
            return (column.table.upper(),) + held[column.name.lower()]
        return None
    holders = [(alias, table) for alias, table in aliases.items() if isinstance(table, str)
               and column.name.lower() in {f for f, _, _ in fields.get(table, [])}]
    return (holders[0][0], holders[0][1], column.name.lower()) if len(holders) == 1 else None


def review(query, dictionary, advice=False):
    """Checks a target query against the data dictionary of its conversion, and returns the findings as plain sentences.

    dictionary is what dictionary.build returns, or the path of a dictionary.json. Each finding is
    one sentence that names what the query does and why the conversion does not support it:

    - a concept compared with a concept field in which the conversion never writes it;
    - a concept compared with a field of the wrong table or domain, because the conversion writes it elsewhere;
    - a measurement or an observation related to an anaesthetic by anything other than the documented links;
    - a value field compared with a number where the rows that the query keeps hold more than one unit;
    - a table or a field that the conversion does not write;
    - a field of a custom table that tables.json does not define.

    With advice, two findings that advise and do not block follow: the query counts records where it could
    also count people, and the query returns a row for each record.

    An empty list means only that the review found none of these. It does not mean that the query
    is right: the planted scenarios, the same answer on two engines and the clinical lead's reading
    of the query's restatement remain the tests of that. Raises TargetError for anything but one SELECT.
    """
    if not isinstance(dictionary, dict):
        from . import dictionary as dictionaries
        dictionary = dictionaries.load(dictionary)
    w = REVIEW_WORDING
    try:
        trees = [tree for tree in sqlglot.parse(query, dialect="tsql") if tree is not None]
    except sqlglot.errors.SqlglotError:
        raise TargetError("the target query cannot be read as SQL") from None
    if len(trees) != 1 or not isinstance(trees[0], (exp.Select, exp.SetOperation)):
        raise TargetError("the target query must be one SELECT")
    tree = trees[0]
    tables = dictionary["tables"]
    cdm = convert.cdm_fields()
    fields = dict(cdm, **{name: [(f, False, "") for f in entry["fields"]]
                          for name, entry in tables.items() if entry["kind"] == "custom"})
    concepts = dictionary["concepts"]
    identifiers = dictionary["identifiers"]
    anaesthetic_roots = set(dictionary["anaesthetic_roots"])
    findings = []

    def add(text):
        if text not in findings:
            findings.append(text)

    named_ctes = {cte.alias.upper() for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        if not table.db and table.name.upper() in named_ctes:
            continue
        name = table.name.lower()
        if name not in tables:
            add(w["table"].format(table=name) if _is_omop(table) and name in cdm else w["unknown_table"].format(table=name))

    def roots(table, field):
        return set(identifiers.get(f"{table}.{field}", [f"{table}.{field}"]))

    restrictions = {}       # (alias, field) -> {value}
    for select in tree.find_all(exp.Select):
        aliases = _scope_tables(select, tree)
        own = [column for column in select.find_all(exp.Column) if column.find_ancestor(exp.Select) is select]
        # Tables and fields that the conversion does not write.
        for column in own:
            found = _resolve(column, aliases, fields)
            if found is None or found[1] not in tables:
                continue
            _, table, field = found
            entry = tables[table]
            if field in entry["fields"]:
                continue
            if entry["kind"] == "custom":
                add(w["custom"].format(table=table, field=field))
            elif field in {f for f, _, _ in cdm.get(table, [])}:
                add(w["field"].format(table=table, field=field))
            else:
                add(w["not_cdm"].format(table=table, field=field))
        # Concepts and other fixed values compared with a field.
        for node in select.find_all(exp.EQ, exp.In):
            if node.find_ancestor(exp.Select) is not select:
                continue
            if isinstance(node, exp.In):
                if node.args.get("query"):
                    continue
                sides = [(node.this, node.expressions)]
            else:
                sides = [(node.this, [node.expression]), (node.expression, [node.this])]
            for one, others in sides:
                column = _passed_through(one)
                values = [_literal(other) for other in others]
                if not isinstance(column, exp.Column) or not values or any(v is None for v in values):
                    continue
                found = _resolve(column, aliases, fields)
                if found is None or found[1] not in tables:
                    continue
                alias, table, field = found
                restrictions.setdefault((alias, field), set()).update(_norm(v) for v in values)
                info = tables[table]["fields"].get(field)
                if not field.endswith("_concept_id") or info is None or info.get("open"):
                    continue
                for value in (_norm(v) for v in values):
                    if value in info.get("concepts", []):
                        continue
                    about = concepts.get(value)
                    if about and about["written_in"]:
                        named = w["named"].format(name=about["name"], domain=about["domain"]) if about.get("name") else ""
                        add(w["concept_elsewhere"].format(table=table, field=field, concept=value, named=named,
                                                          where=_join(about["written_in"])))
                    else:
                        add(w["concept_never"].format(table=table, field=field, concept=value))
        # How a measurement or an observation is related to an anaesthetic.
        related, linked = {}, set()
        for node in select.find_all(exp.EQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Between):
            if node.find_ancestor(exp.Select) is not select:
                continue
            resolved = [r for r in (_resolve(c, aliases, fields) for c in node.find_all(exp.Column)) if r is not None]
            readings = [r for r in resolved if r[1] in READING_TABLES]
            others = [r for r in resolved if r[1] in ANAESTHETIC_SIDE or tables.get(r[1], {}).get("kind") == "custom"]
            for reading in readings:
                for other in others:
                    if other[0] == reading[0]:
                        continue
                    related.setdefault(reading[0], (reading[1], other[1], []))
                    if reading[2] not in related[reading[0]][2]:
                        related[reading[0]][2].append(reading[2])
                    if isinstance(node, exp.EQ) and roots(reading[1], reading[2]) & roots(other[1], other[2]) & anaesthetic_roots:
                        linked.add(reading[0])
        for alias, (table, other, used) in related.items():
            if alias in linked:
                continue
            documented = sorted(f"{name}" for name, found in identifiers.items()
                                if name.startswith(f"{table}.") and set(found) & anaesthetic_roots)
            add(w["link"].format(table=table, other=other, fields=_join(used), links=_join(documented)))
    # A value compared with a number where the rows that the query keeps mix units.
    units = dictionary.get("units", {})
    for select in tree.find_all(exp.Select):
        aliases = _scope_tables(select, tree)
        for node in select.find_all(exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ, exp.NEQ, exp.Between):
            if node.find_ancestor(exp.Select) is not select:
                continue
            columns = list(node.find_all(exp.Column))
            numbers = [n for n in node.iter_expressions() if isinstance(n, exp.Neg) or (isinstance(n, exp.Literal) and not n.is_string)]
            if len(columns) != 1 or not numbers:
                continue
            found = _resolve(columns[0], aliases, fields)
            if found is None or found[1] not in units or found[2] != units[found[1]]["value_field"]:
                continue
            alias, table, field = found
            if (alias, "unit_concept_id") in restrictions:
                continue
            by_concept = units[table]["by_concept"]
            chosen = restrictions.get((alias, units[table]["concept_field"]))
            held = {u for concept, found_units in by_concept.items() if chosen is None or concept in chosen for u in found_units}
            if len(held) > 1:
                named = []
                for unit in sorted(held, key=lambda u: (len(u), u)):
                    about = concepts.get(unit, {})
                    named.append(w["no_unit"] if unit == "none" else f"{unit} {about['name']}" if about.get("name") else unit)
                add(w["units"].format(table=table, field=field, units=_join(named)))
    # Two findings that advise and do not block: whether the query also counts people, and whether it returns
    # a row for each record.
    tree = trees[0]
    final = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    if not advice:
        return findings
    counted = count_columns(tree)
    people = any(isinstance(p.unalias(), exp.Count) and isinstance(p.unalias().this, exp.Distinct)
                 and any(c.name.lower() == "person_id" for c in p.unalias().this.find_all(exp.Column))
                 for p in final.expressions)
    if counted and not people:
        add(w["advise_people"])
    group = final.args.get("group")
    grouped = {g.sql(dialect="tsql") for g in group.expressions} if group else set()
    rows_each = [p for p in final.expressions if p.unalias().sql(dialect="tsql") not in grouped
                 and not isinstance(p.unalias(), exp.Literal) and not p.unalias().find(exp.AggFunc)]
    if rows_each and not group:
        add(w["advise_rows"])
    return findings


def main():
    parser = argparse.ArgumentParser(prog="schemalyser.target")
    parser.add_argument("world", type=Path)
    parser.add_argument("conversion", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("--checks", type=Path, help="a check results file")
    parser.add_argument("--profile", type=Path, help="the saved result of the core profile script")
    parser.add_argument("--run", action="store_true", help="run the conversion and then the query on the synthetic rows")
    parser.add_argument("--draft", action="store_true",
                        help="write source_draft.sql, the same question as one query against the source tables alone; "
                             "it names the hospital's tables and local codes, so it is for use inside the hospital only")
    parser.add_argument("--review", action="store_true",
                        help="review the query against the data dictionary of the conversion, and write review.txt")
    parser.add_argument("--specification", action="store_true",
                        help="write specification.txt, one page for a person who writes the query himself; it names the "
                             "hospital's tables and local codes, so it is for use inside the hospital only")
    parser.add_argument("--facts", type=Path, help="facts.json, the facts that a person confirmed")
    parser.add_argument("--check-query", type=Path,
                        help="a query written by hand over the source tables, to run on the synthetic rows beside the "
                             "target's answer; only the synthetic results are shown, and nothing of the query is written")
    parser.add_argument("--rows", type=int, default=500)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    world = harness.World.from_folder(args.world)
    sql = decode(args.target.read_bytes())
    checks_text = decode(args.checks.read_bytes()) if args.checks else None
    try:
        rows, traced = checklist(world, args.conversion, sql, checks_text,
                                 decode(args.profile.read_bytes()) if args.profile else None,
                                 decode(args.facts.read_bytes()) if args.facts else None, args.target.stem)
        checked = (check_query(world, args.conversion, sql, decode(args.check_query.read_bytes()), args.rows)
                   if args.check_query else None)
        if args.specification:
            from .catalogue import Catalogue
            spec = specification(args.conversion, sql, rows, traced, Catalogue.from_csv(world.catalogue_text()), args.target.stem)
        result = run(world, args.conversion, sql, args.rows, checks_text) if args.run else None
        if args.review:
            from . import dictionary as dictionaries
            try:
                findings = review(sql, dictionaries.build(args.conversion), advice=True)
            except dictionaries.DictionaryError as error:
                raise TargetError(str(error)) from None
        if args.draft:
            from .catalogue import Catalogue
            draft = source_draft(args.conversion, sql, Catalogue.from_csv(world.catalogue_text()), target_name=args.target.name)
    except TargetError as error:
        raise SystemExit(f"schemalyser.target: {error}")
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "checklist.csv").write_text(to_csv(rows), encoding="utf-8")
    text = readiness(rows, traced, result["scenarios"] if result is not None else None)
    (args.out / "readiness.txt").write_text(text, encoding="utf-8")
    print(text, end="")
    if args.review:
        (args.out / "review.txt").write_text("".join(f"{line}\n" for line in findings) or REVIEW_EMPTY + "\n", encoding="utf-8")
        print(*(findings or [REVIEW_EMPTY]), sep="\n")
    if args.draft:
        (args.out / "source_draft.sql").write_text(draft, encoding="utf-8")
    if args.specification:
        (args.out / "specification.txt").write_text(spec, encoding="utf-8")
    if checked is not None:
        # Only the synthetic results are shown; nothing of the hand-written query is written anywhere.
        print(CHECK_WORDING["agree"] if checked["agree"] else CHECK_WORDING["differ"])
        print(CHECK_WORDING["columns"].format(columns=", ".join(checked["columns"])))
        for label, found in (("target", checked["target"]), ("query", checked["query"])):
            print(CHECK_WORDING[label])
            for row in found:
                print("  " + ", ".join("" if v is None else v for v in row))
        if checked["differs"]:
            print(CHECK_WORDING["differs"])
            for side, row in checked["differs"]:
                print(f"  {CHECK_WORDING['only_' + side]}: " + ", ".join("" if v is None else v for v in row))
            if checked["scenarios"]:
                print(CHECK_WORDING["scenarios"].format(names=", ".join(checked["scenarios"])))
    if result is not None:
        (args.out / "result.csv").write_text(result_csv(result), encoding="utf-8")
        (args.out / "published.sql").write_text(result["published"] + "\n", encoding="utf-8")
        for failure in result["failures"]:
            print("conversion:", failure)


if __name__ == "__main__":
    main()
