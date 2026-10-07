# The roles of an anaesthetic record

This page describes what an anaesthetic record holds, written without reference to any hospital, vendor or database. A role is one thing that the record must be able to tell an audit. A hospital's system may hold a role in one column, across several tables, inside a text field, as a row of a table of events, or not at all. Each hospital has a map that says which of its own tables and columns play each role, written as one SQL view for each role, and an audit written against the role views then runs unchanged at any hospital.

`contract.json`, beside this page, holds the same model as data: every view, its columns, their types, the words that the proposer looks for in a data dictionary, and the vocabularies. When this page and `contract.json` differ, `contract.json` is the one that the code reads, and this page should be corrected to match it.

## The rules of the record

The role views assume the following of every hospital's record, and a map must make each of them true.

1. One row of `role_anaesthetic` is one anaesthetic, and its key identifies that anaesthetic and no other.
2. Every time is in local time, as the hospital's clocks showed it. No view converts a time to another zone.
3. A reading, an event, a drug given or a device belongs to an anaesthetic by its link to that anaesthetic, and never because its time falls within the anaesthetic. A ward observation taken in the same hour is not part of the anaesthetic's record.
4. A view keeps the rows that an audit leaves out, such as test patients, anaesthetics with no stop and values that were not accepted, so that the standard counts can see them.
5. A view never joins in a way that repeats a row of the thing it describes, so that the view's key identifies its row.
6. An empty value means that the record does not say. A value that the record holds but that cannot be read as the role's type is also empty.
7. A key compares equal with itself within one hospital only, and no key is carried from one hospital to another.
8. A value is in the unit that its role or its kind fixes, and the view converts a value that the hospital holds in another unit.

## The types

| Type | Meaning |
|---|---|
| key | Any type that compares equal with itself within one hospital, such as a whole number or text. |
| date | A date without a time. |
| datetime | A date and a time of day, in local time. |
| number | A number that may hold a fraction. |
| whole | A whole number. |
| flag | The whole number 1 or 0, and never empty. |
| flag_or_empty | The whole number 1 or 0, or empty where the record does not say. |
| kind | Text that is one of the kinds of the vocabulary that the column names. |
| text | Text as the hospital records it. |

## The roles

Every map supplies the first three views, which the neonatal audit reads, and their columns do not change. The further views are a starter set for the outcomes and covariates below, which the owner will trim; a map supplies a further view only where the hospital records it. In each table below, "Needed" says whether the audit written so far reads the column.

### role_patient: one row for each patient

This view holds one row for each patient, test and training patients included, so that an audit can leave them out. The hospital usually holds it in the patient table of its system, which has one row for each person. The date of death is often held in a second patient table, or loaded from a registry of deaths.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| patient_key | key | yes | The hospital's own identifier of the patient, the same value that `role_anaesthetic.patient_key` holds. |
| birth_date | date | yes | The date of birth, to the day, so that an age in days counts calendar dates. |
| death_date | date | yes | The date of death where the hospital knows of it, and empty otherwise. |
| is_test | flag | yes | 1 for a test or training patient who is not a real person, and 0 for every other patient. An empty flag in the source counts as 0. |

### role_anaesthetic: one row for each anaesthetic

This view holds one row for each episode of anaesthesia or sedation given by an anaesthetist, including those whose start or stop is missing or out of order. The hospital usually holds it in an anaesthetic record or anaesthesia episode of its own, which is linked to a theatre case or to an encounter of its own. It links to `role_patient` by `patient_key`.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| anaesthetic_key | key | yes | The hospital's own identifier of the anaesthetic, the same value that `role_reading.anaesthetic_key` holds. |
| patient_key | key | yes | The patient who had the anaesthetic, as `role_patient.patient_key`. |
| start_time | datetime | yes | When the anaesthetic started, in local time. |
| stop_time | datetime | yes | When the anaesthetic stopped, in local time, and empty where no stop was recorded. |

### role_reading: one row for each value charted in an anaesthetic's record

