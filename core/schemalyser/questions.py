"""The open questions about the real hospital, with the evidence that would settle each one.

Every stage of the pipeline rests on something that the project cannot see: the real catalogue,
the real check results, the real timings, the real mapping rows and the real core OMOP database.
This module lists each such question in one place, says what it decides and exactly what evidence
would settle it, who can supply that evidence and through which mechanism, and then works out from
the inputs to hand how far each question is answered.

    python -m schemalyser.questions WORLD CONVERSION [--checks FILE] [--profile FILE] [--build] --out questions.csv

WORLD is a world folder, as the harness reads it. The register of questions and all of its wording
are fixed below, in WORDING, so that the wording can be reviewed in one place. Every status and
every statement of the evidence in hand is computed. The evidence is stated as counts and names,
and the names come only from the catalogue, the site rules, the OMOP standard, the conversion's own
steps and settings, and the fixed register. No value from the check results is ever written, and
nothing from the text of a request.
"""
import argparse
import csv
import io
import json
from collections import Counter
from pathlib import Path

import sqlglot
from sqlglot import exp

from . import checks as checking
from . import convert
from . import harness
from . import profile as core_profile
from . import tuning as tunable
from . import vocabulary as v
from .catalogue import Catalogue
from .extract import _passed_through, decode
from .translate import OMOP_SCHEMA

LAYOUT = ("question_id", "stage", "question", "decides", "currently_from", "evidence_needed", "who", "mechanism",
          "status", "evidence_in_hand")
STAGES = {"A": "the SQL analyser", "B": "the shadow database", "C": "the core layer",
          "D": "the anaesthesia layer", "E": "the release"}
STATUSES = ("answered", "partly", "open")
WHO = ("analytics team", "central OMOP team", "clinical lead")
MECHANISMS = ("catalogue export", "check script", "site rules", "mapping rows", "core profile script",
              "release settings", "a decision")
# Where the current answer comes from. "catalogue export" and "release settings" are added to the
# list that the plan gives, because a catalogue and a release.json written at the hospital are
# real evidence that fits none of the other sources.
SOURCES = ("invented default", "public source", "site rules", "check results", "mapping rows", "core profile",
           "catalogue export", "release settings", "a guess")
CHECK_KINDS = ("column", "rows", "values", "years", "spans")

