// The wording of the page Describe the record (index.html, the front page), in one place. Each instruction says what to do and where
// the control is; the fuller account of each input sits behind a short disclosure beside it.

const plural = (n: number, one: string, many: string) => `${n.toLocaleString('en-AU')} ${n === 1 ? one : many}`;
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
// A date as a person reads it everywhere on the page: 8 October 2026.
const day = (iso: string) => {
  const at = (iso ?? '').match(/^(\d{4})-(\d{2})-(\d{2})/);
  return at ? `${Number(at[3])} ${MONTHS[Number(at[2]) - 1]} ${at[1]}` : iso;
};
// A date with its time, as 8 October 2026 at 14:05.
const when = (iso: string) => {
  const time = (iso ?? '').match(/T(\d{2}:\d{2})/);
  return time ? `${day(iso)} at ${time[1]}` : day(iso);
};
const rows = (n: number) => plural(n, 'row', 'rows');
const describeVendor = (v: { matched: number; gained: number }) =>
  v.matched
    ? `The vendor's file has added a description to ${plural(v.gained, 'column', 'columns')} that had none.`
    : "The vendor's file matched none of the database's tables and columns by name, so it has added no description.";

// The receipt of the data dictionary made from the database, with the vendor's descriptions where they were added.
const databaseReceipt = (r: { tables: number; columns: number; described: number; saved?: boolean; vendor?: { matched: number; gained: number } | null }) =>
  [
    r.saved
      ? `The page has read the data dictionary from the saved schema. It was made from the database and holds ${plural(r.columns, 'column', 'columns')} in ${plural(r.tables, 'table', 'tables')}, with descriptions for ${r.described.toLocaleString('en-AU')} of them.`
      : `The page has made the data dictionary from the database: ${plural(r.columns, 'column', 'columns')} in ${plural(r.tables, 'table', 'tables')}, with descriptions for ${r.described.toLocaleString('en-AU')} of them.`,
    r.vendor ? describeVendor(r.vendor) : '',
    r.described * 4 < r.columns
      ? "Few columns have a description, so the proposals rest on the names of tables and columns and will need more correcting. If the hospital's team that looks after the record system can export the vendor's descriptions, the clinician can add them with Upload a dictionary file you already have."
      : '',
  ].filter(Boolean).join(' ');