This view holds one row for each value charted in an anaesthetic's own record, by a monitor or by hand, including values that were not accepted and values that are not numbers. The hospital usually holds readings in a table of measurements or observations, often the largest in its system, reached from the anaesthetic through the record or sheet on which they were charted. It links to `role_anaesthetic` by `anaesthetic_key`, and its rows are identified by the anaesthetic, the kind and the time.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| anaesthetic_key | key | yes | The anaesthetic whose record holds the reading, as `role_anaesthetic.anaesthetic_key`. |
| kind | kind | yes | What was measured, as one of the kinds of readings below. |
| reading_time | datetime | yes | When the value was taken, in local time, and not when it was filed. |
| value | number | yes | The value as a number in the kind's own unit, and empty where the charted value is not a number. |
| accepted | flag | yes | 1 where the value counts as valid, and 0 where it was marked as an artefact, rejected or superseded. An empty flag in the source counts as 1. |

The kinds of readings are these. A map gives the hospital's own codes for each kind that it can find, and every other value charted in the record is of the kind `other`.

| Kind | Meaning |
|---|---|
| map_arterial | A mean arterial pressure from an arterial line, in mmHg. The audit needs it. |
| map_cuff | A mean arterial pressure from a non-invasive cuff, in mmHg. The audit needs it. |
| heart_rate | A heart rate, in beats a minute. |
| spo2 | An oxygen saturation by pulse oximetry, in per cent. |
| etco2 | An end-tidal carbon dioxide, in mmHg. |
| temperature | A body temperature, in degrees Celsius. |
| other | Any other value charted in the record, which the counts use to see whether the record is reached at all. |

### role_patient_detail: the patient's details at birth

This view holds one row for each patient whose record holds any of these details. The hospital may hold them in a second patient table, in the child's birth history, in the mother's delivery record where that is linked to the baby, or as a measurement charted on the newborn. It links to `role_patient` by `patient_key`.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| patient_key | key | no | The patient, as `role_patient.patient_key`. |
| sex | kind | no | The sex recorded at birth, as one of the kinds of the sex vocabulary. |
| gestation_weeks | number | no | The gestational age at birth in weeks, with any days as a fraction of a week. |
| birth_weight_grams | number | no | The weight at birth in grams. |

### role_stay: one row for each hospital stay

This view holds one row for each hospital stay, from admission to discharge, day stays included. The hospital usually holds it in its table of hospital encounters or admissions. It links to `role_patient` by `patient_key`, and `role_anaesthetic_detail` and `role_unit_stay` link to it by `stay_key`.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| stay_key | key | no | The hospital's own identifier of the stay. |
| patient_key | key | no | The patient whose stay it is, as `role_patient.patient_key`. |
| admit_time | datetime | no | When the patient was admitted, in local time. |
| discharge_time | datetime | no | When the patient was discharged, or died in hospital, in local time, and empty while the patient is still in hospital. |
| unplanned | flag_or_empty | no | 1 for an emergency or other unplanned admission, 0 for a planned one, and empty where the record does not say. The hospital usually records this as an admission type or category, whose values a person translates. |

### role_anaesthetic_detail: the anaesthetic's covariates