# The register. Each entry gives the fixed wording of one question, or of a family of questions
# with a placeholder for a name. It is a draft until the clinical lead approves it.
WORDING = {
    # Stage A: the SQL analyser.
    "A-catalogue": {
        "question": "The tables and columns of the real database are known only from the catalogue file that the analytics team exports.",
        "decides": "Which tables and columns the analysis can recognise, and which ones the check script and the sandbox can name.",
        "evidence_needed": "The analytics team runs the catalogue query against the reporting database and supplies the whole result as the catalogue file.",
        "who": "analytics team", "mechanism": "catalogue export"},
    "A-unread-parse_error": {
        "question": "Parts of some requests could not be read as SQL, so the tables and columns that those parts use are not yet known.",
        "decides": "Whether the inventory, the checks and the sandbox cover every table and column that past requests use.",
        "evidence_needed": "The analytics team reviews each request that the page lists as not fully read, and corrects or removes the parts that are not SQL.",
        "who": "analytics team", "mechanism": "a decision"},
    "A-unread-dynamic_sql": {
        "question": "Some requests build their SQL as text when they run, so the tables inside that SQL are not yet known.",
        "decides": "Whether the inventory, the checks and the sandbox cover the tables that those requests read.",
        "evidence_needed": "The analytics team supplies, for each such request, a plain copy of the SQL that it builds, so that the analysis can read it.",
        "who": "analytics team", "mechanism": "a decision"},
    "A-unread-opaque_statement": {
        "question": "Some requests hold statements of a kind that Schemalyser does not analyse, so what those statements read is not yet known.",
        "decides": "Whether the inventory, the checks and the sandbox cover the tables that those statements read.",
        "evidence_needed": "The analytics team confirms whether those statements read any table that the inventory does not show.",
        "who": "analytics team", "mechanism": "a decision"},
    "A-unread-qualify_error": {
        "question": "In some requests the columns could not be matched to their tables, so the use of those columns is not yet known.",
        "decides": "Whether the inventory counts every use of those columns.",
        "evidence_needed": "The analytics team checks that the catalogue includes every table that those requests read, and supplies a fuller export where it does not.",
        "who": "analytics team", "mechanism": "catalogue export"},
    "A-unread-table_not_in_catalogue": {
        "question": "Some requests refer to tables that are not in the catalogue, so those tables are not yet known.",
        "decides": "Whether the inventory, the checks and the sandbox include those tables.",
        "evidence_needed": "The analytics team exports the catalogue again to include those tables, or confirms that they are temporary tables that the requests make for themselves.",
        "who": "analytics team", "mechanism": "catalogue export"},
    "A-unread-local_table_held_back": {
        "question": "Some requests refer to tables that the site rules mark as built locally, and the analysis leaves those tables out.",
        "decides": "Whether the inventory leaves out any table that the conversion needs.",
        "evidence_needed": "The clinical lead reviews the local table patterns in the site rules, and removes any pattern that holds back a table that matters.",
        "who": "clinical lead", "mechanism": "site rules"},
    "A-unread-column_not_attributed": {
        "question": "Some columns could not be attributed to a single table, so their use is not yet known.",
        "decides": "Whether the inventory counts every use of those columns.",
        "evidence_needed": "The analytics team checks that the catalogue includes every table that those requests read, and supplies a fuller export where it does not.",
        "who": "analytics team", "mechanism": "catalogue export"},
    "A-unread-derivation_withheld": {
        "question": "Some computed expressions could not be rewritten safely and were left out, so how those requests derive their values is not yet known.",
        "decides": "Whether the inventory shows every column that a computed value depends on.",
        "evidence_needed": "The analytics team reviews those expressions by hand, and tells the clinical lead which columns they derive from.",
        "who": "analytics team", "mechanism": "a decision"},
    "A-unread-other": {
        "question": "Some parts of the requests could not be analysed, for a reason the analysis records as {kind}.",
        "decides": "Whether the inventory covers every table and column that past requests use.",
        "evidence_needed": "The analytics team reviews the requests that the page lists as not fully read.",
        "who": "analytics team", "mechanism": "a decision"},
    "A-checks-column": {
        "question": "For the columns that the requests join on, the number of rows, distinct values and empty values in the real database is not yet known.",
        "decides": "How many rows the sandbox builds for each table, and whether it treats a key as unique.",
        "evidence_needed": "The analytics team runs the check script and returns the results file unchanged.",
        "who": "analytics team", "mechanism": "check script"},
    "A-checks-rows": {
        "question": "The number of rows in each table that the requests use is not yet known.",
        "decides": "How many rows the sandbox builds for each table that has no column check.",
        "evidence_needed": "The analytics team runs the check script and returns the results file unchanged.",
        "who": "analytics team", "mechanism": "check script"},
    "A-checks-values": {
        "question": "The values that the requests compare columns with, and the values that the conversion maps, are not yet known for the real database.",
        "decides": "Which codes the sandbox holds, which values the inventory confirms, and which values need a mapping row.",
        "evidence_needed": "The analytics team runs the check script and returns the results file unchanged.",
        "who": "analytics team", "mechanism": "check script"},
    "A-checks-years": {
        "question": "How the rows of each date column that the requests use fall across the years is not yet known.",
        "decides": "The years in which the sandbox places its dates.",
        "evidence_needed": "The analytics team runs the check script and returns the results file unchanged.",
        "who": "analytics team", "mechanism": "check script"},
    "A-checks-spans": {
        "question": "How long the real intervals between paired date columns last is not yet known, including the length of an anaesthetic.",
        "decides": "The durations and intervals that the sandbox draws, in place of the invented defaults.",
        "evidence_needed": "The analytics team runs the check script with the spans checks included, and returns the results file unchanged.",
        "who": "analytics team", "mechanism": "check script"},
    "A-check-errors": {
        "question": "Some checks may not have been answered by the real database, and the reason for each is not yet known.",
        "decides": "Whether the sandbox lacks the counts or values that those checks would have given.",
        "evidence_needed": "The analytics team looks up the error number of each error row in the results, and tells the project which tables or columns could not be read.",
        "who": "analytics team", "mechanism": "check script"},

    # Stage B: the shadow database. The tunable parameters are grouped by what they shape.
    "B-tuning-ages": {
        "question": "How old the children are at their anaesthetics is not yet known from real data.",
        "decides": "The ages that the sandbox gives its subjects at their first anaesthetic.",
        "evidence_needed": "The clinical lead gives the youngest and the oldest age, and how strongly ages lean young, under tuning in the site rules, for example from the hospital's published activity figures.",
        "who": "clinical lead", "mechanism": "site rules"},
    "B-tuning-anaesthetic_duration": {
        "question": "How long anaesthetics last is not yet known from real data.",
        "decides": "The durations of the sandbox's anaesthetics, and so where every timed value falls.",
        "evidence_needed": "The analytics team runs the spans check from {first} to {second}, which counts the anaesthetics by their length in fixed bands.",
        "evidence_needed_without_roles": "The clinical lead gives the start and the stop of the anaesthetic their roles in the site rules, and the analytics team then runs the spans check between those two columns.",
        "who": "analytics team", "mechanism": "check script"},
    "B-tuning-induction": {
        "question": "What share of doses is given at induction, and how long induction lasts, is not yet known from real data.",
        "decides": "When the sandbox places each dose within its anaesthetic.",
        "evidence_needed": "The clinical lead gives the share of doses given at induction and the length of induction under tuning in the site rules.",
        "who": "clinical lead", "mechanism": "site rules"},
    "B-tuning-placement": {
        "question": "How soon after the start of an anaesthetic an airway or a line is placed is not yet known from real data.",
        "decides": "When the sandbox places each airway and line.",
        "evidence_needed": "The clinical lead gives the longest usual time from the start of the anaesthetic to placement under tuning in the site rules.",
        "who": "clinical lead", "mechanism": "site rules"},
    "B-tuning-removal": {
        "question": "How long an airway or a line stays in place is not yet known from real data.",
        "decides": "When the sandbox removes each airway and line.",
        "evidence_needed": "The analytics team runs the spans check from {first} to {second}, which counts the airways and lines by the time that each stayed in place.",
        "evidence_needed_without_roles": "The clinical lead gives the placement time and the removal time of an airway or a line their roles in the site rules, and the analytics team then runs the spans check between those two columns.",
        "who": "analytics team", "mechanism": "check script"},
    "B-tuning-still_in_place": {
        "question": "What share of airways and lines is recorded as still in place, with the site's sentinel date as the removal time, is not yet known.",
        "decides": "How many airways and lines in the sandbox carry the sentinel date as their removal time.",
        "evidence_needed": "The clinical lead gives the sentinel date under sentinelValues in the site rules, and the analytics team counts the rows of {column} that hold that date, from a years check on that column, against the rows of the table.",
        "evidence_needed_without_roles": "The clinical lead gives the sentinel date under sentinelValues and the removal time its role in the site rules, and the analytics team then counts the rows that hold that date, from a years check on that column, against the rows of the table.",
        "who": "analytics team", "mechanism": "check script"},
    "B-tuning-measurement_before": {
        "question": "How long before an anaesthetic a weight or a height is taken is not yet known from real data.",
        "decides": "When the sandbox places weights and heights.",
        "evidence_needed": "The clinical lead gives the least and the most usual time before the anaesthetic under tuning in the site rules.",
        "who": "clinical lead", "mechanism": "site rules"},
    "B-tuning-admission": {
        "question": "How long before an anaesthetic a child is admitted is not yet known from real data.",
        "decides": "When the sandbox places each admission.",
        "evidence_needed": "The clinical lead gives the least and the most usual time from admission to the anaesthetic under tuning in the site rules.",
        "who": "clinical lead", "mechanism": "site rules"},
    "B-tuning-discharge": {
        "question": "How long after an anaesthetic a child is discharged is not yet known from real data.",
        "decides": "When the sandbox places each discharge.",
        "evidence_needed": "The analytics team runs the spans check from {first} to {second}, which counts the hospital visits by their length.",
        "evidence_needed_without_roles": "The clinical lead gives the admission time and the discharge time their roles in the site rules, and the analytics team then runs the spans check between those two columns.",
        "who": "analytics team", "mechanism": "check script"},
    "B-tuning-death_share": {
        "question": "The share of children who have a date of death is not yet known from real data.",
        "decides": "How many subjects in the sandbox have a date of death.",
        "evidence_needed": "The analytics team counts the rows of {column} that hold a date, with a column check, and the share follows from the rows of the table.",
        "evidence_needed_without_roles": "The clinical lead gives the date of death its role in the site rules, and the analytics team then counts the rows that hold a date, with a column check on that column.",
        "who": "analytics team", "mechanism": "check script"},
    "B-tuning-death_timing": {
        "question": "For the children who die, the time from their last event to their death is not yet known from real data.",
        "decides": "When the sandbox places each date of death.",
        "evidence_needed": "The clinical lead gives the least and the most usual time under tuning in the site rules.",
        "who": "clinical lead", "mechanism": "site rules"},
    "B-tuning-end_tidal_co2": {
        "question": "The usual range of end-tidal carbon dioxide under anaesthesia at this hospital is not yet known from real data.",
        "decides": "The end-tidal carbon dioxide values in the sandbox.",
        "evidence_needed": "The clinical lead gives the usual value, its spread and its limits under tuning in the site rules.",
        "who": "clinical lead", "mechanism": "site rules"},
    "B-tuning-end_tidal_agent": {
        "question": "The usual range of end-tidal volatile agent under anaesthesia at this hospital is not yet known from real data.",
        "decides": "The end-tidal agent values in the sandbox.",
        "evidence_needed": "The clinical lead gives the usual value, its spread and its limits under tuning in the site rules.",
        "who": "clinical lead", "mechanism": "site rules"},
    "B-tuning-oxygen_saturation": {
        "question": "The usual range of oxygen saturation under anaesthesia at this hospital is not yet known from real data.",
        "decides": "The oxygen saturation values in the sandbox.",
        "evidence_needed": "The clinical lead gives the lowest and the highest usual value under tuning in the site rules.",
        "who": "clinical lead", "mechanism": "site rules"},
    "B-tuning-temperature": {
        "question": "The usual range of temperature under anaesthesia at this hospital is not yet known from real data.",
        "decides": "The temperatures in the sandbox.",
        "evidence_needed": "The clinical lead gives the lowest and the highest usual value under tuning in the site rules.",
        "who": "clinical lead", "mechanism": "site rules"},
    "B-tuning-pressure_fallback": {
        "question": "The blood pressures to use where the public reference file is missing, or gives no spread, have not been confirmed.",
        "decides": "The blood pressures in the sandbox, but only where the public reference file is missing or gives no spread.",
        "evidence_needed": "The clinical lead confirms or replaces these values under tuning in the site rules.",
        "who": "clinical lead", "mechanism": "site rules"},
    "B-tuning-mean_pressure": {
        "question": "How many anaesthetics at this hospital have an arterial line, how often its mean pressure is charted, and the range of mean pressures under anaesthesia are not yet known from real data.",
        "decides": "Which anaesthetics in the sandbox have a mean pressure charted every few minutes from an arterial line, and the mean pressures that the sandbox writes.",
        "evidence_needed": "The clinical lead gives the share of anaesthetics with an arterial line, the interval at which its mean pressure is charted, and the lowest and the highest mean pressure under tuning in the site rules.",
        "who": "clinical lead", "mechanism": "site rules"},
    "B-tuning-other": {
        "question": "The value of the tunable parameter {key} is not yet known from real data.",
        "decides": "{description}",
        "evidence_needed": "The clinical lead gives a value under tuning in the site rules.",
        "who": "clinical lead", "mechanism": "site rules"},
    "B-roles-unused": {
        "question": "Some roles in the site rules may name a column that is not in the catalogue, or a meaning that the tool does not know, and the sandbox cannot use those roles.",
        "decides": "Which columns the sandbox fills with realistic values rather than filler.",
        "evidence_needed": "The clinical lead corrects each such role in the site rules, so that it names a catalogue column and a meaning from the fixed list.",
        "who": "clinical lead", "mechanism": "site rules"},
    "B-role": {
        "question": "The sandbox could not apply the role {role} to {table}.{column}, so that column still holds filler.",
        "decides": "Whether the sandbox's values in that column look like the real ones.",
        "evidence_needed_prerequisite": "The clinical lead adds to the site rules what this role depends on, such as the start and the stop of the anaesthetic, or the values that mean male and female.",
        "evidence_needed_database": "The clinical lead checks that the column's type in the catalogue suits the role, and corrects the role in the site rules.",
        "evidence_needed_reference data": "The clinical lead asks for the public reference file that this role draws on to be restored.",
        "evidence_needed": "The clinical lead reviews the role in the site rules.",
        "who": "clinical lead", "mechanism": "site rules"},
    "B-repeat-anaesthetics": {
        "question": "How many anaesthetics each child has is not yet known from real data.",
        "decides": "Whether the sandbox gives some children more than one anaesthetic, and so whether the anaesthesia layer's joins are tested against children with several.",
        "evidence_needed": "A check that counts the subjects by their number of anaesthetics would settle this. That check is planned and does not exist yet, so the analytics team can run it only once it is added to the check script.",
        "who": "analytics team", "mechanism": "check script"},

    # Stage C: the guesses about the real core.
    "C-cdm-version": {
        "question": "The CDM version of the real core is not yet known, and the anaesthesia layer assumes version 5.4.",
        "decides": "Which fields the anaesthesia steps may write, including the event fields and the procedure end time.",
        "evidence_needed": "The central OMOP team runs the core profile script and returns its result, which reports the version that CDM_SOURCE records and any CDM 5.4 fields that the core lacks.",
        "who": "central OMOP team", "mechanism": "core profile script"},
    "C-source-value": {
        "question": "Whether {field} in the real core holds the source system's keys as text is not yet known.",
        "decides": "Whether the anaesthesia steps that join on this field find their rows in the core. Every anaesthesia row depends on these joins, and this is the guess most likely to be wrong.",
        "evidence_needed": "The central OMOP team runs the core profile script with the source prefix set, so that it counts, for each join, how many distinct source keys the core holds.",
        "who": "central OMOP team", "mechanism": "core profile script"},
    "C-domain": {
        "question": "Whether the real core already loads rows into {table} that the anaesthesia layer would add again is not yet known.",
        "decides": "Whether the anaesthesia layer writes to {table} or leaves it to the core, and so whether any rows are counted twice.",
        "evidence_needed": "The central OMOP team runs the core profile script, which counts the core's rows in {table} by type concept.",
        "who": "central OMOP team", "mechanism": "core profile script"},
    "C-visit-detail": {
        "question": "Whether the real core populates VISIT_DETAIL is not yet known, and the anaesthesia layer assumes that it does not.",
        "decides": "Whether the anaesthesia layer writes its own visit detail for each anaesthetic, or joins to the core's.",
        "evidence_needed": "The central OMOP team runs the core profile script, which counts the rows of the core's VISIT_DETAIL.",
        "who": "central OMOP team", "mechanism": "core profile script"},
    "C-observation-periods": {
        "question": "Whether the real core's observation periods cover every anaesthetic is not yet known.",
        "decides": "Whether ATLAS can see every anaesthetic, and whether the quality gate on observation periods passes.",
        "evidence_needed": "The central OMOP team runs the core profile script, which counts the people who have an observation period. The quality gate on observation periods then confirms, at the first release, that each anaesthetic falls inside one.",
        "who": "central OMOP team", "mechanism": "core profile script"},

    # Stage D: the anaesthesia layer's mapping rows.
    "D-mapping": {
        "question": "Whether every value of {columns} that matters has a mapping row under {vocabulary} is not yet known.",
        "decides": "Which source values the anaesthesia steps keep and which concepts they are written with. A value without a mapping row is left out or written with the concept 0.",
        "evidence_needed": "The check results list the values of {columns}, and the clinical lead writes a mapping row under {vocabulary} for each value that should be kept, or confirms that the value is meant to be left out.",
        "who": "clinical lead", "mechanism": "mapping rows"},
    "D-mapping-constant": {
        "question": "The mapping row under {vocabulary}, for the code that the step supplies itself, has not yet been confirmed.",
        "decides": "The concept that the anaesthesia steps write for that code.",
        "evidence_needed": "The clinical lead confirms the mapping row under {vocabulary}, and its concept.",
        "who": "clinical lead", "mechanism": "mapping rows"},

    # Stage E: the release settings and decisions.
    "E-setting-omop_schema": {
        "question": "Where the real core's tables are held has not yet been confirmed.",
        "decides": "The omop_schema setting, through which the release script and the core profile reach the core tables.",
        "evidence_needed": "The central OMOP team names the schema, and the core profile script, run with that schema, confirms that the CDM tables are there.",
        "who": "central OMOP team", "mechanism": "core profile script"},
    "E-setting-source_prefix": {
        "question": "How the release reaches the source tables from the OMOP database is not yet known.",
        "decides": "The source_prefix setting, through which every anaesthesia step reads its source tables.",
        "evidence_needed": "The central OMOP team states where the source tables can be read from the OMOP database, and the core profile script, run with that prefix, confirms that it reaches them.",
        "who": "central OMOP team", "mechanism": "core profile script"},
    "E-setting-identifier_type": {
        "question": "The type of the real core's identifiers, and how much room they leave for the anaesthesia rows, is not yet known.",
        "decides": "The identifier_type setting of the release, and whether the anaesthesia rows can be numbered on from the core's highest key.",
        "evidence_needed": "The central OMOP team runs the core profile script, which reports the type of each primary key and the number of digits in the highest key.",
        "who": "central OMOP team", "mechanism": "core profile script"},
    "E-setting-anaesthesia_schema": {
        "question": "The schema for the anaesthesia tables has not yet been agreed with the central OMOP team.",
        "decides": "The anaesthesia_schema setting, which names where the release writes its own tables.",
        "evidence_needed": "The central OMOP team agrees a schema name, and the name is recorded as anaesthesia_schema in release.json.",
        "who": "central OMOP team", "mechanism": "release settings"},
    "E-setting-published_schema": {
        "question": "The schema for the published views has not yet been agreed with the central OMOP team.",
        "decides": "The published_schema setting, which names where the release publishes the views that ATLAS reads.",
        "evidence_needed": "The central OMOP team agrees a schema name, and the name is recorded as published_schema in release.json.",
        "who": "central OMOP team", "mechanism": "release settings"},
    "E-setting-identifier_offset": {
        "question": "How far above the core's highest identifier the anaesthesia rows should be numbered has not yet been agreed.",
        "decides": "The identifier_offset setting, which keeps the anaesthesia rows' identifiers clear of the core's after each refresh.",
        "evidence_needed": "The central OMOP team states how high the core's identifiers may rise between refreshes, and the agreed offset is recorded as identifier_offset in release.json.",
        "who": "central OMOP team", "mechanism": "release settings"},
    "E-setting-on_failure": {
        "question": "Whether the real core regenerates its surrogate keys at each refresh is not yet known.",
        "decides": "The on_failure setting of the release script. If the core renumbers its rows at each refresh, the previous anaesthesia rows no longer point at the right people and visits, so they cannot be kept when a release fails.",
        "evidence_needed": "The central OMOP team states whether person_id, visit_occurrence_id and the other surrogate keys keep their values from one refresh to the next, and the choice that follows is recorded as on_failure in release.json.",
        "who": "central OMOP team", "mechanism": "a decision"},
    "E-setting-other": {
        "question": "The release setting {key} rests on a default that has not been confirmed for the real database.",
        "decides": "The release setting {key}.",
        "evidence_needed": "The central OMOP team confirms the value, and the value is recorded as {key} in release.json.",
        "who": "central OMOP team", "mechanism": "release settings"},
    "E-how-run": {
        "question": "How the release script will be run at the hospital, whether by hand or by a scheduler after each core refresh, has not yet been decided.",
        "decides": "Whether the release script can rely on SQLCMD mode, and how it learns that the core refresh has finished.",
        "evidence_needed": "The central OMOP team decides how and when the release runs, and tells the project.",
        "who": "central OMOP team", "mechanism": "a decision"},
}

