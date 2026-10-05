# Sources of the reference data

The files in this directory give the synthetic-data generator realistic values for an invented paediatric perioperative database. Every file is plain UTF-8 CSV with Unix line endings and a header row. All sources were retrieved on 3 October 2026. None of the files contains data about any real patient, and none contains a hospital or vendor name.

## growth_weight.csv and growth_stature.csv

**What they are.** These files hold the LMS parameters of the CDC growth charts for every whole month from 0 to 240 months, for males (`sex` = 1) and females (`sex` = 2), giving 482 rows each. Weight is in kilograms. Stature is recumbent length in centimetres for months 0 to 35 and standing height in centimetres for months 36 to 240. A value for a child at z-score *z* is `M * (1 + L * S * z) ** (1 / L)`, or `M * exp(S * z)` when L is zero.

**Where they came from.** The CDC data files at <https://www.cdc.gov/growthcharts/cdc-data-files.htm>, specifically:

- <https://www.cdc.gov/growthcharts/data/zscore/wtageinf.csv> (weight for age, birth to 36 months)
- <https://www.cdc.gov/growthcharts/data/zscore/wtage.csv> (weight for age, 2 to 20 years)
- <https://www.cdc.gov/growthcharts/data/zscore/lenageinf.csv> (length for age, birth to 36 months)
- <https://www.cdc.gov/growthcharts/data/zscore/statage.csv> (stature for age, 2 to 20 years)