This view holds one row for each anaesthetic whose record holds any of these details. The hospital holds them partly in the anaesthetic record and partly in the theatre case or surgical log, with the pre-anaesthetic assessment for the grade and the weight. It links to `role_anaesthetic` by `anaesthetic_key` and to `role_stay` by `stay_key`.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| anaesthetic_key | key | no | The anaesthetic, as `role_anaesthetic.anaesthetic_key`. |
| stay_key | key | no | The hospital stay in which the anaesthetic took place, as `role_stay.stay_key`, and empty where it belongs to no stay. |
| technique | kind | no | The kind of anaesthetic, as one of the kinds of the technique vocabulary. A record may hold several, and the map chooses the main one. |
| asa_grade | whole | no | The ASA physical status grade from 1 to 6, without any letter for an emergency. |
| is_emergency | flag_or_empty | no | 1 where the operation was done as an emergency, 0 where it was planned, and empty where the record does not say. The hospital may record it on the theatre case or booking, or as an E after the ASA grade. |
| weight_kg | number | no | The patient's weight at the anaesthetic, in kilograms. The hospital may hold it on the pre-anaesthetic assessment, as a measurement charted on the day, or on the theatre case. |
| planned_icu | flag_or_empty | no | 1 where intensive care after the operation was planned before it began, 0 where it was not, and empty where the record does not say. The hospital usually records it on the booking as the planned destination or as a request for a bed. |
| unplanned_return | flag_or_empty | no | 1 where the operation was recorded as an unplanned return to theatre, 0 where it was not, and empty where the record does not say. |

### role_operation: one row for each procedure done under an anaesthetic

This view holds one row for each procedure done under an anaesthetic, so that an anaesthetic for several procedures has several rows. The hospital usually holds the procedures of a theatre case or surgical log, with a lookup table of the procedures that it offers. It links to `role_anaesthetic` by `anaesthetic_key`; at many hospitals that link runs from the anaesthetic record to its theatre case, so the map must take care that a case with two anaesthetics does not repeat its procedures.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| anaesthetic_key | key | no | The anaesthetic under which the procedure was done, as `role_anaesthetic.anaesthetic_key`. |
| procedure_code | text | no | The hospital's own code of the procedure, or a national code where the hospital uses one. |
| procedure_name | text | no | The name of the procedure as the hospital's list gives it. |
| specialty | kind | no | The surgical specialty of the procedure, as one of the kinds of the specialty vocabulary, which gives the operation's kind. |
| is_cardiac | flag_or_empty | no | 1 where the procedure was a cardiac operation or a cardiac catheter procedure, 0 where it was not, and empty where nothing says. The hospital may record it through the specialty, the procedure's code, or a diagnosis of the stay. |

### role_unit_stay: one row for each period in one unit

This view holds one row for each period that a patient spent in one unit during a hospital stay. The hospital usually holds it in the movements of the encounter, with one row for each transfer into or out of a unit and a list of units that says what kind each one is. It links to `role_stay` by `stay_key`.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| stay_key | key | no | The hospital stay, as `role_stay.stay_key`. |
| unit_kind | kind | no | The kind of unit, as one of the kinds of the unit vocabulary. |
| entered_time | datetime | no | When the patient entered the unit, in local time. |
| left_time | datetime | no | When the patient left the unit, in local time, and empty while the patient is still there. |

### role_event: one row for each milestone of an anaesthetic

This view holds one row for each timed milestone of an anaesthetic. The hospital usually holds them as events of the anaesthetic record, or as the timing events of the theatre case, each with a code for its kind. It links to `role_anaesthetic` by `anaesthetic_key`.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| anaesthetic_key | key | no | The anaesthetic, as `role_anaesthetic.anaesthetic_key`. |
| kind | kind | no | The kind of event, as one of the kinds of the event vocabulary. |
| event_time | datetime | no | When the event happened, in local time. |

### role_drug: one row for each dose or infusion action

This view holds one row for each dose given during an anaesthetic, and for each start, change of rate and stop of an infusion. The hospital usually holds them as administrations in its medication record, linked to their orders, and sometimes as drug values charted on the anaesthetic record. It links to `role_anaesthetic` by `anaesthetic_key`.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| anaesthetic_key | key | no | The anaesthetic during which the drug was given, as `role_anaesthetic.anaesthetic_key`. |
| drug | text | no | The drug, as the hospital's own name or code for it. |
| action | kind | no | What was done, as one of the kinds of the drug action vocabulary. |
| amount | number | no | The dose, or the rate of an infusion, as a number in the unit beside it. |
| unit | text | no | The unit of the amount, as the hospital records it. |
| route | text | no | The route by which the drug was given, as the hospital records it. |
| given_time | datetime | no | When the dose was given or the infusion changed, in local time. |