# The sentences that state the evidence in hand. Each holds only counts and names.
IN_HAND = {
    "catalogue": "The catalogue lists {tables} and {columns}. The site rules hold back {held} as built locally.",
    "catalogue_empty": "The catalogue lists no table that the tool could accept.",
    "unread_none": "The analysis found none of these in {files}.",
    "unread_some": "The analysis counts {count} of these. Of the {files} that were read, {not_fully} could not be read fully.",
    "checks_none": "No check results have been supplied. The check script plans {planned} of this kind.",
    "checks_some": "The check script plans {planned} of this kind. The results answer {answered} of them, and give no answer for {unanswered}.",
    "values_note": "A values check returns nothing when every value is held by fewer than ten rows or when there are too many values to list, so a check without an answer is not always a fault.",
    "errors_none_supplied": "No check results have been supplied.",
    "errors": "The results record {errors} that the database could not answer.",
    "provenance_one": "Every parameter in this group ({keys}) comes from {source}.",
    "provenance_mixed": "The parameters in this group come from these sources: {parts}.",
    "provenance_part": "{key} from {source}",
    "roles_unused": "{unused} of the {total} role entries in the site rules could not be used.",
    "roles_all_used": "Each of the {total} role entries in the site rules names a catalogue column and a known meaning.",
    "roles_none": "The site rules give no roles.",
    "role_reason": "The sandbox recorded the reason as {reason}.",
    "repeat": "No such check exists yet.",
    "no_profile": "No core profile has been supplied.",
    "general_profile": "The general database profile does not answer this question.",
    "version": "The core profile records CDM version {version}.",
    "version_unusual": "The core profile records a CDM version that is not in the usual form.",
    "version_none": "The core profile does not record a CDM version.",
    "fields_missing": "The core lacks {count} that the anaesthesia steps use.",
    "fields_complete": "The core holds every field that the anaesthesia steps use.",
    "joins": "The anaesthesia steps join this field to {count}, in {steps}.",
    "match": "For {measured} of those source keys, the core holds {range} per cent of the distinct keys.",
    "match_range": "between {low} and {high}",
    "match_below": "For {count} of them the share is below {threshold} per cent, so the guess does not hold there.",
    "match_empty": "For {count} of those source keys, the source table held no keys when the profile was run.",
    "match_core_side": "For {count} of those source keys, the figure was measured from the core side, because the core table is large: of a sample of the core's values, {range} per cent are keys in the source system. Each such figure is an estimate from a sample.",
    "match_none": "The core profile does not give a match rate for this field.",
    "shape": "{percent} per cent of the field's values are made only of digits.",
    "domain_rows": "The core's {table} holds about {rows} rows.",
    "domain_empty": "The core's {table} holds no rows.",
    "domain_absent": "The core does not have {table}.",
    "domain_overlap": "The core's rows already carry {count} that the anaesthesia layer writes to {table}, so the two should be compared with the central OMOP team.",
    "domain_clear": "None of the type concepts that the anaesthesia layer writes to {table} appear in the core's rows.",
    "visit_detail_rows": "The core's VISIT_DETAIL holds about {rows} rows.",
    "visit_detail_empty": "The core's VISIT_DETAIL holds no rows.",
    "visit_detail_absent": "The core does not have VISIT_DETAIL.",
    "periods": "PERSON holds about {persons} people, and about {with_period} of them have an observation period.",
    "periods_gate": "The quality gate on observation periods has not yet been run against the real core.",
    "mapping_rows": "The conversion holds {rows} under this vocabulary.",
    "mapping_steps": "The anaesthesia steps read it in {steps}.",
    "mapping_derived": "derived_mappings.json can propose rows for it from the labels in {table}.{label}, when the runner is given a vocabulary download.",
    "mapping_no_checks": "No check results have been supplied.",
    "mapping_not_planned": "No values check is planned for {columns}, so its values cannot be listed by the checks and the mapping rows must be written from another source.",
    "mapping_not_listed": "The check results do not list the values of {columns}.",
    "mapping_listed": "The check results list {listed} of {columns}.",
    "mapping_unmapped": "The number of those that have no mapping row under this vocabulary is {unmapped}.",
    "mapping_all": "Each of them has a mapping row under this vocabulary.",
    "setting_default": "release.json does not set {key}, so the release uses its default.",
    "setting_set": "release.json sets {key}.",
    "schema_tables": "The core profile found {present} of the {total} tables of CDM 5.4 in the schema it was run against.",
    "schema_missing": "The core lacks {count} that the anaesthesia layer reads or adds to.",
    "profile_errors": "The core profile could not answer {count}.",
    "prefix_compared": "The core profile reached the source tables through the source prefix and compared {count}.",
    "prefix_not_compared": "The core profile was run without a source prefix, so it did not reach the source tables.",
    "keys": "The core profile gives the key type of {typed} of the {total} tables that the anaesthesia layer reads or adds to, and BIGINT is the type of {bigint} of them.",
    "keys_no_bigint": "The core profile gives the key type of {typed} of the {total} tables that the anaesthesia layer reads or adds to, and none of them uses BIGINT.",
    "keys_digits": "The highest key among them has {digits} digits.",
    "keys_concerns": "The profile raises {count} about the type of the keys or the room left for further rows.",
    "how_run": "No decision has been recorded.",
}
# Counted nouns: the singular, the plural, and the words for none.
NOUNS = {"table": ("table", "tables", "no tables"), "column": ("column", "columns", "no columns"),
         "file": ("request file", "request files", "no request files"), "check": ("check", "checks", "no checks"),
         "field": ("field", "fields", "no fields"), "join": ("join", "joins", "no joins"), "source key": ("source key", "source keys", "no source keys"),
         "mapping row": ("mapping row", "mapping rows", "no mapping rows"), "value": ("value", "values", "no values"),
         "type concept": ("type concept", "type concepts", "no type concepts"),
         "question": ("of its questions", "of its questions", "none of its questions"),
         "concern": ("concern", "concerns", "no concerns")}
