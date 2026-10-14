# What is asked of the record

This page holds the public counts from the question log of the plan's first package. The log itself names colleagues and may describe cases, so it lives in the private root; what appears here is each request in a sentence, the class it falls in, the parts of the record it needs, and what the contract lacks for it.

## The classes

A request is one of four things: a catalogue capability with parameters; a free question over the three contract parts; a free question that needs draft parts; or a question the anaesthesia record cannot answer.

## The first six, 14 October 2026

The owner gave six requests from memory as examples of what the department asks. They are classified against contract version 1.1.

| Request | Class | Parts needed | What the contract lacks |
| --- | --- | --- | --- |
| A quality audit of fresh gas flow, nitrous oxide and sevoflurane use, for each anaesthetist, by month | Catalogue capability (`volatile_consumption`, `monitoring_completeness`) grouped by anaesthetist and month | Readings, anaesthetics, staff | Nothing in the vocabulary. The grouping by anaesthetist names a person, which is a governance decision rather than a gap. |
| An audit of blood transfusion practice in neonates having an anaesthetic | Catalogue capability (`transfusion`, `total_volume_per_kg`) with cohort parameters | Fluids, anaesthetics, patients, the patient's details at birth, laboratory results for the trigger | Nothing, once fluids and laboratory results are promoted. |
| An audit of the induction assessment form for each child anaesthetised, where two induction scores are recorded | Free question over the contract parts, as completeness of two kinds of reading | Readings, anaesthetics, patients | One of the two scores is a kind of reading (`ciba`); the other is not in the kinds of reading and would be added as one. |
| A review of haemoglobin and tranexamic acid use in neonatal cardiac surgery | Free question needing draft parts, composed from `cardiac`, `preoperative_result`, `postoperative_result` and `exposure_intervals` | Laboratory results, drugs, operations, patients, the patient's details at birth | Nothing in the vocabulary; the drug is reached through the mapping view's concept. |
| A review of quality of recovery: pain scores in recovery, time in recovery, opioid use, antiemetic use | Catalogue capability (`recovery_time`, `exposure_intervals`) with free grouping | Readings, events, drugs, anaesthetics | A drug class, such as opioid or antiemetic, is not a kind the roles hold; it is a list of concepts the question carries, or a mapping the catalogue gains. |
| Fasting times, the incidence of aspiration, and the complications that followed | Free question needing draft parts | Facts found in the record, events, transfers, stays | The event vocabulary has no kind for aspiration, and the fasting hours are a fact that needs a structured field or a hospital-made table. |

## The counts

| Class | Requests |
| --- | --- |
| Catalogue capability with parameters | 3 |
| Free question over the contract parts | 1 |
| Free question needing draft parts | 2 |
| Not answerable from the anaesthesia record | 0 |

Parts named by the six: readings 4, anaesthetics 6, patients 4, drugs 3, events 2, laboratory results 2, the patient's details at birth 2, fluids 1, staff 1, operations 1, facts found 1, transfers 1, stays 1. Every request but the first needs at least one draft part, which is the case for promotion under the plan's third phase.

Gaps in the contract the six expose: a kind of reading for the second induction score; an event kind for aspiration; a way to name a drug class in a question; and the source of the fasting hours. Each is small, and each goes through the contract checker and the try command before it is published.

## The baseline

The plan's second package, the one clinician's minutes on the next ten questions handled by hand, has not started. Its summary will appear here.