### role_device: one row for each device placed

This view holds one row for each airway device, line or catheter placed for an anaesthetic. The hospital usually holds the lines, drains and airways of the patient's record, each with its placement and removal; where the device belongs to the patient rather than to the anaesthetic, the map must find the link. It links to `role_anaesthetic` by `anaesthetic_key`.

| Column | Type | Needed | Meaning |
|---|---|---|---|
| anaesthetic_key | key | no | The anaesthetic for which the device was placed, as `role_anaesthetic.anaesthetic_key`. |
| kind | kind | no | The kind of device, as one of the kinds of the device vocabulary. |
| placed_time | datetime | no | When the device was placed, in local time. |
| removed_time | datetime | no | When the device was removed, in local time, and empty while it stays in place. |

## The vocabularies

A map translates the hospital's own codes into these kinds. Every vocabulary except the drug actions ends with `other`, which holds any value that the map has not translated.

| Vocabulary | Kinds |
|---|---|
| sex | female, male, indeterminate, other |
| technique | general, regional, sedation, local, other |
| specialty | cardiac, general, neurosurgery, orthopaedic, ear_nose_throat, dental, ophthalmic, plastic, urology, imaging, other |
| unit | intensive_care, high_dependency, theatre, recovery, emergency, ward, other |
| event | anaesthesia_start, induction, airway_secured, incision, bypass_on, bypass_off, surgery_end, extubation, anaesthesia_stop, other |
| device | endotracheal_tube, supraglottic_airway, arterial_line, central_line, peripheral_line, regional_catheter, other |
| drug_action | dose, infusion_start, rate_change, infusion_stop, not_given |

## The outcomes and covariates

An audit works out each outcome from the roles, so that no map has to. The starter set is below.

| Outcome or covariate | Worked out from | How |
|---|---|---|
| Death within a number of days | `role_patient.death_date`, `role_anaesthetic.start_time` | The patient died on a date from the day of the anaesthetic's start to that many days after it. |
| Unplanned admission to intensive care | `role_unit_stay`, `role_anaesthetic_detail.planned_icu`, `role_anaesthetic.stop_time` | The patient entered an intensive care unit within 24 hours after the anaesthetic's stop, intensive care had not been planned, and the patient was not already there at the start. |
| Return to theatre within 30 days | `role_anaesthetic`, `role_operation`, `role_anaesthetic_detail.unplanned_return` | The same patient had another anaesthetic with an operation within 30 days after this one's stop. Where the hospital records a return as unplanned, the audit can count only those. |
| Length of stay | `role_stay`, `role_anaesthetic_detail.stay_key` | The time from admission to discharge of the stay in which the anaesthetic took place, and the time from the anaesthetic's stop to that discharge. |
| Readmission within 30 days | `role_stay` | The patient began another unplanned stay within 30 days after the discharge of the stay in which the anaesthetic took place. |
| Gestational age at birth | `role_patient_detail.gestation_weeks` | As recorded. |
| Weight at the anaesthetic | `role_anaesthetic_detail.weight_kg` | As recorded. |
| ASA grade | `role_anaesthetic_detail.asa_grade` | As recorded. |
| Emergency status | `role_anaesthetic_detail.is_emergency` | As recorded. |
| Cardiac operation | `role_operation.is_cardiac` | Any procedure under the anaesthetic that was cardiac. |
| The operation's kind | `role_operation.specialty` | The specialty of the main procedure, which the audit chooses by its own rule. |

## The question that the first three views answer

The neonatal audit asks: among neonates, children under 28 days old at the start, who had an anaesthetic, how many minutes of each anaesthetic were spent with a mean arterial pressure below 40, and how many of those children died within 90 days? To answer it, the audit needs each anaesthetic with its patient, its start and its stop; the patient's date of birth and date of death; and every accepted mean arterial pressure reading during the anaesthetic, with its time, its value, and whether it came from a cuff or an arterial line.