SOURCE_PHRASES = {tunable.INVENTED: "an invented default", tunable.PUBLIC: "a public source",
                  tunable.SITE_RULES: "the site rules", tunable.CHECK_RESULTS: "the check results"}
REASON_PHRASES = {"database": "a database error", "prerequisite": "something that the role depends on is missing",
                  "reference data": "the reference data is missing"}
# The summary that the harness can print at the end of its scorecard.
SUMMARY = {
    "title": "OPEN QUESTIONS",
    "total": "questions: {total} ({answered} answered, {partly} partly answered, {open} open)",
    "stage": "stage {stage}, {name}: {total} ({answered} answered, {partly} partly answered, {open} open)",
    "who": "not yet answered, by who must act: {parts}",
    "who_part": "{who} {count}",
    "who_none": "not yet answered, by who must act: none",
}

# The tunable parameters, grouped by what they shape. A parameter in no group gets a row of its own.
TUNING_GROUPS = {
    "ages": ("age_curve_exponent", "youngest_age_days", "oldest_age_days"),
    "anaesthetic_duration": ("anaesthetic_durations_minutes", "shortest_anaesthetic_minutes"),
    "induction": ("induction_dose_share", "induction_window_minutes"),
    "placement": ("placement_window_minutes",),
    "removal": ("removal_window_minutes",),
    "still_in_place": ("still_in_place_share", tunable.STILL_IN_PLACE_VALUE),
    "measurement_before": ("measurement_before_least_minutes", "measurement_before_most_minutes"),
    "admission": ("admission_before_least_minutes", "admission_before_most_minutes"),
    "discharge": ("discharge_after_least_minutes", "discharge_after_most_minutes"),
    "death_share": ("death_share",),
    "death_timing": ("death_after_least_days", "death_after_most_days"),
    "end_tidal_co2": ("end_tidal_co2_mean", "end_tidal_co2_spread", "end_tidal_co2_lowest", "end_tidal_co2_highest"),
    "end_tidal_agent": ("end_tidal_agent_mean", "end_tidal_agent_spread", "end_tidal_agent_lowest", "end_tidal_agent_highest"),
    "oxygen_saturation": ("oxygen_saturation_lowest", "oxygen_saturation_highest"),
    "temperature": ("temperature_lowest_c", "temperature_highest_c"),
    "pressure_fallback": ("systolic_sd_fallback", "diastolic_sd_fallback", "systolic_base", "systolic_per_year",
                          "systolic_highest", "diastolic_base", "diastolic_per_year", "diastolic_highest",
                          "pressure_fallback_spread"),
    "mean_pressure": ("arterial_line_share", "arterial_interval_minutes", "mean_pressure_lowest", "mean_pressure_highest"),
}
# The pairs of roles that a spans check measures, and the parameter that the sandbox sets from it.
SPAN_GROUPS = {"anaesthetic_duration": ("anaesthetic_start", "anaesthetic_stop", "anaesthetic_durations_minutes"),
               "removal": ("placement_time", "removal_time", "removal_window_minutes"),
               "discharge": ("admission_time", "discharge_time", "discharge_after_most_minutes")}
