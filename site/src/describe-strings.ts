// The wording of the page Describe the record (describe.html), in one place. Each instruction says what to do and where
// the control is; the fuller account of each input sits behind a short disclosure beside it.

const plural = (n: number, one: string, many: string) => `${n.toLocaleString('en-AU')} ${n === 1 ? one : many}`;
const rows = (n: number) => plural(n, 'row', 'rows');

export const describeStrings = {
  title: 'Describe the record',
  intro:
    "A clinician and a colleague who can run SQL work through these steps together, once for each hospital. The page proposes where the hospital's database keeps each part of the anaesthetic record, the colleague confirms or corrects each one, and the page saves the result as the hospital schema.",
  privateNote: "Everything you load or paste stays in this browser tab. The only copy the page makes is the saved hospital schema, a single file that you keep on the hospital's own storage.",
  back: 'For an audit of the earlier kind, you can still use the existing page.',
  backLink: 'Open the existing page',

  // The headings of the steps. The page records the heading of the step that offered each query in the saved hospital schema.
  steps: [
    '1. Take this page offline',
    '2. Load the data dictionary',
    '3. Open a saved hospital schema',
    '4. Propose where each part is held',
    '5. Check which tables exist',
    '6. Confirm each column',
    "7. Choose the hospital's codes",
    '8. Run the counts',
    '9. Save the hospital schema',
  ],

  // The rail beside the steps.
  railLabel: 'Steps',
  state: { done: 'Done', current: 'Do this now', available: 'Ready', optional: 'Optional', waiting: 'Waiting', problem: 'Needs attention' } as Record<string, string>,
  stepOf: (n: number, total: number, name: string) => `Step ${n} of ${total}: ${name}`,
  waitingFor: {
    offline: 'This step opens once the page is offline.',
    dictionary: 'This step opens once the dictionary is loaded.',
    map: 'This step opens once the hospital schema is proposed.',
    folder: 'This opens once you open a saved hospital schema at step 3.',
  },
  showStep: 'Show this step',
  hideStep: 'Hide this step',
  receipt: {
    folder: 'The page has opened the saved hospital schema.',
    proposed: (drafted: number, roles: number) => `The page has proposed ${drafted} of the ${roles} parts of the record.`,
    tables: 'The page has read the result of the query of tables and columns.',
    confirmed: (total: number, undrafted: number) =>
      undrafted
        ? `Every column that can be answered has an answer. The page found no table for ${plural(undrafted, 'part', 'parts')}, which ${undrafted === 1 ? 'remains' : 'remain'} to be answered once a table is chosen.`
        : `Every one of the ${total} columns has an answer.`,
    codes: (n: number, of: number) => (of ? `${n.toLocaleString('en-AU')} of ${plural(of, 'list', 'lists')} saved.` : 'No column holds local codes that need choosing.'),
    counts: (n: number, of: number) => `${n.toLocaleString('en-AU')} of ${plural(of, 'count', 'counts')} read.`,
  },
  // Beside step 6 in the rail, while some columns still have no answer.
  stillToAnswer: (columns: number, tables: number, untranslated: number) => {
    const unanswered = [columns ? plural(columns, 'column', 'columns') : '', tables ? plural(tables, 'table', 'tables') : ''].filter(Boolean).join(' and ');
    return [unanswered ? `${unanswered} still to answer` : '', untranslated ? `${untranslated.toLocaleString('en-AU')} still to translate` : ''].filter(Boolean).join(' and ');
  },
  aboutFile: 'What this file is',

  // 1. Offline.
  offlineWhat: 'Wait for the page to load, then take this tab offline. Your SQL window stays connected.',
  loading: 'The page is loading. Keep it online until it says that it has finished.',
  loaded: 'The page has finished loading. Take this tab offline now.',
  offlineInvented: ['If you want to try the page with the invented dictionary rather than a real one, load it at ', 'step 2', ' now, before you take the tab offline.'],
  loadFailed: 'The page has not been able to load. If the tab is online, reload it to try again.',
  offlineHowSummary: 'How to take this tab offline',
  offlineHow: [
    'In Chrome or Edge, press F12, choose the Network panel, open the menu that reads No throttling and choose Offline. Leave the developer tools open, because the tab goes back online when they close.',
    'In Firefox, open the File menu and choose Work Offline. If you cannot see the menu bar, press Alt.',
    'If the developer tools are not available on this computer, use Firefox and Work Offline.',
    'On a phone or a tablet, the page cannot be taken offline in this way, so please use it on a computer.',
  ],
  noFiles: 'The page will not accept any files while it is online.',
  offlineDone: 'This tab is offline, so nothing you load or paste can leave it.',
  locked:
    'This tab has gone back online, so the page has let go of the dictionary and of everything pasted. If you saved the hospital schema, nothing is lost: reload the page, take it offline and open the saved hospital schema at step 3.',
  policySummary: 'How the page makes sure that nothing can leave it',

  // 2. The dictionary.
  dictionaryWhat: 'Choose whichever of these three fits this sitting.',
  choiceReal: 'I have the data dictionary.',
  choiceRealWhat: "Choose the file your colleague exported from the vendor's dictionary.",
  choiceInvented: 'I want to try the page first.',
  inventedLoad: 'Load the invented dictionary',
  choiceSaved: 'I have a saved hospital schema.',
  choiceSavedWhat: ['Open it at ', 'step 3', '.'],
  inventedOnlineOnly:
    'The invented dictionary can only be loaded while the tab is online. Go back online, load it, then take the tab offline again.',
  inventedLoading: 'The page is loading the invented dictionary.',
  inventedFailed: 'The page has not been able to fetch the invented dictionary. If the tab is still online, reload the page and try again.',
  dictionaryLabel: 'The dictionary file, with one row for each column:',
  dictionaryAbout:
    "The data dictionary is the vendor's own description of every table and column of the reporting database. The colleague exports it from the vendor's dictionary tool as a CSV or tab-separated file with a row of headings. The page uses it to propose where each part of the record is held, and shows its definition beside each column. The dictionary is licensed, so the page reads it in this tab only and sends it nowhere. A copy goes into the saved hospital schema, which stays on the hospital's own storage.",
  tablesLabel: 'The tables file, if you have one, with one row for each table:',
  tablesAbout:
    "This optional file comes from the same tool. It gives each table's description and the column that identifies its rows, which help the page choose the right table and find how the tables link.",
  headingsSummary: 'If the page cannot find the headings',
  headingsWhat:
    'The page looks for the usual headings, such as TABLE_NAME, COLUMN_NAME and DESCRIPTION. If your file uses others, write them here exactly as they appear in its first row, and leave the rest empty.',
  headingFields: [
    ['table', 'The heading of the table names:'],
    ['column', 'The heading of the column names:'],
    ['description', 'The heading of the descriptions:'],
    ['data_type', 'The heading of the data types:'],
    ['key', 'The heading that marks the identifying column:'],
  ] as [string, string][],
  dictionaryLoad: 'Load the dictionary',
  dictionaryReading: 'The page is reading the dictionary.',
  dictionaryReceipt: (r: { tables: number; columns: number; described: number; keyed: number; skipped: number; source?: string | null }) =>
    `The page has read ${r.source === 'invented' ? 'the invented dictionary' : r.source === 'saved' ? 'the dictionary from the saved schema' : 'the dictionary'}: ${plural(r.columns, 'column', 'columns')} in ${plural(r.tables, 'table', 'tables')}.${
      r.described === r.columns ? '' : ` ${plural(r.described, 'column has', 'columns have')} a description.`
    }${r.skipped ? ` The page left out ${rows(r.skipped)} whose names are not plain table and column names.` : ''}`,
  dictionaryFailed: 'The page could not read this file as a dictionary. Make sure that it is the CSV export, then choose it again.',

  // 3. The saved hospital schema.
  folderWhat:
    'If you saved a hospital schema at an earlier sitting, choose Open a saved hospital schema and pick the file. The page reads the data dictionary and everything you settled from it, and you carry on from where you left off. At a first sitting, you can leave this step.',
  folderLabel: 'The saved hospital schema:',
  folderChoose: 'Open a saved hospital schema',
  folderAbout:
    "The saved hospital schema is the one file that the page saves at step 9. It holds the data dictionary, the hospital schema, your answers, the hospital's codes, the counts, and every query that was run with its result. It stays on the hospital's own storage, and the page reads it in this tab only.",
  folderReading: 'The page is reading the saved hospital schema.',
  folderReceipt: (r: { map: boolean; tables: boolean; codes: number; counts: number; dictionary: boolean; confirmations: number; queries: number }) =>
    [
      r.map ? 'The page has opened the hospital schema from the saved file.' : 'This file holds no hospital schema yet, so the page will propose a new one.',
      r.confirmations ? `It holds ${plural(r.confirmations, 'recorded answer', 'recorded answers')}.` : '',
      r.queries ? `It holds ${plural(r.queries, 'earlier query', 'earlier queries')}.` : '',
      r.tables ? 'The result of the query of tables and columns is restored.' : '',
      r.codes ? `The codes of ${plural(r.codes, 'list are', 'lists are')} restored.` : '',
      r.counts ? `${plural(r.counts, 'count is', 'counts are')} restored.` : '',
    ].filter(Boolean).join(' '),
  folderNeedsDictionary: 'This file does not hold the data dictionary. Load it at step 2, then open the saved hospital schema again.',
  folderFailed: 'The page could not read this file as a saved hospital schema. Make sure that it is the file that the page saved at step 9, then open it again.',

  // The check of a saved hospital schema against the database.
  checkHeading: 'Check a saved schema against the database',
  checkWhat:
    "After a change to the database or a new release of the vendor's system, choose Check against the database. The page proposes the hospital schema again from the dictionary and your recorded answers, compares it with the saved schema, and lists each earlier query for the colleague to run again.",
  checkButton: 'Check against the database',
  checkSame: 'The schema proposed again is the same as the saved schema.',
  checkDiffers: (n: number) => `The schema proposed again differs from the saved schema in ${plural(n, 'place', 'places')}:`,
  checkNoDictionary:
    'Without the dictionary, the page has compared only the queries. Load the dictionary at step 2, then choose Check against the database again to compare the hospital schema as well.',
  checkNoQueries: 'The saved schema records no queries yet.',
  checkQuery: (number: number, file: string, step: string) => {
    const at = step.match(/^(\d+)\.\s*(.+)$/);
    return at ? `Query ${number}, saved as ${file}, from step ${at[1]} (${at[2]})` : `Query ${number}, saved as ${file}`;
  },
  checkPrevious: (pasted: string | null, database: string | null) =>
    pasted
      ? `The earlier result below was pasted on ${pasted.replace('T', ' at ')}${database ? `, from the ${database === 'training' ? 'training' : 'production'} database` : ''}.`
      : 'No result was pasted for this query.',
  checkPasteLabel: 'Run the query again and paste the new result here, with its headers:',
  checkCompare: 'Compare with the earlier result',
  checkNoChange: 'The new result is the same as the earlier one.',
  checkChanges: 'The new result differs from the earlier one:',

  // 4. The proposal.
  proposeWhat: 'Choose Propose the hospital schema. The page matches each part of the anaesthetic record to a table and column in the dictionary.',
  proposeAboutSummary: 'How the page proposes the hospital schema',
  proposeAbout:
    "The page compares the words that describe each part of the record with the dictionary's names and descriptions. It works by fixed rules, in this tab only and with no language model, so the same dictionary always gives the same proposal.",
  proposeButton: 'Propose the hospital schema',
  proposeProgress: (done: number, total: number) =>
    done === 0 ? 'The page is indexing the dictionary.' : `The page has proposed ${done} of ${total} parts of the record.`,
  proposeDone: (roles: number, drafted: number) =>
    `The page has proposed ${drafted} of the ${roles} parts of the record. You confirm or correct each one in step 6.`,
  proposalSummary: 'See the proposal, part by part',
  roleUndrafted: 'The dictionary has no table that fits this part, so the page has not proposed one.',
  roleRequired: 'Every audit reads this part.',
  roleFurther: 'An audit reads this part only where the hospital records it.',
  rowsAttribute: 'The table that holds one row for each',
  proposedLabel: 'Proposed:',
  confirmedLabel: 'Confirmed:',
  correctedLabel: 'Corrected to:',
  nothingProposed: 'The page found nothing in the dictionary for this column.',
  nothingProposedRows: 'The page found no table in the dictionary for this part.',
  // A link between two tables, in words: "T.C, by A = B" becomes "T.C, linked by matching A to B".
  linkedBy: 'linked by matching',
  linkedTo: 'to',
  linkedThen: 'then',
  definitionLabel: "The dictionary's definition",
  evidenceLabel: 'Why the page proposed it',
  confidence: {
    high: "high confidence, as the dictionary's words match",
    medium: "medium confidence, as the dictionary's words match in part",
    low: 'low confidence, as the proposal is a guess',
    none: 'no match',
  } as Record<string, string>,
  reasonLabel: 'Why the page proposed it:',
  alternativesLabel: 'Other columns that came close',

  // 5. The tables and columns query.
  tablesWhat: 'Choose the database, then choose Write the query. Run the query in your SQL window, paste the result below and choose Read the result.',
  databaseLegend: 'The database that your SQL window is connected to:',
  databaseOptions: [
    ['production', 'The production reporting database, or a refreshed copy of it'],
    ['training', 'A training or play database with fictional patients'],
    ['unsure', 'I am not sure'],
  ] as [string, string][],
  databaseAboutSummary: 'Why the page asks',
  databaseAbout:
    "A training database has the hospital's real tables and codes but fictional patients, so its counts mean nothing. On a training database the page still settles what depends on the tables alone, and marks every count to be run again on production.",
  databaseUnsure: 'The page will treat it as the production database. If you later find that it is a training database, change this answer.',
  tablesWrite: 'Write the query',
  tablesNames: (n: number) => `The query asks about the ${plural(n, 'table', 'tables')} that the proposal names.`,
  tablesCopy: 'Copy the query',
  showQuery: 'Show the query',
  tablesHow: [
    'Choose Copy the query, paste it into your SQL window and run it.',
    'In SQL Server Management Studio, click the empty square at the top left of the results grid, then right-click and choose Copy with Headers.',
    'Paste the result into the box below and choose Read the result.',
  ],
  querySafeSummary: 'Why this query is safe on production',
  querySafe: "The query reads only SQL Server's own list of tables and columns, and never a row of any table.",
  tablesPasteLabel: 'The result, copied with its headers:',
  tablesRead: 'Read the result',
  tablesReceipt: (r: { tables: number; columns: number; sized: number; asked: number; absent: number }) =>
    `The page has read the result: ${plural(r.columns, 'column', 'columns')} in ${plural(r.tables, 'table', 'tables')}.${
      r.absent ? ` ${plural(r.absent, 'table', 'tables')} did not come back, either because ${r.absent === 1 ? 'it does' : 'they do'} not exist here or because this login cannot see ${r.absent === 1 ? 'it' : 'them'}.` : ' Every table came back.'
    } Each column in step 6 now shows whether the database holds it.`,
  tablesDoubt: 'More than half of the tables did not come back. Make sure that your SQL window is connected to the reporting database before you go on.',
  tablesUnreadable: "The page could not read this as the query's result. Use Copy with Headers on the whole results grid, then paste it again.",

  // 6. Confirming.
  confirmIntro:
    "The page describes the anaesthetic record as a set of parts, such as patients, anaesthetics and the readings charted during an anaesthetic, each with a few columns. For each column, the page proposes the table and column in the hospital's database that holds it, and your colleague says whether that is right.",
  confirmWhat:
    "Beside each column below, choose Yes, this is right, Choose another column or Not sure. The row for a part's table offers Choose another table, and a column for which the page found nothing offers Choose a column.",
  confirmLegend:
    "Where a column sits in another table, the page shows how the tables are linked: linked by matching A to B means that a row of one table belongs with the row of the other in which B holds the same value as A. Beside each proposal, the page gives its confidence: high where the dictionary's own words match the column's meaning, medium where they match in part, and low where the proposal is a guess.",
  confirmAboutSummary: 'What happens to each answer',
  confirmAbout:
    'The page records each answer with its date. Choose another column opens a short form. You fill it in and choose Check this change, and the page runs the test on made-up rows: it builds rows with no hospital data in this tab and runs the whole hospital schema, with the change, on them. You then keep the change or discard it. Each Not sure goes into the list of questions for the database team at the end of this step. Once a column has an answer, Change the answer brings the choices back.',
  // Beside each part, who can usually answer for it.
  whoAnswers: {
    colleague: 'Your colleague can usually answer for this part from what they know of the record.',
    team: 'Usually only the team that looks after the reporting database can answer for this part, so a Not sure here goes to them.',
  } as Record<string, string>,
  teamParts: ['role_lab', 'role_diagnosis', 'role_note', 'role_finding'],
  partCount: (answered: number, total: number) => `${answered.toLocaleString('en-AU')} of ${total.toLocaleString('en-AU')} answered`,
  tally: (t: { confirmed: number; corrected: number; not_sure: number; remaining: number; untranslated: number; total: number; tables: number; tables_remaining: number }) => {
    const n = (v: number) => v.toLocaleString('en-AU');
    return `Of ${n(t.total)} columns, ${n(t.confirmed)} ${t.confirmed === 1 ? 'is' : 'are'} confirmed, ${n(t.corrected)} corrected and ${n(t.not_sure)} not sure, ${n(t.untranslated)} ${t.untranslated === 1 ? 'is' : 'are'} still to translate, and ${n(t.remaining)} ${t.remaining === 1 ? 'remains' : 'remain'} to answer. Of the ${n(t.tables)} tables of the parts, ${n(t.tables - t.tables_remaining)} ${t.tables - t.tables_remaining === 1 ? 'has' : 'have'} an answer.`;
  },
  tallyLabels: { confirmed: 'Confirmed', corrected: 'Corrected', not_sure: 'Not sure', untranslated: 'Still to translate', remaining: 'Still to answer' } as Record<string, string>,
  tablesTally: (answered: number, total: number) => `The tables of the parts: ${answered.toLocaleString('en-AU')} of ${total.toLocaleString('en-AU')} answered.`,
  yes: 'Yes, this is right',
  another: 'Choose another column',
  anotherRows: 'Choose another table',
  chooseColumn: 'Choose a column',
  chooseTable: 'Choose a table',
  notSure: 'Not sure',
  change: 'Change the answer',
  anotherLabel: 'Choose one of the other columns that the page found:',
  anotherNoneFound: 'The page has no other suggestion, so write a column from the dictionary below.',
  anotherRowsLabel: 'Write the name of the table that holds one row for each, and the page will propose this part again from it:',
  anotherRowsChoose: 'Choose one of the other tables that the page found:',
  anotherRowsWrittenOr: 'Or write the name of the table, and the page will propose this part again from it:',
  anotherWritten: 'Write it as TABLE.COLUMN:',
  anotherWrittenOr: 'Or write it as TABLE.COLUMN:',
  anotherNone: 'None of these',
  anotherUse: 'Use this one',
  inForceWritten: (name: string) => `The page will use ${name}, which you wrote.`,
  inForceChosen: (name: string) => `The page will use ${name}, chosen from the list.`,
  // A column of a flag or a kind whose source holds codes, before and after Yes.
  coded: {
    flag: 'This column holds codes rather than 1 and 0. After Yes, the page opens the 1-or-0 form so that you can translate them.',
    kind: "This column holds the hospital's own codes. After Yes, you translate them in the list of codes in step 7.",
    kindForm: "This column holds the hospital's own codes. After Yes, the page opens a form in which you translate them.",
    flagNext: 'To finish this column, translate its codes to 1 and 0 in the form below: choose Write the query of values, run it and paste the result, tick each value that means yes, then choose Check this change.',
    kindNext: "To finish this column, translate its codes in step 7, where the page lists the codes in use.",
    kindNextForm: "To finish this column, translate its codes in the form below, then choose Check this change.",
  },
  landed: (text: string) => `The test on made-up rows found: ${text}`,
  // The date of an answer, as 7 October.
  day: (iso: string) => {
    const at = iso.match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (!at) return iso;
    const months = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
    return `${Number(at[3])} ${months[Number(at[2]) - 1]}${at[1] === String(new Date().getFullYear()) ? '' : ` ${at[1]}`}`;
  },
  answered: {
    yes: (date: string) => `Confirmed on ${date}.`,
    untranslated: (date: string) => `Column confirmed on ${date}; codes not yet translated.`,
    untranslatedNo: (date: string, replacement: string) => `Column corrected to ${replacement} on ${date}; codes not yet translated.`,
    no: (date: string, replacement: string) => `Corrected to ${replacement} on ${date}.`,
    notSure: (date: string) => `Not sure, listed as a question on ${date}.`,
  },
  presence: {
    present: 'The database holds this column.',
    presentTable: 'The database holds this table.',
    missing: (names: string[]) => `The database did not return ${names.join(', ')}.`,
    large: (table: string, n: number) => `The database holds this. ${table} is large, at about ${rows(n)}, so the page's queries read only a sample of it.`,
    unknown: 'The page does not yet know whether the database holds this.',
  },
  questionsHeading: 'Questions for the database team',
  questionsWhat: 'Each column marked Not sure is listed here. Choose Copy the questions, paste them into an email to the team, and answer each column above when they reply.',
  questionsNone: 'There are no questions yet.',
  questionsCopy: 'Copy the questions',
  questionsNoteHead: 'Questions about the reporting database, from the description of the anaesthetic record. For each, the page states what it proposes and asks whether that is right:',
  questionsMeaning: (meaning: string) => `The description of the record defines it as follows: ${meaning}`,
  confirmFailed: 'The page could not record that answer. Please try again.',

  // 7. The codes.
  codesWhat:
    "Some columns hold the hospital's own codes, such as the kind of each reading. For each list below, choose Write the list, run it in your SQL window, paste the result and choose Read the list. Then choose what each code means and choose Save these codes.",
  codesAboutSummary: 'How the list works, and why it is safe',
  codesSafe: (limit: string) =>
    `The list counts the codes used on the anaesthetics of the year above, with their names. It runs in two parts: part 1 puts at most ${limit} anaesthetics of that year into a temporary table, #cohort, and part 2 reads only their rows, never the whole of a large table.`,
  yearLabel: 'The year to look at:',
  yearNote: 'A recent full year shows how the record is charted now.',
  vocabularyHeading: (title: string) => `The codes of ${title[0].toLowerCase()}${title.slice(1)}`,
  vocabularyBound: (bound: string, lookup: string | null) =>
    lookup ? `The codes come from ${bound}, and their names from ${lookup}.` : `The codes come from ${bound}. The dictionary names no table of their names, so the list shows the codes alone.`,
  vocabularyReason: {
    unbound: 'No column is chosen for this yet, so there are no codes to choose.',
    unlinked: 'This part does not link to an anaesthetic or a patient, so a later screen settles its codes.',
    unbound_link: 'The hospital schema does not yet say how this part links to its anaesthetic, so the page cannot write the list.',
  } as Record<string, string>,
  kindsSummary: 'What each kind means',
  // A kind in plain words, with its code after it, as the lists and forms show it.
  kindOption: (kind: string, meaning?: string) => (meaning ? `${meaning.replace(/[.\s]+$/, '')} (${kind})` : kind),
  chartedWrite: 'Write the list',
  chartedCopy: 'Copy the list',
  chartedPasteLabel: 'The result of the list, copied with its headers:',
  chartedRead: 'Read the list',
  chartedReceipt: (n: number, year: number) =>
    `The page has read ${plural(n, 'code', 'codes')} charted in ${year}. Choose the kind of each code you recognise, leave the rest as Not chosen, then choose Save these codes.`,
  codesOther: 'A code left as Not chosen counts as other, which each list also offers.',
  chartedEmpty: (year: number) => `The list for ${year} came back empty. Look again at this part's columns at step 6, or choose another year.`,
  chartedColumns: ['Code', 'Times charted', 'Anaesthetics', 'Name', 'Kind'],
  notChosen: 'Not chosen',
  codesSave: 'Save these codes',
  codesSaved: (n: number, date: string) => `The page saved ${plural(n, 'code', 'codes')} for this list on ${date}.`,
  codesFromRow: 'Go to this list in step 7',
  underTen: 'under 10',
  codesNoneSaved: 'No codes are saved for this list yet.',
  kindMeaning: (kind: string, meaning: string) => `${kind}: ${meaning}`,

  // 8. The counts.
  countsWhat: 'Choose Write the counts. For each count, choose Copy the count, run it, paste its result and choose Read the result, then choose Save the judgement.',
  countsAboutSummary: 'What the counts are for',
  countsAbout:
    'The counts show whether the hospital schema reaches the record in every year. Each count is rounded down to ten. A year or a group with fewer than ten is left out, and a figure under ten within a group shows as under 10, so that no small number can point to a patient.',
  countsWrite: 'Write the counts',
  countsAgain: 'If you change a column or a code, choose Write the counts again.',
  countHeading: {
    coverage_by_year: 'The anaesthetics of each year',
    repeated_keys: 'Rows that appear twice',
    readings_by_kind: 'The readings of one year, by kind',
  } as Record<string, string>,
  countWhat: {
    coverage_by_year:
      'This count shows, for each year, how many anaesthetics the hospital schema finds and how many of them have a patient, a date of birth, a date of death and a recorded stop.',
    repeated_keys: 'This count shows whether any patient or anaesthetic appears on more than one row, which would make an audit count it twice.',
    readings_by_kind: 'This count shows the readings of {year}, the year chosen in step 7, by kind, so that you can see whether the codes chosen in step 7 reach them.',
  } as Record<string, string>,
  countTablesSummary: 'Which tables this count reads',
  countSafe: 'This count reads no table of readings, so it is safe to run on production.',
  countTraining: 'Run on the training database; run it again on production before the figures are used.',
  countTrainingNote: 'Your SQL window is connected to a training database, whose patients are fictional, so these figures show only that the query runs.',
  countScript: (limit: string) =>
    `This count reads the readings, so it runs in two parts: part 1 puts at most ${limit} anaesthetics of the year into #cohort, and part 2 reads only their readings.`,
  countTables: 'It reads these tables:',
  sizeUnknown: 'size not known',
  sizeRows: (n: number) => `about ${rows(n)}`,
  countCopy: 'Copy the count',
  countPasteLabel: 'The result of this count, copied with its headers:',
  countRead: 'Read the result',
  countReceipt: (n: number) => `The page has read ${rows(n)} of this count.`,
  countNoFindings: 'The page sees nothing unusual in this count.',
  lookRightLegend: 'Record whether these figures look right to the two of you:',
  lookRightCompare: {
    coverage_by_year: 'Compare each year with about the number of anaesthetics the department gives in a year, which your colleague will know.',
    repeated_keys: 'Compare the figures with none, because a patient or an anaesthetic should appear on one row only.',
    readings_by_kind: 'Compare the readings of each kind with what is usually charted, such as a mean pressure every few minutes of an anaesthetic.',
  } as Record<string, string>,
  lookRight: [['yes', 'Yes, they look right'], ['no', 'No, something is wrong']] as [string, string][],
  lookRightNote: 'A note, if you want to record what looks wrong or why:',
  lookRightSave: 'Save the judgement',
  lookRightSaved: (answer: string, date: string) =>
    answer === 'yes' ? `On ${date}, you recorded that this count looks right.` : `On ${date}, you recorded that something in this count is wrong.`,
  lookRightTraining: 'The page has recorded this judgement as made on the training database, and the README in the saved hospital schema lists the count to run again on production.',

  // 9. Saving the hospital schema.
  writeWhat:
    'Choose Save the hospital schema. The page saves everything you have settled as one file, which you open at step 3 at the next sitting to carry on.',
  writeAboutSummary: 'What the file holds',
  writeAbout:
    "The file holds the data dictionary, the hospital schema with one SQL file for each part of the record, the hospital's codes, the counts and their judgements, every query with its result, your answers in order, and a README that explains each part.",
  writeSave: 'Save the hospital schema',
  writeSaveNote: "Keep the file on the hospital's own storage, because it holds the hospital's data dictionary.",
  saved: (draft: string) =>
    `The page has saved the hospital schema${draft ? ` as a draft (${draft})` : ''}. Keep the file on the hospital's own storage.`,
  draftNote: (unfinished: string) => `The hospital schema is not yet complete: ${unfinished}. You can save it now as a draft, which the README and settings.json record, and finish it at a later sitting.`,
  draftCodes: 'These columns are answered, but their codes are not yet translated. Each link leads to its row:',
  savedDraft: 'Saved as a draft',
  writtenStale: 'You have changed something since the hospital schema was saved, so please save it again.',
  writeFailed: 'The page could not save the hospital schema. Please try again.',

  // 6. Corrections, each a sentence to complete.
  corrections: {
    intro: 'Choose the kind of change, then complete the sentence. The page writes the SQL for you.',
    formLabel: 'The kind of change:',
    forms: {
      column: 'A different column',
      rows: 'A different table for the rows',
      flag: 'A 1 or 0 worked out from a column',
      scale: 'A number in another unit',
      date: 'The date alone, from a date and time',
      trim: 'Text with spaces trimmed from either end',
      filter: 'Only some of the rows',
      path: 'A link through other tables',
      pair: 'A link that matches on two columns',
      window: 'A link that also needs a time window',
      joined: 'Several rows joined into one text',
      codes: 'A translation of the local codes',
    } as Record<string, string>,
    formWhat: {
      column: 'Use this when a different column holds this value.',
      rows: 'Use this when a different table holds one row for each. The page will propose this whole part again from that table.',
      flag: 'Use this when the column holds a code or a word rather than 1 and 0.',
      scale: 'Use this when the column holds the value in another unit, such as kilograms instead of grams.',
      date: 'Use this when the column holds a date and time and only the date is wanted.',
      trim: 'Use this when the column holds text with spaces at either end, or a code that should be read as text.',
      filter: 'Use this when the table also holds rows that do not belong, such as cancelled cases.',
      path: 'Use this when the value is reached through one, two or three other tables. The page suggests each link from the dictionary.',
      pair: 'Use this when the next table can be matched only on two columns at once, such as a case number and a line number.',
      window: 'Use this when a row shares a number, such as the encounter, with every anaesthetic of that encounter. The row then belongs to the anaesthetic whose start and stop its time falls between.',
      joined: 'Use this when the text is spread over several rows, such as the lines of a note.',
      codes: "Use this when the column holds the hospital's own codes. Step 7 offers the same choice from the codes actually in use.",
    } as Record<string, string>,
    windowRule:
      'A time window alone never links a row to an anaesthetic. The row must also share a number with its anaesthetic, such as the encounter, so the page refuses a window without one.',
    tableLabel: 'Take the value from the table',
    columnLabel: 'and its column',
    filterTable: 'Keep only the rows of the table',
    filterColumn: 'whose column',
    chooseTable: 'Choose the table first',
    chooseColumn: 'Choose a column',
    valuesLabel: 'holds any of these values, separated by commas',
    flagValuesLabel: 'It is 1 where the column holds any of these values, separated by commas, and 0 otherwise',
    valuesNote: 'To pick from the values that the column actually holds, choose Write the query of values, run it and paste the result.',
    valuesWrite: 'Write the query of values',
    valuesCopy: 'Copy the query of values',
    valuesPasteLabel: 'The result of the query of values, copied with its headers:',
    valuesRead: 'Read the values',
    valuesTick: 'Tick each value that counts:',
    valueRows: (value: string, rows: number | null) => `${value || '(empty)'}${rows === null ? ', in fewer than ten rows' : `, in about ${rows.toLocaleString('en-AU')} rows`}`,
    factorLabel: 'then multiply it by',
    offsetLabel: 'and add this, which is usually 0',
    stepHeading: (n: number) => `Link ${n}`,
    stepFrom: (table: string) => `Join ${table} on its column`,
    stepTo: 'to the table',
    stepToColumn: 'at its column',
    stepSecondFrom: (table: string) => `and also match ${table} on its column`,
    stepSecondTo: 'to the column',
    suggestedLabel: (table: string) => `Links from ${table} that the dictionary suggests:`,
    suggestedNone: 'Choose a suggested link, or fill in the fields below',
    suggestedRepeats: 'may repeat rows',
    addStep: 'Add another table',
    removeStep: 'Remove the last table',
    finalColumn: (table: string) => `Then take the value from the column of ${table}`,
    sharedTable: 'The row shares a number with its anaesthetic, such as the encounter, in the table',
    sharedColumn: 'and the column',
    anaestheticKey: (table: string) => `which matches the column of ${table}, the anaesthetic's own table`,
    beforeLabel: 'Allow this many minutes before the start',
    afterLabel: 'and this many after the stop',
    onTable: 'Start from the table',
    onColumn: 'whose column',
    rowsTable: 'matches the rows of the table',
    linkColumn: 'on their column',
    textColumn: 'Take the text from their column',
    orderColumn: 'in the order of their column',
    separatorLabel: 'with this between each row, such as a space',
    codeLabel: 'The local code',
    kindLabel: 'stands for',
    addCode: 'Add another code',
    sentenceLabel: 'What this change means:',
    checkingUse: 'The page runs the test on made-up rows before you keep this change.',
    sqlLabel: 'Show the SQL that the page will write',
    incomplete: 'Complete the sentence, and the page will show what the change means.',
    checkWhat: 'Choose Check this change. The page runs the test on made-up rows: it builds rows with no hospital data in this tab and runs the whole hospital schema, with the change, on them.',
    passedMeans:
      'This change keeps the hospital schema whole on made-up rows. It does not say whether the column means what you think, which the test query against the real database and your own knowledge do.',
    checkButton: 'Check this change',
    checking: 'The page is building the made-up rows and testing every part of the hospital schema.',
    checkSeconds: (seconds: number) => `The test on made-up rows took ${seconds.toLocaleString('en-AU')} seconds.`,
    remainingLabel: 'These problems were there before the change, which neither causes nor mends them:',
    notesLabel: 'The page also notes:',
    keep: 'Keep this',
    discard: 'Discard',
    although: 'Keep it although the test fails',
    reasonLabel: 'The reason for keeping it, which the page records with the change:',
    keepFailing: 'This change fails the test on made-up rows. To keep it, tick Keep it although the test fails and give the reason.',
    keepAlthough: 'Keep it with this reason',
    kept: (date: string, check: string) =>
      check.startsWith('passed') ? `Kept on ${date}. The test on made-up rows passed.` : `Kept on ${date}, although the test on made-up rows failed.`,
    keptReason: (reason: string) => `The reason given: ${reason}`,
    modelCheck: 'Test the hospital schema on made-up rows',
    modelCheckAgain: 'Test the hospital schema as it now stands',
    modelCheckWhat:
      'Before you change anything, you can run the test on made-up rows on the hospital schema as it stands, to see what is already wrong. The page builds rows with no hospital data and runs every part of the schema on them. Each finding links to its column below.',
    modelCheckWhatAfter: 'The test on made-up rows runs the hospital schema with every answer and change so far. Each finding links to its column below.',
    probeWhat: {
      link: 'Run this test query to try the link on the database. It counts the anaesthetics of {year}, the year chosen in step 7, that have at least one row through the link, and those with none.',
      filter: 'Run this test query to count the rows read and how many of them pass the filter.',
      flag: 'Run this test query to count the rows in which the flag is 1, 0 and empty.',
      flag_two: 'Run this test query to count the rows in which the flag is 1 and 0. The form never leaves this flag empty.',
    } as Record<string, string>,
    probeWrite: 'Write the test query',
    probeCopy: 'Copy the test query',
    probePasteLabel: 'The result of the test query, copied with its headers:',
    probeRead: 'Read the result of the test query',
    probeNone: 'There is no test query for this kind of change, so the test on made-up rows is its only test.',
    problemLabel: 'The page cannot use the form yet:',
  },

  failed: 'The page could not finish that. Everything settled before it is kept.',
  copied: 'Copied.',
  version: (version: string) => `Schemalyser ${version}`,
};