export const describeStrings = {
  title: 'Describe the record',
  intro:
    "Two people work through these steps together, once for each hospital. The clinician leads the audit and works through the page, and the database analyst has access to the hospital's database, runs the queries that the page offers and answers questions about the database. If one person is both the clinician and the database analyst, the page's instructions still name the role that acts. The page proposes where the hospital's database keeps each part of the anaesthetic record, the database analyst confirms or corrects each one, and the page saves the result as the hospital schema, which records where this hospital's database keeps each part of the anaesthetic record.",
  privateNote: "Everything loaded or pasted stays in this browser tab. The only copy the page makes is the saved hospital schema, a single file that the clinician keeps on the hospital's own storage.",

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
    offline: 'This step opens once the tab is offline.',
    loading: 'This step opens once the page has loaded.',
    problem: 'This step opens once the page has loaded again.',
    inventedNoHospital:
      'The invented dictionary has no database to ask in this sitting, so the clinician goes on to step 6. If the clinician loads the invented dictionary again at step 2 while the tab is online, the invented hospital answers this step.',
    dictionary: 'This step opens once the dictionary is loaded.',
    map: 'This step opens once the hospital schema is proposed.',
    folder: 'This opens once the clinician opens a saved hospital schema at step 3.',
  },
  showStep: 'Show this step',
  hideStep: 'Hide this step',
  receipt: {
    folder: 'The page has opened the saved hospital schema.',
    proposed: (drafted: number, roles: number) => `The page has proposed a table for ${drafted} of the ${roles} parts of the record.`,
    tables: 'The page has read the result of the query of tables and columns.',
    confirmed: (total: number, undrafted: number) =>
      undrafted
        ? `Every column that can be answered has an answer. The page found no table for ${plural(undrafted, 'part', 'parts')}, which ${undrafted === 1 ? 'remains' : 'remain'} to be answered once a table is chosen.`
        : `Every one of the ${total} columns has an answer.`,
    codes: (n: number, of: number) => (of ? `${n.toLocaleString('en-AU')} of ${plural(of, 'list', 'lists')} saved.` : 'No column holds local codes that need choosing.'),
    counts: (n: number, of: number) => `${n.toLocaleString('en-AU')} of ${plural(of, 'count', 'counts')} read.`,
    countsWrong: (n: number) => `${plural(n, 'count looks', 'counts look')} wrong`,
  },
  // Beside step 6 in the rail, while some columns still have no answer.
  stillToAnswer: (columns: number, tables: number, untranslated: number) => {
    const unanswered = [columns ? plural(columns, 'column', 'columns') : '', tables ? plural(tables, 'table', 'tables') : ''].filter(Boolean).join(' and ');
    return [unanswered ? `${unanswered} still to answer` : '', untranslated ? `${plural(untranslated, 'column', 'columns')} confirmed whose codes are not yet translated` : ''].filter(Boolean).join(' and ');
  },
  aboutFile: 'What this file is',

  // 1. Offline.
  offlineWhat: 'The clinician waits for the page to load, then takes this tab offline. The database analyst\'s SQL window stays connected.',
  loading: 'The page is loading. Keep it online until it says that it has finished.',
  loaded: 'The page has finished loading. Take this tab offline now.',
  offlineInvented: ['If the clinician wants to try the page first, the clinician chooses Use the invented dictionary to try the page at ', 'step 2', ' now, before taking the tab offline.'],
  // Once the invented dictionary has loaded while the tab is online, in step 1 and in step 2.
  inventedLoaded:
    'The invented dictionary is loaded, with the invented hospital that answers its queries. Now take this tab offline and carry on at step 4.',
  loadFailed: 'The page has not been able to load. If the tab is online, reload it to try again.',
  offlineHowSummary: 'How to take this tab offline',
  offlineHow: [
    'In Chrome or Edge, press F12, choose the Network panel, open the menu that reads No throttling and choose Offline. Leave the developer tools open, because the tab goes back online when they close.',
    'In Firefox, open the File menu and choose Work Offline. If you cannot see the menu bar, press Alt.',
    'If the developer tools are not available on this computer, use Firefox and Work Offline.',
    'On a phone or a tablet, the page cannot be taken offline in this way, so please use it on a computer.',
  ],
  noFiles: 'The page will not accept any files while it is online.',
  offlineDone: 'This tab is offline, so nothing loaded or pasted here can leave it.',
  locked:
    'This tab has gone back online, so the page has let go of the dictionary and of everything pasted. If the clinician saved the hospital schema, nothing is lost: the clinician reloads the page, takes it offline and opens the saved hospital schema at step 3.',
  policySummary: 'How the page makes sure that nothing can leave it',

  // 2. The dictionary.
  dictionaryWhat: 'Give the page the data dictionary in one of four ways:',
  // The four ways, each a heading and one sentence; only the chosen one's controls are shown beneath.
  ways: [
    ['create', 'Create it from the database', "The database analyst runs one query on the hospital's database, and the page makes the data dictionary from its result."],
    ['upload', 'Upload a dictionary file you already have', "The clinician chooses a file that the hospital's team that looks after the record system has exported from the vendor's dictionary, or a result of the query in the first way that was saved earlier."],
    ['invented', 'Use the invented dictionary to try the page', 'The page fetches the dictionary of a made-up hospital, and the invented hospital that answers its queries, from this site while the tab is still online.'],
    ['saved', 'Open a saved hospital schema', 'If the page saved a hospital schema at an earlier sitting, the clinician opens it at step 3.'],
  ] as [string, string, string][],
  // Beside each input, where it comes from: run on the database, asked of someone, or made here.
  choiceDatabase: 'Create it from the database',
  databaseOrigin:
    "Run this on the database. The database analyst copies the query below, runs it in the SQL window connected to the hospital's database, and brings the result back here in one of the two ways below.",
  databaseCopy: 'Copy the query',
  databaseSafeSummary: 'Why this query is safe on production, and what it returns',
  databaseReturns:
    "The query returns one row for each column of every table and view: the table, the column, its data type, whether it is part of its table's primary key, the number of rows in the table, and the description that the database holds for the column, which is empty where it holds none. The page reads the result as the data dictionary. Because the result also says which tables exist and how large they are, it answers step 5 at the same time. The result stays in this tab and in the saved hospital schema.",
  databaseBack:
    'Once the query has finished, bring the result here in whichever of these two ways suits its size. A result of more than about 20,000 rows is easier to save as a file than to paste.',
  databaseSmall: 'For a small database, copy the result with headers and paste it here.',
  databaseSmallHow:
    'In SQL Server Management Studio, the database analyst clicks the empty square at the top left of the results grid, then right-clicks it and chooses Copy with Headers. The analyst pastes the result into the box below and choose Read the result.',
  databasePasteLabel: 'The result, copied with its headers:',
  databaseRead: 'Read the result',
  databaseLarge: 'For a large one, save the result as a file, then choose it here.',
  databaseLargeSummary: 'How to save the result as a file in SQL Server Management Studio',
  databaseLargeHow: [
    'Before the first time, open the Tools menu and choose Options, then Query Results, SQL Server and Results to Grid. Tick Include column headers when copying or saving the results, and choose OK. The database analyst need do this only once.',
    'Once the query has run, the database analyst right-clicks the results grid, chooses Save Results As, chooses CSV as the type of file and saves it, then chooses the file below.',
    'If the result is too large to show in the grid, the database analyst opens the Query menu before running the query and chooses Results To and then Results to File. When the query runs, SQL Server Management Studio asks where to save the result. For this way, also set two things once, under Tools, Options, Query Results, SQL Server and Results to Text: set Output format to Tab delimited, and set Maximum number of characters displayed in each column to 8192, so that long descriptions are kept whole.',
  ],
  databaseFileLabel: 'The saved result:',
  databaseReading: 'The page is making the data dictionary from the result.',
  databaseReceipt,
  databaseUnreadable: "The page could not read this as the result of the data dictionary query. Make sure that it is the result of the query shown here, with its headers, then paste it or choose the file again.",
  choiceReal: 'Upload a dictionary file you already have',
  choiceRealWhat:
    "The clinician asks the hospital's team that looks after the record system for the vendor's own data dictionary, exported as a table with three columns: the table name, the column name and the description. If the data dictionary has already been created from the database, the page adds the file's descriptions to it, which makes the proposals much better. If it has not, the page reads the file as the data dictionary itself, and step 5 then asks for one more query to check which tables exist. A result of the query in the first way, saved earlier as a file, is read just as if it had been pasted there. The vendor's export is licensed material: it stays in this browser and in the saved hospital schema, which stays on hospital storage.",
  choiceInvented: 'Use the invented dictionary to try the page',
  choiceInventedWhat:
    'The page makes this; nothing is run on the database. The invented dictionary describes a made-up hospital. The page fetches it from this site with the invented hospital, a small database of made-up rows on which the page runs each of its queries itself, so the clinician loads it while the tab is still online, then takes the tab offline.',
  inventedLoad: 'Load the invented dictionary',
  choiceSaved: 'Open a saved hospital schema',
  choiceSavedWhat: ['The page makes this; nothing is run on the database. The page saved this file at step 9 of an earlier sitting. The clinician opens it at ', 'step 3', '.'],
  inventedOnlineOnly:
    'The invented dictionary can only be loaded while the tab is online. Go back online, load it, then take the tab offline again.',
  inventedLoading: 'The page is loading the invented dictionary.',
  inventedFailed: 'The page has not been able to fetch the invented dictionary. If the tab is still online, reload the page and try again.',
  dictionaryLabel: 'The dictionary file, with one row for each column:',
  dictionaryAbout:
    "The vendor's data dictionary describes every table and column of the reporting database in words. The hospital's team that looks after the record system exports it from the vendor's dictionary tool as a CSV or tab-separated file with a row of headings, such as TABLE_NAME, COLUMN_NAME and DESCRIPTION. The page uses the descriptions to propose where each part of the record is held, and shows each one beside its column. The page reads the file in this tab only and sends it nowhere.",
  tablesLabel: "The vendor's file of tables, if the team can export one, with one row for each table:",
  tablesAbout:
    "This optional file comes from the same export. It gives each table's description and the column that identifies its rows, which help the page choose the right table and find how the tables link.",
  referenceLabel: "A reference conversion's lineage, if the team has one:",
  referenceAbout:
    "This optional file is the lineage that the compare tool writes from another hospital's conversion to OMOP, which the page reads as further evidence beside the dictionary, and it stays in this browser and in the saved hospital schema.",
  referenceReceipt: (r: { file: string; targets: number }) =>
    `The page has also read the reference conversion's lineage, which covers ${plural(r.targets, 'OMOP table', 'OMOP tables')}.`,
  headingsSummary: 'If the page cannot find the headings',
  headingsWhat:
    'The page looks for the usual headings, such as TABLE_NAME, COLUMN_NAME and DESCRIPTION. If the file uses others, the clinician writes them here exactly as they appear in its first row and leaves the rest empty.',
  headingFields: [
    ['table', 'The heading of the table names:'],
    ['column', 'The heading of the column names:'],
    ['description', 'The heading of the descriptions:'],
    ['data_type', 'The heading of the data types:'],
    ['key', 'The heading that marks the identifying column:'],
  ] as [string, string][],
  dictionaryLoad: 'Load the dictionary',
  vendorLoad: 'Add the descriptions',
  dictionaryReading: 'The page is reading the dictionary.',
  vendorReceipt: (vendor: { matched: number; gained: number }) => describeVendor(vendor),
  dictionaryReceipt: (r: { tables: number; columns: number; described: number; keyed: number; skipped: number; source?: string | null; saved?: boolean; vendor?: { matched: number; gained: number } | null }) =>
    r.source === 'database'
      ? databaseReceipt(r)
      : `The page has read ${r.source === 'invented' ? 'the invented dictionary' : r.source === 'saved' ? 'the dictionary from the saved schema' : 'the dictionary'}: ${plural(r.columns, 'column', 'columns')} in ${plural(r.tables, 'table', 'tables')}.${
        r.described === r.columns ? '' : ` ${plural(r.described, 'column has', 'columns have')} a description.`
      }${r.skipped ? ` The page left out ${rows(r.skipped)} whose names are not plain table and column names.` : ''}`,
  dictionaryFailed: 'The page could not read this file as a dictionary. Make sure that it is the CSV export, then choose it again.',

  // 3. The saved hospital schema.
  folderWhat:
    'If the clinician saved a hospital schema at an earlier sitting, the clinician chooses Open a saved hospital schema and picks the file. The page reads the data dictionary and everything settled from it, and the work carries on from where it was left. At a first sitting, the clinician can leave this step.',
  folderLabel: 'The saved hospital schema:',
  folderChoose: 'Open a saved hospital schema',
  folderAbout:
    "The saved hospital schema is the one file that the page saves at step 9. It holds the data dictionary, the hospital schema, the recorded answers, the hospital's codes, the counts, and every query that was run with its result. It stays on the hospital's own storage, and the page reads it in this tab only.",
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
    "After a change to the database or a new release of the vendor's system, the clinician chooses Check against the database. The page proposes the hospital schema again from the dictionary and the recorded answers, compares it with the saved schema, and lists each earlier query for the database analyst to run again.",
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
      ? database === 'invented'
        ? `The invented hospital gave the earlier result below on ${when(pasted)}.`
        : `The earlier result below was pasted on ${when(pasted)}${database ? `, from the ${database === 'training' ? 'training' : 'production'} database` : ''}.`
      : 'No result was pasted for this query.',
  checkPasteLabel: "The database analyst runs the query again on the hospital's database, then pastes the new result here with its headers:",
  checkCompare: 'Compare with the earlier result',
  checkNoChange: 'The new result is the same as the earlier one.',
  checkChanges: 'The new result differs from the earlier one:',

  // 4. The proposal.
  proposeWhat:
    'The clinician chooses Propose the hospital schema. The page describes the anaesthetic record as a set of parts, such as patients, anaesthetics and the readings charted during each, and matches each part to a table and its columns in the dictionary.',
  proposeAboutSummary: 'How the page proposes the hospital schema',
  proposeAbout:
    "The page compares the words that describe each part of the record with the dictionary's names and descriptions. It works by fixed rules, in this tab only and with no language model, so the same dictionary always gives the same proposal.",
  proposeButton: 'Propose the hospital schema',
  proposeProgress: (done: number, total: number) =>
    done === 0 ? 'The page is indexing the dictionary.' : `The page has proposed ${done} of ${total} parts of the record.`,
  proposeDone: (roles: number, drafted: number) =>
    `The page has proposed a table for ${drafted} of the ${roles} parts of the record. The database analyst confirms or corrects each one in step 6.`,
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
  // The confidence beside a proposal, by what the proposal rests on, so that it agrees with the reason shown under it.
  confidence: {
    words: {
      high: "high confidence, as the dictionary's words match",
      medium: "medium confidence, as the dictionary's words match in part",
      low: "low confidence, as few of the dictionary's words match",
    },
    name: { high: 'high confidence, as the names match', medium: 'medium confidence, as the names match', low: 'low confidence, as only the names match' },
    key: { high: "high confidence, as this column identifies the part's rows", medium: "medium confidence, as this column identifies the part's rows", low: "low confidence, as this column identifies the part's rows" },
    link: { high: 'high confidence, as the column names match', medium: 'medium confidence, as the column names match', low: 'low confidence, as the link may repeat rows' },
    reference: {
      high: 'high confidence, as a conversion at another hospital reads this column',
      medium: 'medium confidence, as a conversion at another hospital reads this column',
      low: "low confidence, as only a conversion at another hospital reads this column and the dictionary's words do not match",
    },
  } as Record<string, Record<string, string>>,
  reasonLabel: 'Why the page proposed it:',
  alternativesLabel: 'Other columns that came close',

  // 5. The tables and columns query.
  tablesWhat:
    "Run this on the database. The database analyst chooses the database, then Write the query, runs the query in the SQL window connected to the hospital's database, pastes the result below and chooses Read the result.",
  tablesAnswered: 'The data dictionary made from the database has answered this step. Every proposed table exists, and the large ones are marked.',
  tablesInvented:
    'The invented hospital answers this. The clinician chooses Write the query, then Run on the invented hospital, and the page runs the query there and reads its result. The page records the invented hospital as the database, so it does not ask which database the SQL window is connected to.',
  databaseLegend: "The database that the database analyst's SQL window is connected to:",
  databaseOptions: [
    ['production', "The hospital's everyday reporting database, with its real patients, or a recent copy of it"],
    ['training', 'A training or play database with fictional patients'],
    ['unsure', 'I am not sure'],
  ] as [string, string][],
  databaseAboutSummary: 'Why the page asks',
  databaseAbout:
    "A training database has the hospital's real tables and codes but fictional patients, so its counts mean nothing. On a training database the page still settles what depends on the tables alone, and marks every count to be run again on production.",
  databaseUnsure: 'The page will treat it as the production database. If the database analyst later finds that it is a training database, the clinician changes this answer.',
  tablesWrite: 'Write the query',
  tablesNames: (n: number) => `The query asks about ${plural(n, 'table', 'tables')}, including the lookup tables that give names to codes.`,
  tablesCopy: 'Copy the query',
  showQuery: 'Show the query',
  tablesHow: [
    'The database analyst chooses Copy the query, pastes it into the SQL window and runs it.',
    'In SQL Server Management Studio, click the empty square at the top left of the results grid, then right-click and choose Copy with Headers.',
    'Paste the result into the box below and choose Read the result.',
  ],
  querySafeSummary: 'Why this query is safe on production',
  querySafe:
    "The query reads only the database's own records of its tables, and never a row of any table. It may take a minute on a large database, and it needs no special permission beyond reading the database.",
  tablesPasteLabel: 'The result, copied with its headers:',
  tablesRead: 'Read the result',
  tablesReceipt: (r: { tables: number; columns: number; sized: number; asked: number; absent: number }) =>
    `The page has read the result: ${plural(r.columns, 'column', 'columns')} in ${plural(r.tables, 'table', 'tables')}.${
      r.absent ? ` ${plural(r.absent, 'table', 'tables')} did not come back, either because ${r.absent === 1 ? 'it does' : 'they do'} not exist here or because this login cannot see ${r.absent === 1 ? 'it' : 'them'}.` : ' Every table came back.'
    } Each column in step 6 now shows whether the database holds it.`,
  tablesDoubt: 'More than half of the tables did not come back. The database analyst should make sure that the SQL window is connected to the reporting database before going on.',
  tablesUnreadable: "The page could not read this as the query's result. Use Copy with Headers on the whole results grid, then paste it again.",

  // 6. Confirming.
  confirmIntro:
    "Each part of the anaesthetic record has a few columns. For each column, the page proposes the table and column in the hospital's database that holds it, and the database analyst says whether that is right.",
  confirmWhat:
    "Beside each column below, choose Yes, this is right, Choose another column or Not sure. The row for a part's table offers Choose another table, and a column for which the page found nothing offers Choose a column.",
  confirmLegend:
    "Where a column sits in another table, the page shows how the tables are linked: linked by matching A to B means that a row of one table belongs with the row of the other in which B holds the same value as A. Beside each proposal, the page gives its confidence and what it rests on: the dictionary's own words, the names alone, or the column that identifies the part's rows.",
  confirmAboutSummary: 'What happens to each answer',
  confirmAbout:
    'The page records each answer with its date. Choose another column opens a short form. The database analyst fills it in and chooses Check this change, and the page runs the test on made-up rows: it builds rows with no hospital data in this tab and runs the whole hospital schema, with the change, on them. The database analyst then keeps the change or discards it. Each Not sure goes into the list of questions for the database team at the end of this step. Once a column has an answer, Change the answer brings the choices back.',
  // Beside each part, who can usually answer for it.
  whoAnswers: {
    colleague: 'The database analyst can usually answer for this part from knowledge of the record.',
    team: 'Usually only the team that looks after the reporting database can answer for this part, so a Not sure here goes to them.',
  } as Record<string, string>,
  teamParts: ['role_lab', 'role_diagnosis', 'role_note', 'role_finding'],
  partCount: (answered: number, total: number) => `${answered.toLocaleString('en-AU')} of ${total.toLocaleString('en-AU')} answered`,
  tally: (t: { confirmed: number; corrected: number; not_sure: number; remaining: number; untranslated: number; total: number; tables: number; tables_remaining: number }) => {
    const n = (v: number) => v.toLocaleString('en-AU');
    return `Of ${n(t.total)} columns, ${n(t.confirmed)} ${t.confirmed === 1 ? 'is' : 'are'} confirmed, ${n(t.corrected)} corrected and ${n(t.not_sure)} not sure, ${n(t.untranslated)} ${t.untranslated === 1 ? 'is' : 'are'} confirmed but ${t.untranslated === 1 ? 'its codes are' : 'their codes are'} not yet translated, and ${n(t.remaining)} ${t.remaining === 1 ? 'remains' : 'remain'} to answer. The page proposed a table for ${n(t.tables)} parts of the record, and ${n(t.tables - t.tables_remaining)} of those tables ${t.tables - t.tables_remaining === 1 ? 'has' : 'have'} an answer.`;
  },
  tallyLabels: { confirmed: 'Confirmed', corrected: 'Corrected', not_sure: 'Not sure', untranslated: 'Confirmed, codes not yet translated', remaining: 'Still to answer' } as Record<string, string>,
  tablesTally: (answered: number, total: number) => `The tables of the parts: ${answered.toLocaleString('en-AU')} of ${total.toLocaleString('en-AU')} answered.`,
  yes: 'Yes, this is right',
  another: 'Choose another column',
  anotherRows: 'Choose another table',
  chooseColumn: 'Choose a column',
  chooseTable: 'Choose a table',
  notSure: 'Not sure',
  change: 'Change the answer',
  anotherLabel: 'Choose one of the other columns that the page found, and the page shows what it means at once:',
  anotherNoneFound: 'The page found no other column for this.',
  anotherTable: 'Or choose a table, and then one of its columns from the list:',
  anotherTableFirst: 'Choose a table, and then one of its columns from the list:',
  anotherColumn: 'The column of that table:',
  anotherRowsLabel: 'Write the name of the table that holds one row for each, then choose Use this one, and the page will propose this part again from it:',
  anotherRowsChoose: 'Choose one of the other tables that the page found, and the page shows what it means at once:',
  anotherRowsWrittenOr: 'Or write the name of the table, then choose Use this one, and the page will propose this part again from it:',
  anotherWritten: 'Write it as TABLE.COLUMN, then choose Use this one:',
  anotherWrittenOr: 'Or write it as TABLE.COLUMN, then choose Use this one:',
  anotherNone: 'None of these',
  anotherUse: 'Use this one',
  // A column of a flag or a kind whose source holds codes, before and after Yes.
  coded: {
    flag: 'This column holds codes rather than 1 and 0. After Yes, the page opens a short form in which the database analyst says which of its values mean yes, which the page then reads as 1, and every other value as 0.',
    kind: "This column holds the hospital's own codes. After Yes, the clinician translates them in the list of codes in step 7.",
    kindForm: "This column holds the hospital's own codes. After Yes, the page opens a form in which the clinician translates them.",
    flagNext:
      'To finish this column, the database analyst says which of its values mean yes in the form below, either by typing them in the box, separated by commas, or by choosing Write the query of values and ticking them in the list that its result gives. The analyst then chooses Check this change.',
    heading: 'Translate the codes of this column',
    kindNext: "To finish this column, translate its codes in step 7, where the page lists the codes in use.",
    kindNextForm: "To finish this column, translate its codes in the form below, then choose Check this change.",
  },
  landed: (text: string) => `The test on made-up rows found: ${text} The database analyst chooses another column or table for this row, or Not sure if it is unclear.`,
  landedCount: (text: string) => `The count at step 8 found: ${text} The database analyst chooses another column for this row, or Not sure if it is unclear.`,
  // The first row of step 6 that shows each of these says what it means, in one line.
  glossLink:
    'Linked by matching A to B means that the rows of one table are joined to the rows of the other where these two columns hold the same value.',
  glossConfidence:
    "The confidence beside a proposal says how closely the dictionary matched, and the words after it say what the proposal rests on: the dictionary's own words, the names alone, or the column that identifies the part's rows.",
  glossLookup: (table: string) => `${table} is a lookup, which is a small table that gives the name for each code.`,
  // The date that a person reads, as 8 October 2026, everywhere on the page.
  day,
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
    unknown: ['', 'Step 5', ' will say whether the database holds this.'],
  },
  questionsHeading: 'Questions for the database team',
  questionsWhat: 'Each column marked Not sure is listed here. The clinician chooses Copy the questions and pastes them into an email to the team, and the database analyst answers each column above when the team replies.',
  questionsNone: 'There are no questions yet.',
  questionsCopy: 'Copy the questions',
  questionsNoteHead: 'Questions about the reporting database, from the description of the anaesthetic record. For each, the page states what it proposes and asks whether that is right:',
  questionsMeaning: (meaning: string) => `The description of the record defines it as follows: ${meaning}`,
  confirmFailed: 'The page could not record that answer. Please try again.',

  // 7. The codes.
  codesWhat:
    "Run these on the database. Some columns hold the hospital's own codes, such as the kind of each reading. For each list below, the database analyst chooses Write the list, runs it in the SQL window connected to the hospital's database, pastes the result and chooses Read the list. The clinician then chooses what each code means and chooses Save these codes.",
  codesWhatInvented:
    "The invented hospital answers these. Some columns hold the hospital's own codes, such as the kind of each reading. For each list below, the clinician chooses Write the list, then Run on the invented hospital, then chooses what each code means and chooses Save these codes.",
  codesAboutSummary: 'How the list works, and why it is safe',
  codesSafe: (limit: string) =>
    `The list counts the codes used on the anaesthetics of the year above, with their names. It runs in two parts. Part 1 first fills #cohort, a temporary table of the chosen anaesthetics, at most ${limit} of that year. Part 2 then reads only their rows, never the whole of a large table.`,
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
  kindsSummary: 'The kinds the page knows, and what each means',
  // A kind in plain words, with its code after it, as the lists and forms show it.
  kindOption: (kind: string, meaning?: string) => (meaning ? `${meaning.replace(/[.\s]+$/, '')} (${kind})` : kind),
  chartedWrite: 'Write the list',
  chartedCopy: 'Copy the list',
  chartedPasteLabel: 'The result of the list, copied with its headers:',
  chartedRead: 'Read the list',
  chartedReceipt: (n: number, year: number) =>
    `The page has read ${plural(n, 'code', 'codes')} charted in ${year}. The clinician chooses the kind of each code that the clinician recognises, leaves the rest as Not chosen, then chooses Save these codes.`,
  codesOther: 'A code left as Not chosen counts as other, which each list also offers.',
  chartedEmpty: (year: number) => `The list for ${year} came back empty. The database analyst looks again at this part's columns at step 6, or the clinician chooses another year.`,
  chartedEmptyPatient: (year: number) =>
    `The list for ${year} came back empty. This usually means that the anaesthetics are not reaching their patients, so the database analyst looks again at the patient's identifier in Anaesthetics at step 6, or the clinician chooses another year.`,
  chartedColumns: ['Code', 'Times charted', 'Anaesthetics', 'Name', 'Which of the kinds the page knows'],
  notChosen: 'Not chosen',
  codesSave: 'Save these codes',
  codesSaved: (n: number, date: string) => `The page saved ${plural(n, 'code', 'codes')} for this list on ${day(date)}.`,
  // The lists that the page cannot write yet, gathered after the lists that it can.
  vocabulariesWaiting: 'The page cannot yet write a list for these columns:',
  vocabularyWaiting: (title: string, reason: string) => `${title}: ${reason}`,
  codesFromRow: 'Go to this list in step 7',
  underTen: 'under 10',
  codesNoneSaved: 'No codes are saved for this list yet.',
  kindMeaning: (kind: string, meaning: string) => `${kind}: ${meaning}`,

  // 8. The counts.
  countsWhat:
    "Run these on the database. The clinician chooses Write the counts. For each count, the database analyst chooses Copy the count, runs it in the SQL window connected to the hospital's database, pastes its result and chooses Read the result, and the clinician then chooses Save whether these look right.",
  countsWhatInvented:
    'The invented hospital answers these. The clinician chooses Write the counts, then for each count chooses Run on the invented hospital and then Save whether these look right.',
  countsAboutSummary: 'What the counts are for',
  countsAbout:
    "The counts show whether the hospital schema reaches the record in every year. Each count is rounded down to ten. A year or a group with fewer than ten is left out, and a figure under ten within a group shows as under 10. The rounding and the leaving out reduce what a count can disclose, but they do not make the results anonymous, and repeated counts over slightly different groups can reveal more than one count does. The results are therefore for use inside the hospital until the hospital's own rules say otherwise.",
  // Beside the figures of a count or a list that reads the anaesthetics of one year in #cohort.
  fromSample: (year: number | string, limit: string) =>
    `These figures are from a sample: the anaesthetics of ${year}, at most ${limit} of them. They show what is charted, but not how much the whole record holds.`,
  countsWrite: 'Write the counts',
  countsAgain: 'If a column or a code changes, the clinician chooses Write the counts again.',
  countHeading: {
    coverage_by_year: 'The anaesthetics of each year',
    repeated_keys: 'Rows that appear twice',
    readings_by_kind: 'The readings of one year, by kind',
  } as Record<string, string>,
  countWhat: {
    coverage_by_year:
      'This count shows, for each year, how many anaesthetics the hospital schema finds and how many of them have a patient, a date of birth, a date of death and a recorded stop.',
    repeated_keys: 'This count shows whether any patient or anaesthetic appears on more than one row, which would make an audit count it twice.',
    readings_by_kind: 'This count shows the readings of {year}, the year chosen in step 7, by kind, so that the clinician can see whether the codes chosen in step 7 reach them.',
  } as Record<string, string>,
  countTablesSummary: 'Which tables this count reads',
  countSafe: 'This count reads no table of readings, so it is safe to run on production.',
  countTraining: 'Run on the training database. The database analyst runs it again on production before the figures are used.',
  countTrainingNote: 'The SQL window is connected to a training database, whose patients are fictional, so these figures show only that the query runs.',
  countScript: (limit: string) =>
    `This count reads the readings, so it runs in two parts. Part 1 first fills #cohort, a temporary table of the chosen anaesthetics, at most ${limit} of the year. Part 2 then reads only their readings.`,
  countTables: 'It reads these tables:',
  sizeUnknown: 'size not known',
  sizeRows: (n: number) => (n < 10 ? 'fewer than ten rows' : `about ${rows(n)}`),
  countCopy: 'Copy the count',
  countPasteLabel: 'The result of this count, copied with its headers:',
  countRead: 'Read the result',
  countReceipt: (n: number) => `The page has read ${rows(n)} of this count.`,
  countNoFindings: 'The page sees nothing unusual in this count.',
  countFindingCodes: 'The clinician follows the link to the list at step 7, chooses the kind of each code again, and writes the counts again.',
  countFindingDo: 'The database analyst follows the link to the column and chooses another column there, and the clinician writes the counts again.',
  lookRightLegend: 'Record whether these figures look right:',
  lookRightCompare: {
    coverage_by_year: 'The clinician compares each year with the number of anaesthetics that the department gives in a year.',
    repeated_keys: 'The clinician compares the figures with none, because a patient or an anaesthetic should appear on one row only.',
    readings_by_kind: 'The clinician compares the readings of each kind with what is usually charted, such as a mean pressure every few minutes of an anaesthetic.',
  } as Record<string, string>,
  lookRight: [['yes', 'Yes, they look right'], ['no', 'No, something is wrong']] as [string, string][],
  lookRightNote: 'A note, if the clinician wants to record what looks wrong or why:',
  lookRightNoteSaved: (note: string) => `The note saved with it: ${note}`,
  lookRightSave: 'Save whether these look right',
  lookRightSaved: (answer: string, date: string) =>
    answer === 'yes' ? `On ${date}, the clinician recorded that this count looks right.` : `On ${date}, the clinician recorded that something in this count is wrong.`,
  lookRightTraining: 'The page has recorded that this was judged on the training database, and a note inside the saved file lists the count to run again on the everyday reporting database.',

  // 9. Saving the hospital schema.
  writeWhat:
    'The clinician chooses Save the hospital schema. The page saves everything settled so far as one file, which the clinician opens at step 3 at the next sitting to carry on.',
  writeAboutSummary: 'What the file holds',
  writeAbout:
    "The file holds the data dictionary, the hospital schema with one SQL file for each part of the record, the hospital's codes, the counts and whether they looked right, every query with its result, the answers in order, and a note that explains each part.",
  writeSave: 'Save the hospital schema',
  writeSaveNote: "The clinician keeps the file on the hospital's own storage, because it holds the hospital's data dictionary.",
  // The receipt of a save names the state of readiness that the parts every audit reads have reached.
  saved: (draft: string, reached: string | null = 'runs') =>
    `The page has saved a new version of the hospital schema${draft ? ` as a draft (${draft})` : ''}. ${
      reached === 'clinically validated'
        ? 'The parts that every audit reads are clinically validated, the third of the three states, by a reconciliation against the clinical record that was entered through the evidence import.'
        : reached === 'checked against the database'
        ? 'The parts that every audit reads are checked against the database, the second of the three states. None is clinically validated, because only a reconciliation against the clinical record can establish that.'
        : reached === 'runs'
          ? 'The parts that every audit reads run on made-up rows, the first of the three states, and have not yet been checked against the database.'
          : 'The parts that every audit reads have not yet reached the first of the three states, because the test on made-up rows finds a problem in at least one of them.'
    } Keep the file on the hospital's own storage.`,
  // Where step 9 begins: the three states of readiness, once.
  readiness:
    "The saved file records the evidence for each part of the hospital schema, and Schemalyser works out from that evidence how far each part has been checked, in three states. A part runs once it compiles and runs on made-up rows, and it is checked against the database once the counts that read it have been run on the hospital's database and the clinician has judged them to look right. The file records the coverage that the counts measured beside that judgement and keeps the two apart, because a count can look right and still reach too few anaesthetics. A part is clinically validated only once someone has reconciled a sample of anaesthetics against the clinical record and the result has been entered through the evidence import, which this page does not do. Each save is a new version, and its file name carries the version's identifier.",
  // The time zone of the database's clocks, asked once before the save.
  timeZoneLegend: "The time zone of the hospital's database",
  timeZoneWhy:
    "The hospital schema gives each time as the database holds it, without converting it, so the saved file records the time zone that the database's clocks follow. If the database analyst knows that the database keeps another, such as UTC, the clinician changes it here before saving.",
  // Where the time zone in the box came from. The page never records this computer's zone as if a person had given it.
  timeZoneProposed:
    "The page has proposed this computer's own time zone and daylight saving. If you save them unchanged, the saved file records that the page proposed them from this computer, rather than that a person gave them.",
  timeZoneRecorded: {
    'a person': 'The hospital schema records this time zone as given by a person.',
    'proposed from this computer': "The hospital schema records this time zone as proposed from this computer, because it was saved unchanged. If the database analyst confirms the zone, type it into the box again, and the next save records it as given by a person.",
  } as Record<string, string>,
  timeZoneLabel: 'The time zone, as a name such as Australia/Sydney or UTC:',
  daylightLabel: "The database's clocks change with daylight saving",
  // How the proposals fared, under step 9.
  scoreboardHeading: 'How the proposals fared',
  scoreboardWhat: "This text counts how the page's proposals fared against the database analyst's answers, overall, for each part of the record and for each category of column. The categories are counted apart because a wrong link between a reading and its anaesthetic matters far more than a missing descriptive column. The text names no table or column, so it may be shared.",
  scoreboardCopy: 'Copy the text',
  draftNote: (unfinished: string) => `Some of the hospital schema is not yet answered: ${unfinished}. The clinician can save it now as a draft, which a note inside the saved file records, and finish it at a later sitting.`,
  draftCodes: 'These columns are answered, but their codes are not yet translated. Each link leads to its row:',
  savedDraft: (left: string) => `Saved as a draft, ${left}`,
  writtenStale: 'Something has changed since the hospital schema was saved, so the clinician should save it again.',
  writeFailed: 'The page could not save the hospital schema. Please try again.',

  // 6. Corrections, each a sentence to complete.
  corrections: {
    intro: 'The database analyst chooses the kind of change, then fills in the form below it. The page writes the SQL.',
    findingDo: 'The database analyst follows the link to its row, then chooses another column or table there, or Not sure if it is unclear.',
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
      filter: "Use this when the table holds rows of more than one kind of record and a code on each row says which kind it is, such as a shared table of events or a flag that marks a row as deleted. Keep the rows whose code says they are this part's kind of record. A filter by clinical meaning, such as only one class of drug or only the operations of one specialty, belongs in the question and not in the hospital schema, so the page does not offer it here.",
      path: 'Use this when the value is reached through one, two or three other tables. The page suggests each link from the dictionary.',
      pair: 'Use this when the next table can be matched only on two columns at once, such as a case number and a line number.',
      joined: 'Use this when the text is spread over several rows, such as the lines of a note.',
      codes: "Use this when the column holds the hospital's own codes. Step 7 offers the same choice from the codes actually in use.",
    } as Record<string, string>,
    tableLabel: 'Take the value from the table',
    columnLabel: 'and its column',
    filterTable: 'Keep only the rows of the table',
    filterColumn: 'whose column',
    chooseTable: 'Choose the table first',
    chooseColumn: 'Choose a column',
    valuesLabel: 'holds any of these values, separated by commas',
    flagValuesLabel: 'It is 1 where the column holds any of these values, separated by commas, and 0 otherwise',
    valuesNote: "To pick from the values that the column actually holds, choose Write the query of values, run it in the SQL window connected to the hospital's database, and paste the result here.",
    valuesWrite: 'Write the query of values',
    valuesCopy: 'Copy the query of values',
    valuesPasteLabel: 'The result of the query of values, copied with its headers:',
    valuesRead: 'Read the values',
    valuesTick: 'Tick each value that means yes:',
    valuesTickFilter: 'Tick each value of the rows to keep:',
    valuesLook: 'The values that this column holds:',
    valuesLookNote: "To see what the chosen column holds, choose Write the query of values, run it in the SQL window connected to the hospital's database, and paste the result here.",
    withheld: "The page does not offer columns that hold a person's name, address, contact details or medical record number.",
    valuesRan: (n: number) => `The page has read ${plural(n, 'value', 'values')} of this column.`,
    probeRan: 'The page has read the result of the test query.',
    // A value with its rows. The core marks a count that its query left empty because it was under ten.
    valueRows: (value: string, rows: number | null, suppressed?: string | null) => `${value || '(empty)'}${rows !== null
      ? `, in about ${rows.toLocaleString('en-AU')} rows` : suppressed === 'under_ten' ? ', in fewer than ten rows' : ''}`,
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
    checkingUse: 'The page runs the test on made-up rows before the database analyst keeps this change.',
    sqlLabel: 'Show the SQL that the page will write',
    incomplete: 'Once the form is filled in, the page shows what the change means.',
    incompleteColumn: 'Once a column is chosen, the page shows what the change means.',
    incompleteRows: 'Once a table is chosen, the page shows what the change means.',
    checkWhat: 'The database analyst chooses Check this change. The page runs the test on made-up rows: it builds rows with no hospital data in this tab and runs the whole hospital schema, with the change, on them.',
    passedMeans:
      'This change keeps the hospital schema whole on made-up rows. It does not say whether the column means what the database analyst thinks; a counting query run on the database afterwards, and the analyst\'s own knowledge, decide that.',
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
    kept: (date: string, check: string) => {
      const old = check.match(/^passed: broke nothing new; (.+)$/);
      if (old) return `Kept on ${date}. On the test on made-up rows, this change broke nothing new; ${old[1]}.`;
      return check.startsWith('passed') ? `Kept on ${date}. The test on made-up rows passed.` : `Kept on ${date}, although the test on made-up rows failed.`;
    },
    keptReason: (reason: string) => `The reason given: ${reason}`,
    modelCheck: 'Test the hospital schema on made-up rows',
    modelCheckWhat:
      'Before anything changes, the database analyst can choose Test the hospital schema on made-up rows to see what is already wrong. The page builds rows with no hospital data and runs every part of the schema on them. Each finding links to its column below.',
    modelCheckWhatAfter: 'The database analyst chooses Test the hospital schema on made-up rows to test the schema with every answer and change so far. Each finding links to its column below.',
    probeWhat: {
      link: "The database analyst runs this test query in the SQL window connected to the hospital's database to try the link. It counts the anaesthetics of {year}, the year chosen in step 7, that have at least one row through the link, and those with none.",
      filter: "The database analyst runs this test query in the SQL window connected to the hospital's database. It counts the rows read and how many of them pass the filter.",
      flag: "The database analyst runs this test query in the SQL window connected to the hospital's database. It counts the rows in which the flag is 1, 0 and empty.",
      flag_two: "The database analyst runs this test query in the SQL window connected to the hospital's database. It counts the rows in which the flag is 1 and 0. The form never leaves this flag empty.",
    } as Record<string, string>,
    probeWrite: 'Write the test query',
    probeCopy: 'Copy the test query',
    probePasteLabel: 'The result of the test query, copied with its headers:',
    probeRead: 'Read the result of the test query',
    probeNone: 'There is no test query for this kind of change, so the test on made-up rows is its only test.',
    valuesKept: (n: number) => `Before the database analyst kept this change, the query of values showed that the column holds ${plural(n, 'value', 'values')}:`,
    problemLabel: 'The page cannot use the form yet:',
  },

  // The invented hospital, which answers every query on the page when the invented dictionary is in use.
  invented: {
    run: 'Run on the invented hospital',
    receipt: (text: string) => `Run on the invented hospital: ${text}`,
    running: 'The invented hospital is running the query.',
    fetching: 'The page is fetching the invented hospital. Please keep the tab online until it says that the invented dictionary is loaded.',
    failed: 'The invented hospital could not run this query. Please try again.',
    unavailable:
      'The page has not been able to fetch the invented hospital, so its queries cannot be run here. If the tab is still online, reload the page and load the invented dictionary again.',
  },

  failed: 'The page could not finish that. Everything settled before it is kept.',
  copied: 'Copied.',
  version: (version: string) => `Schemalyser ${version}`,
};