# The single column that names the evidence for a rate.
RATE_ROLES = {"still_in_place": "removal_time", "death_share": "death_date"}
# The settings whose question is about the real core, and so belongs to stage C or E with the profile.
PROFILE_SETTINGS = {"omop_schema", "source_prefix", "identifier_type"}
RANK = {tunable.INVENTED: 0, tunable.PUBLIC: 1, tunable.SITE_RULES: 2, tunable.CHECK_RESULTS: 3}


class QuestionsError(ValueError):
    """An input could not be read."""


def _n(count, noun):
    """A count with its noun, such as 1 table, 3 tables or no tables."""
    one, many, none = NOUNS[noun]
    if noun == "question":
        return none if count == 0 else f"{count:,} {one}"
    return none if count == 0 else f"{count:,} {one if count == 1 else many}"


def _join(items):
    items = list(items)
    if not items:
        return ""
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _row(question_id, stage, wording, status, currently_from, in_hand, **names):
    entry = WORDING[wording]
    fill = lambda text: text.format(**names) if names else text  # noqa: E731
    needed = names.pop("_evidence_needed", None) if names else None
    row = {"question_id": question_id, "stage": stage, "question": fill(entry["question"]),
           "decides": fill(entry["decides"]), "currently_from": currently_from,
           "evidence_needed": fill(needed or entry["evidence_needed"]),
           "who": names.pop("_who", None) or entry["who"], "mechanism": entry["mechanism"],
           "status": status, "evidence_in_hand": " ".join(part for part in in_hand if part)}
    assert row["status"] in STATUSES and row["who"] in WHO and row["mechanism"] in MECHANISMS
    assert row["currently_from"] in SOURCES
    return row


# Reading the inputs.

def _definitions(rules_text):
    """The definition keys of the site rules, as convert.run reads them for the conversion's analysis."""
    found = {}
    try:
        data = json.loads(rules_text) if rules_text else {}
    except ValueError:
        return found
    for rule in data.get("definitionKeys", []) if isinstance(data, dict) else []:
        if isinstance(rule, dict) and rule.get("column") and rule.get("definitionTable"):
            found[rule["column"].upper()] = (rule["definitionTable"], rule.get("keyColumn") or rule["column"])
    return found


def _steps(folder):
    folder = Path(folder)
    steps = json.loads((folder / "conversion.json").read_text())
    # A step's file name is checked as convert.py checks it, so that no step can be read from outside the folder.
    named = [step for step in steps if not isinstance(step.get("file"), str) or not convert.FILE_NAME.fullmatch(step["file"])
             or set(step["file"]) == {"."}]
    if named:
        raise ValueError("; ".join(convert.layer_problems(named)))
    return [(step, decode((folder / step["file"]).read_bytes())) for step in steps]


def _literals(node):
    return [item.this for item in node.expressions if isinstance(item, exp.Literal) and item.is_string] \
        if isinstance(node, exp.In) else []


def vocabularies(folder, catalogue=None):
    """The mapping vocabularies that the anaesthesia steps read.

    Returns {vocabulary: {"columns": [(table, column)], "constant": bool, "steps": [file]}}. A
    vocabulary's column is the source column whose value the step compares with source_code, and it
    is kept only where convert.mapped_columns finds the same column.
    """
    found = {}
    for step, sql in _steps(folder):
        if step.get("layer") != "anaesthesia":
            continue
        try:
            tree = sqlglot.parse_one(sql, dialect="tsql")
        except sqlglot.errors.SqlglotError:
            continue
        mapped = {(t.upper(), c.upper()) for t, c in convert.mapped_columns(tree)}
        for select in tree.find_all(exp.Select):
            aliases = {t.alias_or_name.upper() for t in select.find_all(exp.Table)
                       if (t.db or "").upper() == OMOP_SCHEMA.upper() and t.name.lower() == "source_to_concept_map"}
            named, codes = {}, {}
            for node in select.find_all(exp.EQ, exp.In):
                if isinstance(node, exp.In):
                    column = node.this
                    if isinstance(column, exp.Column) and column.table.upper() in aliases \
                            and column.name.lower() == "source_vocabulary_id":
                        named.setdefault(column.table.upper(), set()).update(_literals(node))
                    continue
                for one, other in ((node.this, node.expression), (node.expression, node.this)):
                    if not (isinstance(one, exp.Column) and one.table.upper() in aliases):
                        continue
                    if one.name.lower() == "source_vocabulary_id" and isinstance(other, exp.Literal) and other.is_string:
                        named.setdefault(one.table.upper(), set()).add(other.this)
                    elif one.name.lower() == "source_code":
                        carried = _passed_through(other)
                        if isinstance(other, exp.Literal) or (carried is not None and isinstance(carried, exp.Literal)):
                            codes.setdefault(one.table.upper(), set()).add(None)
                        elif carried is not None and isinstance(carried, exp.Column) and carried.table:
                            origin = core_profile._source(select, carried)
                            if origin and (origin[0].upper(), origin[1].upper()) in mapped:
                                codes.setdefault(one.table.upper(), set()).add(_spelling(catalogue, origin))
            for alias, names in named.items():
                for name in names:
                    entry = found.setdefault(name, {"columns": [], "constant": False, "steps": []})
                    if step["file"] not in entry["steps"]:
                        entry["steps"].append(step["file"])
                    for code in codes.get(alias, ()):
                        if code is None:
                            entry["constant"] = True
                        elif code not in entry["columns"]:
                            entry["columns"].append(code)
    return dict(sorted(found.items()))


def _spelling(catalogue, origin):
    """A source column in the catalogue's own spelling, where the catalogue has it."""
    if catalogue is not None:
        table = catalogue.table(origin[0])
        column = table.column(origin[1]) if table else None
        if column is not None:
            return table.name, column.name
    return tuple(origin)


def _mapping_rows(folder):
    """The mapping rows of the conversion: vocabulary -> set of source codes."""
    rows = {}
    for row in convert.mapping_dicts(folder):
        rows.setdefault((row.get("source_vocabulary_id") or "").strip(), set()).add((row.get("source_code") or "").strip())
    return rows


def _derived(folder):
    path = Path(folder) / "derived_mappings.json"
    try:
        entries = json.loads(path.read_text()) if path.exists() else []
    except ValueError:
        return {}
    return {e["vocabulary"]: e for e in entries if isinstance(e, dict) and e.get("vocabulary")}


def _release_settings(folder):
    """Every release setting, with whether release.json sets it."""
    try:
        from .release import SETTINGS
    except Exception:  # release.py is being changed elsewhere; the settings known when this was written stand in
        SETTINGS = dict.fromkeys(("omop_schema", "anaesthesia_schema", "published_schema", "source_prefix",
                                  "identifier_type"))
    path = Path(folder) / "release.json"
    try:
        chosen = json.loads(path.read_text()) if path.exists() else {}
    except ValueError:
        chosen = {}
    chosen = chosen if isinstance(chosen, dict) else {}
    keys = list(dict.fromkeys([*SETTINGS, *[k for k in chosen if isinstance(k, str)]]))
    return {key: key in chosen for key in keys}


# The stages.

