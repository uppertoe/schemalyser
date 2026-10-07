// The wording of the page Describe the record (describe.html), in one place. The gate that takes the page offline
// uses the existing page's own wording, from strings.ts, so that the two pages say the same thing.

const plural = (n: number, one: string, many: string) => `${n.toLocaleString('en-AU')} ${n === 1 ? one : many}`;
const rows = (n: number) => plural(n, 'row', 'rows');

export const describeStrings = {
  title: 'Describe the record',
  intro:
    "This page is for a clinician and a colleague who can run SQL against the hospital's reporting database, sitting together once for each hospital. Schemalyser proposes which of the hospital's tables and columns hold each part of the anaesthetic record, and the colleague confirms or corrects each one. What the two of you settle is written to the hospital folder, which every audit then reads.",
  back: 'The existing page, for an audit folder of the earlier kind, is still available.',
  backLink: 'Open the existing page',
  privateNote:
    "Everything that you load or paste here stays in this browser tab. Schemalyser reads it in the tab, and the only copy that it makes is the hospital folder, which it writes where you choose.",

  steps: [
    '1. Take this page offline',
    '2. Load the data dictionary',
    '3. Choose the hospital folder',
    '4. Propose the map',
    '5. Run the tables and columns query',
    '6. Confirm each binding',
    '7. Settle the local codes',
    '8. Run the counts',
    '9. Write the hospital folder',
  ],

  // 1. Offline.
  offlineWhat:
    'Whoever is at the keyboard waits for the page to load, then takes this browser tab offline, so that nothing that you load or paste later can leave it. The SQL window on the same computer stays connected.',
  offlineDone: 'This page is offline. You can now load the dictionary and choose the hospital folder.',
  locked:
    'This page has gone back online, so Schemalyser has stopped and let go of the dictionary and of everything pasted. If you wrote the hospital folder, nothing is lost: reload the page, take it offline again, load the dictionary and choose the folder.',

  // 2. The dictionary.
  dictionaryWhat:
    "The data dictionary is the vendor's own description of every table and column of the reporting database. The colleague exports it from the vendor's dictionary tool as a plain table, a CSV or tab-separated file with one row for each column and a row of headings. Schemalyser needs it to propose which columns hold each part of the record, and to show the vendor's own definition beside each one.",
  dictionaryPrivate:
    'The dictionary is licensed. Schemalyser reads it in this tab only and sends it nowhere. Its descriptions appear on this page and in the hospital folder, as the evidence for each binding, and nowhere else. A copy of the file goes into the folder only if you tick the box in step 9.',
  dictionaryHeadings:
    'Schemalyser finds the table, the column, the description, the data type and the key under the usual headings, such as TABLE_NAME, COLUMN_NAME and DESCRIPTION. Only the table and the column are required. If your export names them differently, name its headings under Name the headings yourself.',
  dictionaryLabel: 'The dictionary file, with one row for each column:',
  tablesLabel: 'If you have one, a second file with one row for each table, giving its description and its primary key:',
  tablesNote:
    "The second file is optional. Its primary keys help Schemalyser find how the tables join, and its descriptions help it choose the table for each role.",
  headingsSummary: 'Name the headings yourself',
  headingsWhat:
    "Write the heading exactly as it appears in the first row of the file, for any field that Schemalyser could not find. Leave the others empty.",
  headingFields: [
    ['table', 'The heading of the table names:'],
    ['column', 'The heading of the column names:'],
    ['description', 'The heading of the descriptions:'],
    ['data_type', 'The heading of the data types:'],
    ['key', 'The heading that marks a primary key:'],
  ] as [string, string][],
  dictionaryLoad: 'Load the dictionary',
  dictionaryReading: 'Schemalyser is reading the dictionary.',
  dictionaryReceipt: (r: { tables: number; columns: number; described: number; keyed: number; skipped: number }) =>
    `Schemalyser has read a dictionary of ${plural(r.columns, 'column', 'columns')} of ${plural(r.tables, 'table', 'tables')}. ${
      r.described === r.columns ? 'Every column has a description.' : `${plural(r.described, 'column has', 'columns have')} a description.`
    } ${plural(r.keyed, 'table has', 'tables have')} a primary key.${
      r.skipped ? ` Schemalyser left out ${rows(r.skipped)} whose names are not plain table and column names.` : ''
    }`,
  dictionaryFailed: 'Schemalyser could not read the file as a dictionary.',
  dictionaryNeeded: 'Load the dictionary first, then choose Load the dictionary.',

  // 3. The folder.
  folderWhat:
    "The hospital folder holds what this page settles: the map, the codes, the counts, every query that the colleague ran and every result that came back. It lives on the hospital's own storage. If one already exists, choose it, and Schemalyser restores where you left off. If this is the first sitting, choose an empty folder, or skip this step and choose where to write the folder in step 9.",
  folderLabel: 'The hospital folder, if one exists:',
  folderNote:
    "Schemalyser reads only the files that it wrote into the folder, and lets go of anything else in it at once. Your browser may ask you to confirm that you want to upload the folder: it is read into this tab only, and nothing is sent.",
  folderReading: 'Schemalyser is reading the hospital folder.',
  folderEmpty:
    'The chosen folder holds no map yet, so Schemalyser starts a new hospital folder. It will write into this folder in step 9.',
  folderReceipt: (r: { map: boolean; tables: boolean; codes: number; counts: number; dictionary: boolean; confirmations: number; queries: number }) =>
    [
      r.map ? 'Schemalyser has restored the map from the hospital folder.' : 'The chosen folder holds no map yet, so Schemalyser starts a new hospital folder.',
      r.confirmations ? `It holds ${plural(r.confirmations, 'recorded answer', 'recorded answers')}.` : '',
      r.queries ? `It holds ${plural(r.queries, 'query', 'queries')} that the page offered before.` : '',
      r.tables ? 'The result of the tables and columns query is restored.' : '',
      r.codes ? `The codes of ${plural(r.codes, 'vocabulary are', 'vocabularies are')} restored.` : '',
      r.counts ? `${plural(r.counts, 'count is', 'counts are')} restored.` : '',
      r.dictionary ? 'The folder kept a copy of the dictionary, and Schemalyser has loaded it.' : '',
    ].filter(Boolean).join(' '),
  folderNoDictionary:
    'The folder holds no copy of the dictionary. To see the definitions beside each binding, to choose a replacement or to check the folder, load the dictionary in step 2 as well.',
  folderFailed: 'Schemalyser could not read the chosen folder.',

  checkHeading: 'Checking a folder that already exists',
  checkWhat:
    "After a change to the database, or a new release of the vendor's system, the folder may no longer be right. Schemalyser can propose the map again from the dictionary, apply the recorded answers in their order and say whether the result is the same as the folder's map. It then lists every query that the folder records with its earlier result. The colleague runs each again and pastes the new result, and Schemalyser lists what has changed.",
  checkButton: 'Check that this folder is still right',
  checkSame: "The map that Schemalyser rebuilt from the dictionary and the recorded answers is the same as the folder's map.",
  checkDiffers: (n: number) =>
    `The map that Schemalyser rebuilt from the dictionary and the recorded answers differs from the folder's map in ${plural(n, 'place', 'places')}:`,
  checkNoDictionary:
    'Schemalyser cannot rebuild the map without the dictionary, so it has checked only the queries. Load the dictionary in step 2 and choose Check that this folder is still right again to rebuild the map as well.',
  checkNoQueries: 'The folder records no query yet.',
  checkQuery: (number: number, file: string, step: string) => `Query ${number}, saved as ${file}, which the page offered under ${step}`,
  checkPrevious: (pasted: string | null, database: string | null) =>
    pasted
      ? `This is the earlier result, pasted on ${pasted.replace('T', ' at ')}${database ? `, from the ${database === 'training' ? 'training' : 'production'} database` : ''}.`
      : 'No result was pasted for this query.',
  checkPasteLabel: 'The new result of this query, copied from the results grid with its headers:',
  checkCompare: 'Compare with the earlier result',
  checkNoChange: 'Schemalyser finds no difference between the earlier result and the new one.',
  checkChanges: 'Schemalyser finds these differences between the earlier result and the new one:',

  // 4. The proposal.
  proposeWhat:
    "Schemalyser compares the words of each role of the anaesthetic record with the dictionary's descriptions and names, and proposes the table and column that most likely hold each attribute. It does this by fixed rules, in this tab, without any model, so that the same dictionary always gives the same proposal. For each attribute the list below gives what the role means, the proposed table and column, the dictionary's own definition of that column, how confident the proposal is and the other columns that came close.",
  proposeButton: 'Propose the map',
  proposeAgain: 'The map is already proposed. Schemalyser keeps every answer that you have given, so it does not propose the map again here.',
  proposeProgress: (done: number, total: number) =>
    done === 0 ? 'Schemalyser is reading the dictionary into its index.' : `Schemalyser has proposed ${done} of ${total} roles.`,
  proposeDone: (roles: number, drafted: number) =>
    `Schemalyser has proposed ${drafted} of the ${roles} roles. A role for which the dictionary holds no fitting table is listed as such, and you can name its table in step 6.`,
  proposeWaiting: 'This step opens once the dictionary is loaded.',
  roleUndrafted: 'The dictionary holds no table that fits this role, so Schemalyser has not proposed it.',
  roleRequired: 'Every audit reads this role.',
  roleFurther: 'An audit reads this role only where the hospital records it.',
  rowsAttribute: 'One row of the role',
  proposedLabel: 'Proposed:',
  nothingProposed: 'Nothing in the dictionary fits this attribute.',
  definitionLabel: "The dictionary's definition:",
  noDefinition: 'The dictionary gives no definition of this column.',
  evidenceLabel: 'Why Schemalyser proposed it:',
  confidence: { high: 'High confidence', medium: 'Medium confidence', low: 'Low confidence', none: 'No candidate fits' } as Record<string, string>,
  alternativesLabel: 'Other columns that came close:',

  // 5. The tables and columns query.
  tablesWhat:
    "The tables and columns query asks SQL Server which of the tables that the proposal names exist in this database, with their columns and the number of rows in each table. It reads only SQL Server's own records of its tables and columns, and no row of any table, so it is safe to run on production. Schemalyser uses the result to mark each binding as present, missing or large.",
  tablesWaiting: 'This step opens once the map is proposed.',
  tablesWrite: 'Write the tables and columns query',
  tablesNames: (n: number) => `The query asks about the ${plural(n, 'table', 'tables')} that the proposal and its alternatives name.`,
  tablesCopy: 'Copy the tables and columns query',
  tablesHow: [
    'Choose Copy the tables and columns query, paste it into your SQL window and run it.',
    'In SQL Server Management Studio, click the empty square at the top left of the results grid to select all of it, then right-click and choose Copy with Headers.',
    'Paste the result into the box below, and choose Read the result.',
  ],
  tablesPasteLabel: 'The result of the tables and columns query, copied from the results grid with its headers:',
  tablesRead: 'Read the result',
  tablesReceipt: (r: { tables: number; columns: number; sized: number; asked: number; absent: number }) =>
    `Schemalyser has read the result of the tables and columns query: ${plural(r.columns, 'column', 'columns')} of ${plural(r.tables, 'table', 'tables')}, with the number of rows for ${r.sized} of them.${
      r.absent ? ` ${plural(r.absent, 'table', 'tables')} that the query asked about did not come back. A table that did not come back may not exist here, or this login may not be allowed to see it.` : ' Every table that the query asked about came back.'
    } Each binding in step 6 is now marked as present, missing or large.`,
  tablesDoubt:
    'More than half of the tables that the query asked about did not come back. The SQL window may be connected to another database, or this login may see only part of it. Please check the connection before you go on.',
  tablesUnreadable:
    'Schemalyser could not read the pasted text as the result of the tables and columns query. Each row needs the ten columns that the query returns, from TABLE_SCHEMA to TABLE_ROWS. Please copy the whole results grid with Copy with Headers, and paste it again.',

  // 6. Confirming.
  confirmWhat:
    'The colleague confirms each binding from what they know of the database. For each one, choose Yes, this is right where the proposed column holds what the role means; choose Choose another to pick one of the other columns or to write the right one; and choose Not sure where neither of you can say. Schemalyser records each answer with its date. A Not sure is gathered below into a list of questions for the team that looks after the database.',
  confirmWaiting: 'This step opens once the map is proposed.',
  tally: (t: { confirmed: number; corrected: number; not_sure: number; remaining: number; total: number }) =>
    `Of ${t.total} bindings, ${t.confirmed} ${t.confirmed === 1 ? 'is' : 'are'} confirmed, ${t.corrected} corrected and ${t.not_sure} not sure, and ${t.remaining} ${t.remaining === 1 ? 'remains' : 'remain'}.`,
  yes: 'Yes, this is right',
  another: 'Choose another',
  notSure: 'Not sure',
  anotherLabel: 'Choose one of the other columns, or write the right one as TABLE.COLUMN:',
  anotherRowsLabel: 'Write the name of the table that holds one row for each, and Schemalyser proposes the role again from it:',
  anotherWritten: 'Or write it here:',
  anotherNone: 'Write the column in the box below',
  anotherUse: 'Use this one',
  answered: {
    yes: (date: string) => `Confirmed on ${date}.`,
    no: (date: string, replacement: string) => `Corrected to ${replacement} on ${date}.`,
    notSure: (date: string) => `Marked as not sure on ${date}.`,
  },
  presence: {
    present: 'Present in the database.',
    missing: (names: string[]) => `Missing: the result of the tables and columns query does not hold ${names.join(', ')}.`,
    large: (table: string, n: number) => `Present, and large: ${table} holds about ${rows(n)}.`,
    unknown: 'Not yet checked against the database.',
  },
  questionsHeading: 'Questions for the database team',
  questionsWhat:
    'These are the bindings that neither of you could confirm. You can copy them as a note to the team that looks after the database, and answer each one here once they reply.',
  questionsNone: 'There are no questions for the database team yet.',
  questionsCopy: 'Copy the questions',
  questionsNoteHead: 'Questions about the reporting database, from the description of the anaesthetic record:',
  confirmFailed: 'Schemalyser could not record that answer.',

  // 7. The codes.
  codesWhat:
    "Some attributes hold the hospital's own codes, such as the kind of each reading or the type of each event, and only the hospital's list says which code means a mean arterial pressure or an induction. For each of them, Schemalyser writes the list of what is charted: a query that counts the codes used on the anaesthetics of one year, with their names. The colleague runs it and pastes the result, and the two of you choose which codes belong to each kind.",
  codesWaiting: 'This step opens once the map is proposed.',
  yearLabel: 'The year whose anaesthetics the lists and the last count read:',
  yearNote: 'A recent full year is usually best, because it shows how the record is charted now.',
  codesSafe: (limit: string) =>
    `Each list is a script in two parts, which is safe to run on production. Part 1 puts at most ${limit} anaesthetics of the year into a temporary table, #cohort, from the anaesthetic's own tables. Part 2 reaches the codes from #cohort by keys alone, so that it reads only the rows of those anaesthetics and never the whole of a large table.`,
  vocabularyHeading: (key: string) => `The codes of ${key}`,
  vocabularyBound: (bound: string, lookup: string | null) =>
    lookup ? `The map reads the codes from ${bound}, and their names from ${lookup}.` : `The map reads the codes from ${bound}. The dictionary names no table that gives their names, so the list shows the codes alone.`,
  vocabularyReason: {
    unbound: 'The map binds no column to this attribute yet, so there are no codes to settle.',
    unlinked: 'This role does not link to an anaesthetic or a patient, so its codes are settled in a later screen.',
    unbound_link: 'The map does not yet say how this role links to its anaesthetic, so Schemalyser cannot write the list.',
  } as Record<string, string>,
  chartedWrite: 'Write the list of what is charted',
  chartedCopy: 'Copy the list of what is charted',
  chartedPasteLabel: 'The result of the list of what is charted, copied from the results grid with its headers:',
  chartedRead: 'Read the list',
  chartedReceipt: (n: number, year: number) =>
    `Schemalyser has read the list of what is charted on the anaesthetics of ${year}: ${plural(n, 'code', 'codes')}. Choose the kind of each code that you recognise, and leave the others as not chosen.`,
  chartedEmpty: (year: number) =>
    `The list of what is charted on the anaesthetics of ${year} came back empty. Either no anaesthetic started in ${year}, or the map does not yet reach these rows from the anaesthetic. Please check the bindings of this role in step 6, or choose another year.`,
  chartedColumns: ['Code', 'Rows', 'Anaesthetics', 'Name', 'Kind'],
  notChosen: 'Not chosen',
  codesSave: 'Save the codes',
  codesSaved: (n: number, date: string) => `Schemalyser recorded ${plural(n, 'code', 'codes')} for this vocabulary on ${date}.`,
  codesNoneSaved: 'No code has been chosen for this vocabulary yet.',
  kindMeaning: (kind: string, meaning: string) => `${kind}: ${meaning}`,

  // 8. The counts.
  countsWhat:
    "The counts show whether the map reaches the record in every year. The colleague runs each one and pastes the result, and the two of you judge whether it looks right. Each count is rounded down to ten, and a group of fewer than ten is left out or blank, so that no small number can point to a patient.",
  countsWaiting: 'This step opens once the map is proposed.',
  countsWrite: 'Write the counts',
  countsAgain: 'If you change a binding or a code, choose Write the counts again, so that the counts read the map as it now stands.',
  countHeading: {
    coverage_by_year: 'The anaesthetics of each year',
    repeated_keys: 'Keys that repeat',
    readings_by_kind: 'The readings of one year, by kind',
  } as Record<string, string>,
  countWhat: {
    coverage_by_year:
      'This count gives, for each year, the anaesthetics that the map finds, and how many of them have a patient, a date of birth, a date of death, a test patient, a recorded stop, and a stop before the start.',
    repeated_keys:
      'This count gives, for the patients and the anaesthetics, how many keys more than one row holds. A repeated key usually means that a join repeats rows, which would count an anaesthetic twice.',
    readings_by_kind:
      'This count gives, for the anaesthetics of the chosen year, the readings of each kind as the map translates the codes, and how many were accepted and hold a number. It shows whether the codes chosen in step 7 reach the readings.',
  } as Record<string, string>,
  countSafe: 'This count reads only the tables below, none of which is a table of readings, so it is safe to run on production.',
  countScript: (limit: string) =>
    `This count reads the readings, so it is a script in two parts, which is safe to run on production. Part 1 puts at most ${limit} anaesthetics of the year into #cohort, and part 2 reaches their readings from #cohort by keys alone.`,
  countTables: 'It reads these tables:',
  sizeUnknown: 'size not known',
  sizeRows: (n: number) => `about ${rows(n)}`,
  countCopy: 'Copy the count',
  countPasteLabel: 'The result of this count, copied from the results grid with its headers:',
  countRead: 'Read the result',
  countReceipt: (n: number) => `Schemalyser has read the result of this count: ${rows(n)}.`,
  countNoFindings: 'Schemalyser sees nothing unusual in this count.',
  lookRightLegend: 'Do these look right to the two of you?',
  lookRight: [['yes', 'Yes, they look right'], ['no', 'No, something is wrong']] as [string, string][],
  lookRightNote: 'A note, if you would like to say what looks wrong or why it is as it is:',
  lookRightSave: 'Record the judgement',
  lookRightSaved: (answer: string, date: string) => (answer === 'yes' ? `Recorded on ${date} that this count looks right.` : `Recorded on ${date} that something in this count is wrong.`),

  // 9. The folder.
  writeWhat:
    "Schemalyser writes everything that the two of you have settled into the hospital folder: the map with one SQL view for each role, the codes, the counts and their judgements, every query offered with every result pasted, the answers in order, a journal of the steps, and a README that explains each file to someone who has never seen this page. Choosing that folder in step 3 next time restores everything.",
  keepLabel: 'Keep a copy of the dictionary in the hospital folder',
  keepNote:
    "The dictionary is licensed, so the box is not ticked unless you tick it. Tick it only if the hospital's licence allows a copy to be kept on the hospital's storage. With a copy, the folder can be checked later without loading the dictionary again.",
  writeFolder: 'Write into a folder on this computer',
  writeFolderNote: 'Chrome and Edge can write into a folder that you choose. Your browser will ask you to choose the folder and to allow Schemalyser to save changes to it.',
  writeZip: 'Download the hospital folder as a zip',
  writeZipNote: 'Firefox cannot write into a folder, so Schemalyser saves the folder as one zip file. Unzip it into the hospital folder, replacing what is there.',
  writeWaiting: 'This step opens once the map is proposed.',
  written: (n: number) => `Schemalyser has written ${plural(n, 'file', 'files')} into the chosen folder.`,
  zipped: (n: number) => `Schemalyser has saved the hospital folder as a zip of ${plural(n, 'file', 'files')}. Unzip it into the hospital folder, replacing what is there.`,
  writeFailed: 'Schemalyser could not write into the chosen folder. You can download the folder as a zip instead.',

  failed: 'Schemalyser could not finish that, and has kept everything that was settled before it.',
  copied: 'Copied.',
  version: (version: string) => `Schemalyser ${version}`,
};