**Licence.** These files are works of the United States Government and are in the public domain. The CDC asks for attribution and that no endorsement be implied (<https://www.cdc.gov/other/agencymaterials.html>). Redistribution is therefore permitted, and the CDC is acknowledged here as the source.

**How they were derived.** The CDC files give values at age 0 and then at half months (0.5, 1.5, 2.5 and so on). For each whole month, `build_data.py` takes L, M and S by linear interpolation between the two neighbouring CDC ages, which for every month after birth is the mean of the half-month values either side. Months 0 to 35 come from the infant files and months 36 to 240 from the 2 to 20 year files. Values are written to six decimal places. Running `python3 build_data.py` downloads the four files again and reproduces both CSVs byte for byte.

**Limitations.** The CDC charts describe children in the United States, mostly from surveys between 1963 and 1994, and are a reference rather than a standard. The WHO standards, which are preferred for children under two in Australia, were not used because their licence (CC BY-NC-SA 3.0 IGO) does not allow unrestricted redistribution. The median stature falls by about 0.2 cm between months 35 and 36, because recumbent length is slightly greater than standing height; this is expected and is not an error.

## vitals.csv

**What it is.** The 10th, 50th and 90th centiles of heart rate (beats per minute) and respiratory rate (breaths per minute) in thirteen age bands from birth to 18 years. `age_to_months` is exclusive.

**Where it came from.** Fleming S, Thompson M, Stevens R, Heneghan C, Plüddemann A, Maconochie I, Tarassenko L, Mant D. Normal ranges of heart rate and respiratory rate in children from birth to 18 years of age: a systematic review of observational studies. *Lancet* 2011;377(9770):1011–1018. doi:10.1016/S0140-6736(10)62226-X. The values come from Web Table 4 (respiratory rate) and Web Table 5 (heart rate) of the web appendix to the author manuscript in PubMed Central: <https://pmc.ncbi.nlm.nih.gov/articles/PMC3789232/> (file `NIHMS54714-supplement-Web_Appendix.pdf`).

**Licence.** The manuscript is free to read in PubMed Central but carries no open licence. The file reproduces only the published numerical reference values, which are facts, with citation; no text, table layout or figure from the paper has been copied. We consider this permissible, but it rests on the general principle that factual data are not protected by copyright rather than on an explicit grant from the publisher.

**How it was derived.** The age bands 0–3 m, 3–6 m, 6–9 m, 9–12 m, 12–18 m, 18–24 m, 2–3 y, 3–4 y, 4–6 y, 6–8 y, 8–12 y, 12–15 y and 15–18 y were converted to months. The 10th centile, median and 90th centile were transcribed by hand from the rendered page and checked, number by number, against values extracted from the PDF's text layer; the two agreed in every cell. The heart-rate table also has a separate "Birth" row (10th 107, median 127, 90th 148), which describes the immediate neonatal period. That row has been left out, because it is a point rather than a band and the 0–3 month band already covers age 0.

**Limitations.** Fleming's ranges come from awake, healthy children, mostly in community and clinical settings. Heart rate and respiratory rate under anaesthesia are usually lower, so the generator should not use these values unchanged for intraoperative observations.

## blood_pressure.csv

**What it is.** The median and an approximate standard deviation of systolic, mean and diastolic non-invasive blood pressure (mmHg) during the surgical phase of anaesthesia, by age, from birth to 18 years.

**Where it came from.** de Graaff JC, Pasma W, van Buuren S, Duijghuisen JJ, Nafiu OO, Kheterpal S, van Klei WA. Reference values for noninvasive blood pressure in children during anesthesia: a multicentered retrospective observational cohort study. *Anesthesiology* 2016;125(5):904–913. doi:10.1097/ALN.0000000000001310. The values come from tables 4, 5 and 6 of Supplemental Digital Content 1, in the author manuscript in PubMed Central: <https://pmc.ncbi.nlm.nih.gov/articles/PMC5259808/> (file `NIHMS825208-supplement-supplemental.pdf`).

**Licence.** As with Fleming, the manuscript is free to read but carries no open licence. Only numerical reference values have been taken, with citation, and they have been combined and transformed as described below rather than reproduced as published.

**How it was derived.** The paper gives, for each sex, the −2, −1, 0, +1 and +2 SD values at twenty ages (birth; 1, 2, 3, 4, 5, 6 and 9 months; and 1, 2, 3, 4, 5, 6, 8, 10, 12, 14, 16 and 18 years). The surgical-phase tables were used rather than the preparation-phase tables, because they describe blood pressure once surgery is under way. The −1, 0 and +1 SD values were transcribed by hand from the rendered pages and checked against the PDF's text layer, and the two agreed in every cell. For each age:

- the `_p50` columns are the mean of the male and female 0 SD values, which the authors' model treats as the median;
- the `_sd` columns are the mean, over the two sexes, of half the distance between the +1 SD and −1 SD values.

Each row applies from its tabulated age up to, but not including, the next tabulated age. The 18-year values have been applied to the band from 216 to 228 months so that the final age has a band of its own.

**Limitations.** The source data are from 116,362 anaesthetics in the United States (the Multicenter Perioperative Outcomes Group). The published distributions are skewed, with the +2 SD value further from the median than the −2 SD value, especially in neonates, so a symmetric normal distribution built from these columns will understate high readings. Combining the sexes loses a difference that is small (usually 1 to 3 mmHg) but real.

## medications.csv

**What it is.** A list of 115 generic medicine names used in paediatric anaesthesia and perioperative care, in upper case, each with a plain therapeutic class.

**Where it came from.** The list was written from general pharmacological knowledge. It was not copied from the Australian Medicines Terminology, the PBS schedule, MIMS, RxNorm or any other vocabulary, and it contains no brand names.

**Licence.** The generic name of a medicine is a fact, and a short list of such names chosen for this purpose is the project's own work. It may be redistributed under the project's licence.

**How it was derived.** The names follow the Australian names in long clinical use, such as PARACETAMOL, ADRENALINE, SUXAMETHONIUM, FRUSEMIDE, LIGNOCAINE, CEPHAZOLIN, AMOXYCILLIN, THIOPENTONE and AMETHOCAINE. Since 2016 the TGA has moved several of these to international names (for example furosemide, lidocaine and cefazolin), so a real Australian record may show either form. Fluids are listed by their usual description, such as SODIUM CHLORIDE 0.9% and COMPOUND SODIUM LACTATE.

**Limitations.** The classes are deliberately coarse, and a medicine with more than one perioperative role (clonidine, for example) appears only once under its most common role.

## durations.csv (not produced)

The brief asked for anaesthetic duration percentiles derived from the VitalDB case table at <https://api.vitaldb.net/cases>. This file has not been produced. The data use agreement on VitalDB's own site (<https://vitaldb.net/dataset/>, section "Data Use Agreement", read on 3 October 2026) states that the dataset is released under the Creative Commons Attribution-NonCommercial-ShareAlike 4.0 licence, and it also requires users not to disclose the data to anyone outside their organisation without the provider's consent. A non-commercial, share-alike licence is not compatible with an ordinary permissive open-source licence, and the non-disclosure clause leaves doubt about publishing even derived statistics, so the condition set for this file was not met.

The same data are also published on PhysioNet as VitalDB 1.0.0 (<https://physionet.org/content/vitaldb/1.0.0/>, file `clinical_data.csv`) under the Creative Commons Attribution 4.0 licence. If the project is satisfied that the PhysioNet release governs, durations could be derived from that copy with attribution to Lee HC et al., *Scientific Data* 2022;9:279. That decision has been left to the maintainers. Even then, the cases are mostly adults at a single hospital in Seoul, so the durations would not describe a paediatric case mix.

## build_data.py

This script uses only the Python standard library. Running `python3 build_data.py` from any directory downloads the four CDC files and rewrites `growth_weight.csv` and `growth_stature.csv` beside the script. It was run on 3 October 2026 and reproduced both files exactly. The other files are transcriptions or hand-written lists, and their derivation is described above.

## Values that are invented

Two meanings have no file here, and the generator writes invented values for them: end-tidal carbon dioxide, spread around 38 mmHg and kept between 25 and 55, and end-tidal volatile agent, spread around 2.2 per cent and kept between 0.4 and 4.0. They are plausible and are not taken from any study.

## procedures.csv and diagnoses.csv

`procedures.csv` lists forty operations and investigations that children commonly have under anaesthesia, and `diagnoses.csv` lists thirty ICD-10 codes for the conditions that lead to them. Both lists were written for this project from general knowledge. The names are ordinary clinical terms and the codes are those of the World Health Organization's ICD-10. Neither list comes from any hospital, and neither says how often anything happens.