def _stage_a(analysis, planned, checks, catalogue, held_back):
    rows = []
    tables = list(catalogue.tables())
    columns = sum(len(t.columns) for t in tables)
    if tables:
        rows.append(_row("A-catalogue", "A", "A-catalogue", "answered", "catalogue export",
                         [IN_HAND["catalogue"].format(tables=_n(len(tables), "table"), columns=_n(columns, "column"), held=_n(len(held_back), "table"))]))
    else:
        rows.append(_row("A-catalogue", "A", "A-catalogue", "open", "a guess", [IN_HAND["catalogue_empty"]]))

    analysis.pack()
    summary = analysis.summary
    by_label = {label: count for label, count in summary["unread"]}
    for kind in v.UNRESOLVED:
        count = by_label.get(v.UNRESOLVED_LABELS[kind], 0)
        key = f"A-unread-{kind}" if f"A-unread-{kind}" in WORDING else "A-unread-other"
        text = (IN_HAND["unread_some"].format(count=count, files=_n(summary["files"], "file"), not_fully=summary["notFullyRead"])
                if count else IN_HAND["unread_none"].format(files=_n(summary["files"], "file")))
        rows.append(_row(f"A-unread-{kind}", "A", key, "open" if count else "answered",
                         "a guess" if count else "catalogue export", [text], kind=kind))

    for kind in CHECK_KINDS:
        wanted = [c for c in planned if c.kind == kind]
        answered = sum(1 for c in wanted if _answered(c, checks)) if checks else 0
        if not wanted:
            continue    # the question does not arise for these requests
        if checks is None:
            text, status = IN_HAND["checks_none"].format(planned=_n(len(wanted), "check")), "open"
        else:
            text = IN_HAND["checks_some"].format(planned=_n(len(wanted), "check"), answered=answered,
                                                 unanswered=len(wanted) - answered)
            status = "answered" if answered == len(wanted) else "partly" if answered else "open"
        note = IN_HAND["values_note"] if kind == "values" and checks is not None and answered < len(wanted) else ""
        source = "check results" if checks is not None and answered else "invented default"
        rows.append(_row(f"A-checks-{kind}", "A", f"A-checks-{kind}", status, source, [text, note]))

    if checks is None:
        rows.append(_row("A-check-errors", "A", "A-check-errors", "open", "a guess", [IN_HAND["errors_none_supplied"]]))
    else:
        # A table whose size the server does not record is not a check left out, so it is not counted here.
        skipped = {item for item in getattr(checks, "skipped", ()) if item[3] in ("time", "size")}
        left_out = v.checks_skipped_sentence(sum(1 for item in skipped if item[3] == "time"),
                                             sum(1 for item in skipped if item[3] == "size"))
        rows.append(_row("A-check-errors", "A", "A-check-errors", "partly" if checks.errors or skipped else "answered",
                         "check results", [IN_HAND["errors"].format(errors=_n(checks.errors, "check"))]
                         + ([left_out] if left_out else [])))
    return rows


def _answered(check, checks):
    # A plain query that has run and found nothing has answered its check.
    if checking.ran_empty(check, checks):
        return True
    up = lambda *names: tuple(n.upper() for n in names)  # noqa: E731
    if check.kind == "rows":
        return check.table.upper() in {t.upper() for t in checks.rows}
    if check.kind == "spans":
        return up(check.table, check.column, check.later) in {up(*k) for k in checks.spans}
    held = {"column": checks.columns, "values": checks.values, "years": checks.years}[check.kind]
    return up(check.table, check.column) in {up(*k) for k in held}


def _current_tuning(analysis, checks):
    """The tuning as the sandbox would hold it after building, without building one.

    It mirrors the sandbox: the overrides in the site rules, a public file of durations where one is
    present, and the parameters that spans in the check results set for the paired roles.
    """
    tuning = tunable.Tuning(analysis.tuning, analysis.still_in_place)
    try:
        from .realistic import DATA
        with open(DATA / "durations.csv", newline="", encoding="utf-8") as f:
            listed = [r for r in csv.DictReader(f) if r.get("measure") == "anaesthesia_duration"]
        if listed:
            tuning.defaults.setdefault("anaesthetic_durations_minutes",
                                       (tuple(int(float(r["minutes"])) for r in listed), tunable.PUBLIC))
    except (OSError, ValueError, KeyError):
        pass
    if checks is not None:
        start, stop = _pair(analysis.roles, "anaesthetic_start", "anaesthetic_stop")
        if start:
            for first_role, second_role, key in SPAN_GROUPS.values():
                first, second = _pair(analysis.roles, first_role, second_role)
                if first and checks.spans.get((first.table, first.column, second.column)):
                    tuning.set_from_checks(key, "spans")
    return tuning


def _pair(roles, first_role, second_role):
    """The first two roles, in one table, that a spans check pairs, or (None, None)."""
    for first in (r for r in roles if r.role == first_role):
        for second in (r for r in roles if r.role == second_role):
            if first.table == second.table and first.column != second.column:
                return first, second
    return None, None


def _stage_b(analysis, tuning, roles_not_applied):
    rows = []
    report = {item["key"]: item for item in tuning.report()}
    keys = list(report) + [k for k in (tunable.STILL_IN_PLACE_VALUE,) if k not in report]
    grouped = {key for members in TUNING_GROUPS.values() for key in members}
    groups = dict(TUNING_GROUPS)
    for key in keys:
        if key not in grouped:
            groups[f"other-{key}"] = (key,)

    for name, members in groups.items():
        present = [k for k in members if k in report]
        if not present and name != "still_in_place":
            continue
        provenance = {k: report[k]["provenance"] for k in present}
        if tunable.STILL_IN_PLACE_VALUE in members and tunable.STILL_IN_PLACE_VALUE not in report:
            provenance[tunable.STILL_IN_PLACE_VALUE] = tunable.INVENTED
        weakest = min(provenance.values(), key=lambda p: RANK.get(p, 0))
        real = [k for k, p in provenance.items() if p in (tunable.SITE_RULES, tunable.CHECK_RESULTS)]
        status = "answered" if len(real) == len(provenance) else "partly" if real else "open"
        if len(set(provenance.values())) == 1:
            text = IN_HAND["provenance_one"].format(keys=", ".join(provenance), source=SOURCE_PHRASES.get(weakest, weakest))
        else:
            text = IN_HAND["provenance_mixed"].format(parts="; ".join(
                IN_HAND["provenance_part"].format(key=k, source=SOURCE_PHRASES.get(p, p)) for k, p in provenance.items()))
        names, who = {}, None
        if name.startswith("other-"):
            key = members[0]
            wording = "B-tuning-other"
            names = {"key": key, "description": report[key]["description"] if key in report else ""}
        else:
            wording = f"B-tuning-{name}"
            entry = WORDING[wording]
            if name in SPAN_GROUPS:
                first, second = _pair(analysis.roles, *SPAN_GROUPS[name][:2])
                if first:
                    names = {"first": f"{first.table}.{first.column}", "second": f"{second.table}.{second.column}"}
                else:
                    names = {"_evidence_needed": entry["evidence_needed_without_roles"]}
                    who = "clinical lead"
            elif name in RATE_ROLES:
                role = next((r for r in analysis.roles if r.role == RATE_ROLES[name]), None)
                if role:
                    names = {"column": f"{role.table}.{role.column}"}
                else:
                    names = {"_evidence_needed": entry["evidence_needed_without_roles"]}
                    who = "clinical lead"
        if who:
            names["_who"] = who
        source = {tunable.INVENTED: "invented default", tunable.PUBLIC: "public source",
                  tunable.SITE_RULES: "site rules", tunable.CHECK_RESULTS: "check results"}.get(weakest, "invented default")
        rows.append(_row(f"B-tuning-{name.removeprefix('other-')}", "B", wording, status, source, [text], **names))

    given = [item for item in analysis.rules.roles]
    if given:
        unused = max(0, len(given) - len(analysis.roles))
        text = (IN_HAND["roles_unused"].format(unused=unused, total=len(given)) if unused
                else IN_HAND["roles_all_used"].format(total=len(given)))
        rows.append(_row("B-roles-unused", "B", "B-roles-unused", "partly" if unused else "answered", "site rules", [text]))
    else:
        rows.append(_row("B-roles-unused", "B", "B-roles-unused", "open", "invented default", [IN_HAND["roles_none"]]))

    for item in roles_not_applied or []:
        table, column, role, reason = item["table"], item["column"], item["role"], item["reason"]
        if analysis.catalogue.table(table) is None or analysis.catalogue.table(table).column(column) is None:
            continue
        needed = WORDING["B-role"].get(f"evidence_needed_{reason}")
        names = {"table": table, "column": column, "role": role}
        if needed:
            names["_evidence_needed"] = needed
        rows.append(_row(f"B-role-{table}.{column}-{role}", "B", "B-role", "open", "invented default",
                         [IN_HAND["role_reason"].format(reason=REASON_PHRASES.get(reason, "an unknown reason"))], **names))

    rows.append(_row("B-repeat-anaesthetics", "B", "B-repeat-anaesthetics", "open", "invented default", [IN_HAND["repeat"]]))
    return rows


def _stage_d(folder, catalogue, planned, checks):
    rows = []
    mapping = _mapping_rows(folder)
    derived = _derived(folder)
    planned_values = {(c.table.upper(), c.column.upper()) for c in planned if c.kind == "values"}
    for vocabulary, entry in vocabularies(folder, catalogue).items():
        codes = mapping.get(vocabulary, set())
        in_hand = [IN_HAND["mapping_rows"].format(rows=_n(len(codes), "mapping row")),
                   IN_HAND["mapping_steps"].format(steps=_join(entry["steps"]))]
        if vocabulary in derived:
            d = derived[vocabulary]
            table = catalogue.table(str(d.get("table", "")))
            label = table.column(str(d.get("label", ""))) if table else None
            if label is not None:
                in_hand.append(IN_HAND["mapping_derived"].format(table=table.name, label=label.name))
        if not entry["columns"]:
            status = "answered" if codes else "open"
            rows.append(_row(f"D-mapping-{vocabulary}", "D", "D-mapping-constant", status,
                             "mapping rows" if codes else "invented default", in_hand, vocabulary=vocabulary))
            continue
        names = _join(f"{t}.{c}" for t, c in entry["columns"])
        listed_all, unmapped_all, missing = 0, 0, []
        for table, column in entry["columns"]:
            key = (table.upper(), column.upper())
            if checks is None:
                continue
            listed = next((vals for (t, c), vals in checks.values.items() if (t.upper(), c.upper()) == key), None)
            if listed is None:
                missing.append(f"{table}.{column}")
                continue
            numeric = checking.is_numeric(catalogue.table(table).column(column))
            known = {checking.normalise(code, numeric) for code in codes}
            listed_all += len(listed)
            unmapped_all += sum(1 for value, _, _ in listed if checking.normalise(value, numeric) not in known)
        not_planned = [f"{t}.{c}" for t, c in entry["columns"] if (t.upper(), c.upper()) not in planned_values]
        if checks is None:
            in_hand.append(IN_HAND["mapping_no_checks"])
        elif missing:
            in_hand.append(IN_HAND["mapping_not_listed"].format(columns=_join(missing)))
        if not_planned:
            in_hand.append(IN_HAND["mapping_not_planned"].format(columns=_join(not_planned)))
        if checks is not None and len(missing) < len(entry["columns"]):
            in_hand.append(IN_HAND["mapping_listed"].format(listed=_n(listed_all, "value"), columns=names))
            if listed_all:
                in_hand.append(IN_HAND["mapping_unmapped"].format(unmapped=unmapped_all) if unmapped_all
                               else IN_HAND["mapping_all"])
        complete = checks is not None and not missing
        status = "answered" if complete and unmapped_all == 0 else "partly" if codes or listed_all else "open"
        source = "mapping rows" if codes else "invented default"
        rows.append(_row(f"D-mapping-{vocabulary}", "D", "D-mapping", status, source, in_hand,
                         vocabulary=vocabulary, columns=names))
    return rows


def _version_text(version):
    import re
    if not version:
        return IN_HAND["version_none"]
    if re.fullmatch(r"v?\d{1,2}(\.\d{1,2}){0,2}", version.strip()):
        return IN_HAND["version"].format(version=version.strip())
    return IN_HAND["version_unusual"]


def _stages_c_e(folder, profile_text):
    rows = []
    try:
        facts = core_profile.conversion_facts(folder)
    except (core_profile.ProfileError, OSError, ValueError, KeyError):
        facts = {"written": [], "pairs": {}, "types": {}, "used": {}, "reads": set(), "uses": {}, "files": [], "writers": []}
    found = None
    concerns = []
    if profile_text is not None:
        found = core_profile.read(profile_text, folder)
        concerns = core_profile.suggest(found, folder)
    general = found is not None and found["format"] == "general"
    tables = found["tables"] if found else {}
    w = core_profile.WORDING

    # The CDM version.
    if found is None:
        rows.append(_row("C-cdm-version", "C", "C-cdm-version", "open", "a guess", [IN_HAND["no_profile"]]))
    else:
        version = (found.get("cdm_source") or {}).get("cdm_version")
        affected = [c for c in concerns if c["question"] in (w["version_question"], w["absent_question"])]
        fields = sum(c["finding"].count(".") for c in affected if c["question"] == w["absent_question"])
        texts = [_version_text(version),
                 IN_HAND["fields_missing"].format(count=_n(fields, "field")) if affected else IN_HAND["fields_complete"]]
        rows.append(_row("C-cdm-version", "C", "C-cdm-version", "answered" if version else "partly", "core profile", texts))

    # The joins on source values.
    by_field = {}
    for (table, field, _, _), entry in facts["pairs"].items():
        by_field.setdefault(f"{table}.{field}", []).extend(s for s in entry["steps"])
    for name, steps in sorted(by_field.items()):
        steps = list(dict.fromkeys(steps))
        joins = [k for k in facts["pairs"] if f"{k[0]}.{k[1]}" == name]
        in_hand = [IN_HAND["joins"].format(count=_n(len(joins), "source key"), steps=_join(steps))]
        status, source = "open", "a guess"
        if found is None:
            in_hand.append(IN_HAND["no_profile"])
        elif general:
            in_hand.append(IN_HAND["general_profile"])
        else:
            matches = [m for m in found["matches"] if m["core"] == name]
            measured = [m for m in matches if m["keys"] and m["percent"] is not None]
            empty = [m for m in matches if not m["keys"]]
            def span(found):
                low, high = min(m["percent"] for m in found), max(m["percent"] for m in found)
                return str(low) if low == high else IN_HAND["match_range"].format(low=low, high=high)
            source_side = [m for m in measured if m.get("side") != "core"]
            if source_side:
                in_hand.append(IN_HAND["match"].format(measured=len(source_side), range=span(source_side)))
            core_side = [m for m in measured if m.get("side") == "core"]
            if core_side:
                in_hand.append(IN_HAND["match_core_side"].format(count=len(core_side), range=span(core_side)))
            # A low share from either side is a finding that the guess does not hold.
            below = [m for m in measured if m["percent"] < core_profile.MATCH_THRESHOLD]
            if below:
                in_hand.append(IN_HAND["match_below"].format(count=len(below), threshold=core_profile.MATCH_THRESHOLD))
            if empty:
                in_hand.append(IN_HAND["match_empty"].format(count=len(empty)))
            if not matches:
                in_hand.append(IN_HAND["match_none"])
            shape = found["source_values"].get(name)
            if shape and shape["digits_percent"] is not None:
                in_hand.append(IN_HAND["shape"].format(percent=shape["digits_percent"]))
            if measured or shape:
                source = "core profile"
                status = "answered" if len(measured) >= len(joins) else "partly"
        rows.append(_row(f"C-source-value-{name}", "C", "C-source-value", status, source, in_hand, field=name))

    # The domains that the layer adds to, apart from VISIT_DETAIL, which has a question of its own.
    for table in facts["written"]:
        if table == "visit_detail":
            continue
        status, source = "open", "a guess"
        if found is None:
            in_hand = [IN_HAND["no_profile"]]
        else:
            entry = tables.get(table)
            source = "core profile"
            if entry is None:
                in_hand, status, source = [IN_HAND["general_profile"]], "open", "a guess"
            elif not entry["present"]:
                in_hand, status = [IN_HAND["domain_absent"].format(table=table)], "answered"
            else:
                in_hand = [IN_HAND["domain_empty"].format(table=table) if entry["rows"] == 0
                           else IN_HAND["domain_rows"].format(table=table, rows=core_profile._count(entry["rows"]))]
                if general:
                    status = "answered" if entry["rows"] == 0 else "partly"
                else:
                    written = {str(c) for (t, c) in facts["types"] if t == table}
                    overlap = written & set(found["type_concepts"].get(table, {}))
                    in_hand.append(IN_HAND["domain_overlap"].format(count=_n(len(overlap), "type concept"), table=table) if overlap
                                   else IN_HAND["domain_clear"].format(table=table))
                    status = "partly" if overlap else "answered"
        rows.append(_row(f"C-domain-{table}", "C", "C-domain", status, source, in_hand, table=table))

    # VISIT_DETAIL.
    detail = tables.get("visit_detail")
    if found is None:
        rows.append(_row("C-visit-detail", "C", "C-visit-detail", "open", "a guess", [IN_HAND["no_profile"]]))
    elif detail is None:
        rows.append(_row("C-visit-detail", "C", "C-visit-detail", "open", "a guess", [IN_HAND["general_profile"]]))
    else:
        text = (IN_HAND["visit_detail_absent"] if not detail["present"] else IN_HAND["visit_detail_empty"]
                if detail["rows"] == 0 else IN_HAND["visit_detail_rows"].format(rows=core_profile._count(detail["rows"])))
        rows.append(_row("C-visit-detail", "C", "C-visit-detail", "answered", "core profile", [text]))

    # Observation periods.
    periods = found.get("observation_period") if found else None
    if found is None:
        rows.append(_row("C-observation-periods", "C", "C-observation-periods", "open", "a guess", [IN_HAND["no_profile"]]))
    elif not periods:
        rows.append(_row("C-observation-periods", "C", "C-observation-periods", "open", "a guess", [IN_HAND["general_profile"]]))
    else:
        rows.append(_row("C-observation-periods", "C", "C-observation-periods", "partly", "core profile", [
            IN_HAND["periods"].format(persons=core_profile._count(periods["persons"]),
                                      with_period=core_profile._count(periods["with_period"])),
            IN_HAND["periods_gate"]]))

    # The release settings, in the order that release.SETTINGS gives them, and the decisions.
    settings = _release_settings(folder)
    touched = (set(facts["written"]) | {t for t, _ in facts["reads"]} | {p[0] for p in facts["pairs"]}) - {"source_to_concept_map"}
    for key, set_here in settings.items():
        wording = f"E-setting-{key}" if f"E-setting-{key}" in WORDING else "E-setting-other"
        stage = "E"
        in_hand = [IN_HAND["setting_set" if set_here else "setting_default"].format(key=key)]
        status = "partly" if set_here else "open"
        source = "release settings" if set_here else "a guess"
        if key in ("anaesthesia_schema", "published_schema", "identifier_offset", "on_failure") or wording == "E-setting-other":
            status = "answered" if set_here else "open"
        if key in PROFILE_SETTINGS and found is not None:
            if key == "omop_schema":
                present = sum(1 for e in tables.values() if e["present"])
                in_hand.append(IN_HAND["schema_tables"].format(present=present, total=len(tables)))
                missing = [t for t in touched if t in tables and not tables[t]["present"]]
                if missing:
                    in_hand.append(IN_HAND["schema_missing"].format(count=_n(len(missing), "table")))
                if found["errors"]:
                    in_hand.append(IN_HAND["profile_errors"].format(count=_n(len(found["errors"]), "question")))
                if present:
                    status, source = ("answered" if not missing else "partly"), "core profile"
            elif key == "source_prefix":
                if general:
                    in_hand.append(IN_HAND["general_profile"])
                elif found.get("source_compared"):
                    compared = [m for m in found["matches"] if m["keys"]]
                    in_hand.append(IN_HAND["prefix_compared"].format(count=_n(len(compared), "join")))
                    if compared:
                        status, source = "answered", "core profile"
                else:
                    in_hand.append(IN_HAND["prefix_not_compared"])
            elif key == "identifier_type":
                relevant = [t for t in sorted(touched) if t in tables]
                typed = [t for t in relevant if tables[t]["key_type"]]
                bigint = [t for t in typed if tables[t]["key_type"] == "bigint"]
                in_hand.append(IN_HAND["keys" if bigint else "keys_no_bigint"].format(
                    typed=len(typed), total=len(relevant), bigint=len(bigint)))
                digits = [tables[t]["key_digits"] for t in typed if tables[t]["key_digits"]]
                if digits:
                    in_hand.append(IN_HAND["keys_digits"].format(digits=max(digits)))
                raised = [c for c in concerns if c["question"] in (w["bigint_question"], w["headroom_question"])]
                if raised:
                    in_hand.append(IN_HAND["keys_concerns"].format(count=_n(len(raised), "concern")))
                if typed:
                    status, source = ("answered" if len(typed) == len(relevant) else "partly"), "core profile"
        elif key in PROFILE_SETTINGS:
            in_hand.append(IN_HAND["no_profile"])
        rows.append(_row(f"E-setting-{key}", stage, wording, status, source, in_hand, key=key))
    if "on_failure" not in settings:
        rows.append(_row("E-setting-on_failure", "E", "E-setting-on_failure", "open", "a guess",
                         [IN_HAND["setting_default"].format(key="on_failure")]))
    rows.append(_row("E-how-run", "E", "E-how-run", "open", "a guess", [IN_HAND["how_run"]]))
    return rows


def tuning_rows(analysis, checks):
    """The register's rows for the groups of tunable parameters, as the sandbox would hold them without building.

    analysis is an Analysis with the world's site rules, and checks its accepted check results or
    None. The target module takes from these the rows that bear on one query.
    """
    rows = _stage_b(analysis, _current_tuning(analysis, checks), None)
    return [row for row in rows if row["question_id"].startswith("B-tuning-")]


# The whole register.

def questions(world, conversion, checks_csv=None, profile_text=None, build=False, rows=400):
    """Every open question, with its status and the evidence in hand, as a list of dictionaries.

    world is a harness.World. conversion is a conversion folder. checks_csv and profile_text are
    the texts of a check results file and a core profile result, either of which may be None. With
    build, the sandbox is built, so that its tuning is exactly what it used and the roles that it
    could not apply have rows of their own.
    """
    folder = Path(conversion)
    rules_text = world.rules_path.read_text() if world.rules_path else None
    try:
        requests_only = world.analysis(checks_csv)
        with_conversion = world.analysis(checks_csv)
        with_conversion.add_request("conversion", convert.as_request(
            [(step["table"], sql) for step, sql in _steps(folder)], _definitions(rules_text)))
    except checking.ChecksError as error:
        raise QuestionsError("the check results file does not have the expected layout") from error
    planned = with_conversion.planned_checks(include_years=True, include_spans=True)
    checks = with_conversion.checks
    full = Catalogue.from_csv(world.catalogue_text())

    roles_not_applied = None
    if build:
        from .sandbox import Sandbox
        sandbox = Sandbox(full, harness.inventory_zip(with_conversion))
        built = sandbox.build(rows)
        tuning, roles_not_applied = sandbox.tuning, built["rolesNotApplied"]
    else:
        tuning = _current_tuning(with_conversion, checks)

    try:
        c_and_e = _stages_c_e(folder, profile_text)
    except core_profile.ProfileError as error:
        raise QuestionsError(f"the core profile could not be read: {error}") from error
    found = (_stage_a(requests_only, planned, checks, with_conversion.catalogue, with_conversion.held_back)
             + _stage_b(with_conversion, tuning, roles_not_applied)
             + [r for r in c_and_e if r["stage"] == "C"]
             + _stage_d(folder, with_conversion.catalogue, planned, checks)
             + [r for r in c_and_e if r["stage"] == "E"])
    return found


def to_csv(rows):
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=LAYOUT, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


def summary(rows):
    """Counts by stage and status, and by who must act, as lines to print at the end of a scorecard."""
    def counts(items):
        found = Counter(r["status"] for r in items)
        return {status: found[status] for status in STATUSES} | {"total": len(items)}

    lines = [SUMMARY["title"], SUMMARY["total"].format(**counts(rows))]
    for stage, name in STAGES.items():
        here = [r for r in rows if r["stage"] == stage]
        if here:
            lines.append(SUMMARY["stage"].format(stage=stage, name=name, **counts(here)))
    waiting = Counter(r["who"] for r in rows if r["status"] != "answered")
    parts = [SUMMARY["who_part"].format(who=who, count=waiting[who]) for who in WHO if waiting[who]]
    lines.append(SUMMARY["who"].format(parts=", ".join(parts)) if parts else SUMMARY["who_none"])
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(prog="schemalyser.questions")
    parser.add_argument("world", type=Path)
    parser.add_argument("conversion", type=Path)
    parser.add_argument("--checks", type=Path, help="a check results file")
    parser.add_argument("--profile", type=Path, help="the saved result of the core profile script")
    parser.add_argument("--build", action="store_true", help="build the sandbox, to list the roles it could not apply")
    parser.add_argument("--rows", type=int, default=400)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        found = questions(harness.World.from_folder(args.world), args.conversion,
                          decode(args.checks.read_bytes()) if args.checks else None,
                          decode(args.profile.read_bytes()) if args.profile else None, args.build, args.rows)
    except QuestionsError as error:
        raise SystemExit(f"schemalyser.questions: {error}")
    args.out.write_text(to_csv(found), encoding="utf-8")
    print(summary(found), end="")


if __name__ == "__main__":
    main()
