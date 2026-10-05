// The wording of the page, as approved on 3 and 4 October 2026, with the checklist's wording added on
// 4 October 2026 in the same voice. The owner no longer reviews this internal tool's wording.
// The sentences that summarise the results and say what could not be read come from the core
// (core/schemalyser/vocabulary.py), because the coverage file carries the same wording.

// The note to send with the first query. A test confirms that it is the text of docs/first-ask-note.md.
const NOTE = `Subject: A request for help with an approved anaesthesia audit, in short steps

Hello,

I am running an approved audit, under the approval [approval reference], and I would like your help to answer it from the source database. The audit asks [the question in one sentence]. It is also the first step towards an OMOP anaesthesia layer, so the answers that you give me will be used again when that layer is built.

The help that I need comes in short steps, and I will send each one only after the one before it.

1. One query that reads only SQL Server's own records. It lists the columns of the tables that our existing anaesthesia queries already read, and the number of rows that the server records for each, rounded down to the nearest ten. It reads no table.
2. A few questions that you may be able to answer from what you know, such as whether two columns join and which codes mean a mean arterial pressure, and, where a question cannot be answered that way, a few short counting queries.
3. The audit query itself, or a one-page specification of it if you would rather write it yourself.

Each query is a single SELECT that writes, creates and changes nothing. It reads WITH (NOLOCK), which means that it takes no row locks, but it holds a schema lock while it runs, so please do not run it during the nightly load. Each counting query rounds its counts down to the nearest ten and leaves out anything that fewer than ten rows hold, and a query on a large table reads a sample of about five million rows, so its counts are estimates. Each query has a comment at the top that says what it does, so that you can read it before you run it.

You would paste each result back to me. I put the results into a page that runs in my browser on a hospital computer, with that browser tab taken offline so that the page cannot send anything anywhere. The results, and the facts that you confirm, are kept in a repository that the hospital controls, [the repository].

The queries are written by a tool that I built with the help of an AI model, [the model and the service]. These controls can be checked: the model worked only from invented examples and never saw any hospital data, any of your team's SQL or any name from our database; the tool runs offline; and its code is open to read. The use of AI in this work follows [the hospital's AI policy].

Thank you for considering it. I am glad to go through any of it with you in person.`;

const files = (n: number) => (n === 1 ? 'file' : 'files');
const items = (n: number) => (n === 1 ? 'item' : 'items');
const queries = (n: number) => (n === 1 ? 'query' : 'queries');
const rows = (n: number) => (n === 1 ? 'row' : 'rows');

export const strings = {
  title: 'Schemalyser',
  intro:
    "Schemalyser reads the data team's SQL requests on this computer. For each target query, it shows what the shadow database still needs and which existing SQL would supply it, and it builds an inventory of the tables and columns that the requests use.",

  steps: [
    'Export the catalogue from Clarity, or start without it.',
    'Take this page offline.',
    'Choose the state and the folder of requests.',
    'Work through the checklist for each target query.',
    'Read the inventory.',
    'Download the results and the check script.',
    'Try the inventory in the sandbox.',
  ],

  catalogueWhy:
    'Schemalyser recognises the tables and columns of Clarity in the requests from a list of them. If you have none, you can skip this step: in the third step, Schemalyser writes one short query of the names and sizes of the tables that your SQL files read. If your team prefers to export the whole list, this query in SQL Server Management Studio produces it, and you can save the results as a CSV file.',
  querySafe: "The query reads Clarity's own list of tables and columns. It does not read any patient table.",
  copyQuery: 'Copy the query',
  skipCatalogue: 'If you already have the catalogue file, you can move on to the next step.',

  connected: 'This page is online.',
  isOffline: 'This page is offline.',

  loading:
    'Schemalyser is loading its analysis engine. Please keep the page online until loading has finished.',
  loadFailed:
    'Schemalyser has not been able to load its analysis engine. If the page is online, you can reload it to try again.',
  policyFailed:
    'Schemalyser cannot confirm that this browser will keep your files on this computer, so it has not started. Please use Chrome, Edge or Firefox.',
  loaded:
    'Schemalyser has finished loading. Please take this page offline now. You can take only this browser tab offline, so that the rest of the computer, including your SQL window, stays connected, or you can disconnect the whole computer.',
  policyHeld:
    "While it loaded, Schemalyser tried on purpose to reach two outside addresses, policy-check.invalid and api.github.com, to confirm that this browser stops the page from sending anything elsewhere. The browser refused both, as it should, and the two refusals appear in the browser's console as messages about the Content Security Policy. If the browser had not refused them, Schemalyser would have stopped and said so.",
  offlineHow: [
    'In Chrome or Edge, press F12 to open the developer tools, choose the Network panel, open the throttling menu, which reads No throttling, and choose Offline. Only this tab goes offline. Leave the developer tools open while you use the page, because the tab goes back online when they close.',
    'In Firefox, open the File menu and choose Work Offline. If you cannot see the menu bar, press the Alt key to show it. Firefox then takes all of its own tabs offline, but the rest of the computer, including your SQL window, stays connected.',
    'To disconnect the whole computer instead, turn off Wi-Fi or unplug the network cable. Your SQL window then cannot reach the database until you reconnect.',
    'The page must stay offline for as long as you use it. If it goes back online while it holds your files, Schemalyser locks the page, stops its analysis engine and discards what it has read. The checklist stays on the page. To carry on, choose Begin a new analysis, wait for Schemalyser to load again, take the page offline again and choose the files again. If you saved the state with the button in the checklist, choose the saved files with the state folder, and nothing that you pasted or answered is lost.',
  ],
  noFilesWhileConnected: 'Schemalyser will not accept any files while this page is online.',
  exampleHeading: 'Try the invented example',
  exampleWhat:
    'If you would like to see the whole checklist working before you bring any files, Schemalyser can load an invented example: a catalogue, site rules, check results, a core profile, a conversion, four target queries and fifteen requests, all made up for testing. None of it comes from a hospital. Load it while the page is online, then take the page offline and analyse it in the next step.',
  exampleLoad: 'Load the invented example',
  exampleLoading: 'Schemalyser is loading the invented example.',
  exampleLoaded: (state: number, requests: number) =>
    `Schemalyser has loaded the invented example, which holds ${state} state ${files(state)} and ${requests} request ${files(requests)}. Take the page offline, then choose Analyse the requests in the next step.`,
  exampleFailed:
    'Schemalyser could not load the invented example. If the page is online, you can try again.',
  exampleChosen:
    'The invented example is loaded in place of your own files. Everything in it is made up, and none of it comes from a hospital. If you choose any file of your own, Schemalyser discards the example and anything worked out from it, and starts clean.',
  exampleBanner:
    'This checklist comes from the invented example. Its tables, codes, requests and results are made up, and none of them comes from a hospital.',

  offline: 'This page is offline. You can now choose the files.',
  chooseState: 'Choose the state folder, if you have one:',
  stateNote:
    'The state folder holds catalogue.csv, and may also hold site-rules.json, checks.csv, a folder named conversion, target queries in a folder named targets, core-profile.csv and boundary.json. A file that you choose separately below takes the place of the same file in the folder.',
  stateFound: (catalogue: boolean, conversion: number, targets: number) =>
    `The folder you chose holds ${catalogue ? 'a catalogue' : 'no catalogue'}, ${
      conversion ? `a conversion of ${conversion} ${files(conversion)}` : 'no conversion'
    } and ${targets ? `${targets} target ${queries(targets)}` : 'no target queries'}.`,
  keptForComparison:
    'Schemalyser has kept the checklist from the previous analysis, and it will show what the next analysis answers.',
  chooseCatalogue: 'Choose the catalogue file:',
  catalogueNote: 'The catalogue file is the CSV file that the query in the first step produces.',
  chooseRules: 'Choose the site rules file, if you have one:',
  rulesNote: 'The site rules file tells Schemalyser about naming conventions at your site. Schemalyser works without one.',
  chooseFolder: 'Choose the folder of requests:',
  folderNote:
    'Schemalyser reads every file ending in .sql in the folder you choose, including files in the folders inside it. It ignores all other files.',
  folderCount: (n: number) => `The folder you chose contains ${n} SQL ${files(n)}.`,
  chooseChecks: 'Choose the check results file, if you have one:',
  checksNote:
    'The check results file is the CSV file that the check script produces. With it, Schemalyser can write the values that your requests filter on.',
  checksError:
    'Schemalyser could not read the check results file. Please check that you have chosen the CSV file saved from the check script.',
  analyse: 'Analyse the requests',
  catalogueError:
    'Schemalyser could not read the catalogue file. The file needs the columns TABLE_NAME, COLUMN_NAME and DATA_TYPE. Please check that you have chosen the file from the first step.',
  analysisFailed: 'Schemalyser has not been able to finish reading the requests. You can choose the files and try again.',

  progress: (done: number, total: number) =>
    `Schemalyser is reading the requests. It has read ${done} of ${total} ${files(total)}.`,
  boundaryProgress: 'Schemalyser has read the requests and is now working out the checklist for each target query.',

  // The checklist.
  checklistIntro:
    'Schemalyser ticks each item once the requests, the check results or the other files in the state settle it. The boxes show what Schemalyser has found, and you cannot tick them by hand. Where a short query against Clarity would settle an item, Schemalyser gives the query with the item, and the query of table sizes at the head of each checklist comes first.',
  noChecklist:
    "To see a checklist for each target query, supply a state folder, or Schemalyser's repository on GitHub, that holds a conversion folder and a folder of target queries.",
  boundaryProblems: {
    bad_rules:
      'Schemalyser could not read site-rules.json, so it has not made the checklists. If the file is meant to hold the site rules, check that it is valid JSON and uses only the known keys.',
    bad_options:
      'Schemalyser could not read boundary.json, so it has not made the checklists. The file may set only includeSpans and includeFanout, each to true or false.',
    bad_checks:
      'Schemalyser could not read checks.csv as the results of the check script, so it has not made the checklists.',
    bad_conversion:
      "Schemalyser could not read the conversion folder, so it has not made the checklists. If the folder is meant to hold a conversion, check that conversion.json lists each step and that each step's file is present.",
    bad_profile:
      'Schemalyser could not read core-profile.csv as the result of a profile script, so it has not made the checklists.',
    bad_evidence:
      'Schemalyser could not read sql_evidence.json, because a finding in it names something that the catalogue does not hold or does not have the expected form, so it has not made the checklists.',
    other:
      'Schemalyser has not been able to make the checklists. The inventory in the next step is complete, and you can still download it.',
  } as Record<string, string>,
  tally: (answered: number, total: number, before?: number) =>
    `${answered} of its ${total} blocking ${items(total)} ${answered === 1 ? 'is' : 'are'} answered${
      before !== undefined && before !== answered ? `, compared with ${before} in the previous analysis` : ''
    }.`,
  tallySinceAnalysis: (answered: number, total: number, before?: number) =>
    `${answered} of its ${total} blocking ${items(total)} ${answered === 1 ? 'is' : 'are'} answered${
      before !== undefined && before !== answered ? `, compared with ${before} when the requests were last analysed` : ''
    }.`,
  newlyAnswered: (n: number) =>
    n === 1
      ? 'Since the previous analysis, 1 item has been answered, and Schemalyser lists it first.'
      : `Since the previous analysis, ${n} items have been answered, and Schemalyser lists them first.`,
  changes: (answered: number, targets: number) =>
    answered === 0
      ? 'Schemalyser has analysed the requests again. No item that was open or partly answered before is answered yet.'
      : `Schemalyser has analysed the requests again. Across the ${targets} target ${queries(targets)}, ${answered} ${items(answered)} ${
          answered === 1 ? 'is' : 'are'
        } now answered that ${answered === 1 ? 'was' : 'were'} not answered before.`,
  groupNew: 'Answered since the previous analysis',
  groupSql: 'Items that existing SQL can settle',
  groupSqlNote:
    'For each item below, Schemalyser says what a query from the data team would need to do. If you find such a query, add its file at the top of this step and analyse again.',
  groupSqlNone: 'No open item for this query can be settled by existing SQL.',
  groupOther: 'Items that need something other than SQL',
  groupOtherNote:
    'The check results, the mapping rows, the site rules or the core profile settle these items. Each one says who must act and how, and where a query against Clarity would settle it, the query is given with the item.',
  groupAnswered: (n: number) => `Show the ${n} answered ${items(n)}`,
  blocking: 'This item blocks the simulation.',
  notBlocking: 'This item bears on how realistic the result is, and it does not block the simulation.',
  statusNames: { answered: 'Answered.', partly: 'Partly answered.', open: 'Open.' } as Record<string, string>,
  readinessInFull: 'Show the readiness statement in full',

  // The plain queries on the checklist, and the results pasted back.
  sizesHeading: 'Table sizes',
  queryStates: {
    ready: 'This query is ready to run.',
    waiting: 'This item waits for the table sizes.',
    large: 'Schemalyser offers no query for this item, because a table is too large to check cheaply.',
    ran: 'The query for this item has run.',
  } as Record<string, string>,
  queryShownEarlier: 'The query shown with an earlier item answers this item as well.',
  pasteHeading: 'Paste the results of the queries',
  pasteWhat:
    'Run a query from the checklist below in SQL Server Management Studio, copy its results grid with the headers, and paste it here. You can paste the results of several queries at once. Schemalyser reads them by the same rules as a check results file, adds them to the check results that it holds, and works out the checklist again.',
  pasteLabel: 'The results, copied from the results grid or from a CSV file:',
  readPaste: 'Read the pasted results',
  pasteReading: 'Schemalyser is reading the pasted results and working out the checklist again.',
  pasted: (read: number, accepted: number) =>
    accepted === 0
      ? `Schemalyser has not added any of the ${read} pasted ${rows(read)} to the check results, because none of them is a row that a query from this page could have returned.`
      : accepted === read
        ? `Schemalyser has added the ${read} pasted ${rows(read)} to the check results.`
        : `Schemalyser has added ${accepted} of the ${read} pasted rows to the check results. It left out the others, because a query from this page could not have returned them.`,
  pasteUnreadable:
    'Schemalyser could not read the pasted text as the results of a query from this page. Each row needs the nine columns that the query returns, from check_kind to is_unique.',
  pasteFailed:
    'Schemalyser has not been able to read the pasted results. The checklist is as it was, and you can paste them again.',
  changesAfterPaste: (answered: number) =>
    answered === 0
      ? 'Schemalyser has worked out the checklist again with the pasted results. No item that was open or partly answered before is answered yet.'
      : `Schemalyser has worked out the checklist again with the pasted results, and ${answered} ${items(answered)} ${
          answered === 1 ? 'is' : 'are'
        } now answered that ${answered === 1 ? 'was' : 'were'} not answered before.`,
  profileHeading: 'For the central OMOP team',
  profileWhat:
    "These queries run on the OMOP database, not on Clarity. The first reads only SQL Server's own records, and the others wait for its result, because Schemalyser offers no query on a core table whose size it does not know.",
  queryInProfile: 'The query for this item is in the section for the central OMOP team, above.',
  profilePasteHeading: 'Paste the results of the core profile queries',
  profilePasteWhat:
    'When the central OMOP team returns the results of its queries, paste them here, as copied from the results grid or from a CSV file. Schemalyser reads them by the same rules as a core profile, adds them to the core profile that it holds, and works out the checklist again.',
  profilePasteLabel: 'The results of the core profile queries:',
  readProfilePaste: 'Read the pasted core profile results',
  profilePasteUnreadable:
    'Schemalyser could not read the pasted text as the results of a core profile query. Each row needs the six columns that the query returns, from ITEM_CATEGORY to VALUE_05.',
  saveProfile: 'Save the core profile as core-profile.csv',
  saveProfileNote:
    'The file holds the core profile from the state together with every result that you have pasted. If you put it in the state folder in place of core-profile.csv, the next analysis begins from it.',
  // The first ask, for a project that starts without a catalogue.
  firstHeading: 'Start without a catalogue',
  firstWhat:
    'If you have no catalogue file, Schemalyser can write one short query that asks Clarity for the columns and the sizes of the tables that your SQL files and the conversion read. Choose the folder of requests above, and the state folder if you have one, then write the query. The query holds the table names from your files, so Schemalyser shows it only here and writes it into no file.',
  firstWrite: 'Write the first query',
  firstNames: (n: number, leftOut: number) =>
    leftOut
      ? `The query asks about ${n} tables, which are those that the most files read. Schemalyser left out ${leftOut} more, and you can ask about them in a second project that starts from the saved catalogue.`
      : `The query asks about the ${n} ${n === 1 ? 'table' : 'tables'} that your files read.`,
  firstNone: 'Schemalyser found no table name in the files that it could use, so it has not written a query.',
  firstCopy: 'Copy the query',
  firstPasteLabel: 'The result of the first query, copied from the results grid with its headers:',
  firstRead: 'Read the result',
  firstReadDone: (tables: number, columns: number, sized: number) =>
    `Schemalyser has read ${columns} columns of ${tables} tables, and the sizes of ${sized} of them.${
      sized < tables
        ? ` SQL Server gave no size for the other ${tables - sized}, as happens for a view or where an account cannot read the server's own records, so Schemalyser will count each of those tables only up to 10,000,000 rows when a query needs its size.`
        : ''
    } You can now analyse the requests.`,
  firstUnreadable:
    'Schemalyser could not read the pasted text as the result of the first query. Each row needs the ten columns that the query returns, from TABLE_SCHEMA to TABLE_ROWS.',
  saveState: 'Save the catalogue, the check results and the confirmed facts',
  // The note to send with the first query, as docs/first-ask-note.md gives it.
  noteHeading: 'Show the note to send with the first query',
  noteCopy: 'Copy the note',
  firstAskNote: NOTE,

  // Questions that a colleague can answer from knowledge.
  questionsHeading: 'Questions for a colleague',
  questionsWhat:
    'A colleague who knows the source database can answer these from knowledge, without running a query. You can copy the list and send it, and enter each answer beside its item when it comes back.',
  questionsCopy: 'Copy the questions',
  whoLabel: 'The person who answered, if you wish to record it, which Schemalyser keeps only in facts.json:',
  factYes: 'Yes, this is right',
  factNo: 'No, this is not right',
  factInstead: 'If it is not, the columns that do join:',
  factSaveNo: 'Save the answer that this is not right',
  codesLabel: 'The local codes, separated by commas:',
  codesSave: 'Save the codes',
  factUnreadable:
    'Schemalyser could not record that answer, because it names something that the catalogue does not hold or a code that cannot be accepted.',
  factRecorded: 'Schemalyser has recorded the answer and worked out the checklist again.',
  questionFirst: 'The question for a colleague:',
  queryAlternative: 'If nobody can answer the question from knowledge, the query below answers it instead.',
  groupUnneeded: (n: number) => `Show the ${n} ${items(n)} that this question does not depend on`,
  unneededWhat:
    'The answer to this question does not depend on these items, so they do not count against answering it from the source database. The OMOP release still needs them.',

  // The end of the first phase: the specification, the check of a hand-written query, and the generated query.
  auditWaiting:
    'Schemalyser will offer the specification and the audit query once the question is ready to be answered from the source database, so that nobody writes or runs the audit query on an item that is still open.',
  specHeading: 'The specification of the audit query',
  specWhat:
    'This page is for a person who writes the audit query against the source database himself. It names source tables and local codes, so it is for use inside the hospital only.',
  specCopy: 'Copy the specification',
  specSave: 'Save the specification',
  checkHeading: 'Checking a query written by hand',
  checkWhat:
    'Schemalyser can run a query written by hand on the synthetic database, with the planted cases, beside the target query, and say whether the two tables agree. Please run python -m schemalyser.target WORLD CONVERSION TARGET.sql --check-query FILE --out FOLDER from the state, which shows only the synthetic results and writes nothing of the query.',
  auditRestructured:
    "Schemalyser wrote this query from the steps of the conversion, and arranged it to start from the cohort of the question. It gives the right answer on the synthetic database, and it is offered as a reference for the person who writes the audit query. Schemalyser cannot tell how SQL Server will run it on a large database, so please show it to the database administrator before anyone runs it there.",
  generatedHeading: 'The generated query, for reference',
  generatedWarning:
    'Schemalyser could not restructure this query to start from the cohort, so it is the composition of the steps of the conversion as they are, and its header says why. It builds every row of those steps before it keeps the rows of the question, so it is not suitable to run on a large database. It is shown here as a reference for the specification.',
  saveChecks: 'Save the check results as checks.csv',
  saveStateNote:
    "The file holds catalogue.csv, checks.csv, core-profile.csv where there is one, the confirmed facts, and sql_evidence.json, which records what the team's SQL showed. If you put them in the state folder, the next project starts from them and is asked only for what is new, even without the request files.",

  // The two stages of each checklist.
  releaseHeading: 'For the later OMOP release',
  releaseWhat:
    'These items matter only when the conversion is released into the OMOP database. They do not count against answering the question from the source database.',
  auditHeading: 'The audit query',
  auditWhat:
    'This is the question itself, written as one query over the source database. Please run it once, on the source database, and keep its result with the audit.',
  auditTables: (tables: { name: string; rows: number | null }[]) =>
    `It reads ${tables.map((t) => (t.rows === null ? `${t.name}, whose size is not known` : `${t.name}, which holds about ${t.rows.toLocaleString('en-AU')} rows`)).join('; ')}.`,
  auditCost:
    'This is the one query that reads the large tables through its own joins, so how long it takes depends on the indexes of the source database. It reads each table WITH (NOLOCK), which takes no row locks but holds a schema lock while it runs, so it should not run during the nightly load.',
  auditCounts: 'It returns only counts, and no row for any one record. It leaves blank any count from 1 to 4.',
  auditRows:
    'It returns a row for each record that it finds, so its result holds patient-level data and belongs under the approval of the audit itself.',
  auditSave: 'Save the audit query',
  auditCopy: 'Copy the audit query',
  saveChecksNote:
    'The file holds the check results from the state together with every result that you have pasted. If you put it in the state folder in place of checks.csv, the next analysis begins from it.',

  // Adding requests and analysing again.
  addHeading: 'Add more requests',
  addWhat:
    'If you find existing SQL that settles an item, add its file here and analyse again. Schemalyser keeps the requests that you have already chosen and shows what the new files answer.',
  addFiles: 'Add request files:',
  addFolder: 'Add a folder of requests:',
  held: (n: number, added: number) =>
    added
      ? `Schemalyser holds ${n} request ${files(n)}, including ${added} that you have added since the previous analysis.`
      : `Schemalyser holds ${n} request ${files(n)}.`,
  reanalyse: 'Analyse again',
  restartWhat:
    'If you have more requests to add, for example by fetching again from GitHub, you can begin a new analysis. Schemalyser keeps the checklist so that it can show what the new files answer.',
  restart: 'Begin a new analysis',
  noHeaders:
    'The catalogue file has no column headers, so Schemalyser has assumed that its columns are in the order of the query in the first step.',
  unreadHeading: 'What Schemalyser could not read',
  readBeforeDownload:
    'The inventory below, with the checklists in the previous step, is everything that Schemalyser will write. Please read it before you download it.',
  namesOnly:
    'Every name in the inventory should be a table or a column from your catalogue. Where a request contained a value, such as a date or a record number, Schemalyser has written a placeholder such as <string> or <number> in its place.',
  doNotDownload: 'If you see anything that should not leave your team, please clear the inventory and do not download it.',
  showExactly: 'Show this file exactly as Schemalyser will write it',
  indexNote:
    'Schemalyser refers to each request by a number. This list shows which file each number refers to, and Schemalyser does not include the list in the inventory.',
  checksNoHeaders:
    'The check results file has no column headers, so Schemalyser has assumed that its columns are in the order of the check script.',
  checkScriptWhat:
    'The checklist gives, beside each item, the short queries that it needs. If your team prefers to run every check in one go, the whole check script asks Clarity what the requests and the conversion leave open: how large each table is, which joined columns hold a different value in every row, which values the filtered columns hold, how far apart paired dates fall, and how many rows share each value of a joined key.',
  checkScriptSafe:
    'The script reads Clarity and changes nothing in it. It rounds every count down to the nearest ten, and it lists a value only when at least ten rows hold it.',
  checkScriptHow:
    'Please run the script in SQL Server Management Studio and save the results as a CSV file. You can then paste the results into the checklist, or begin again from the third step and choose that file as well.',
  downloadCheckScript: 'Download the check script',
  usesChecks: 'The synthetic database uses the values and the table sizes from your check results.',
  runsChosenRequests: 'Schemalyser will run the requests that you chose in the third step.',
  openSandbox: 'If you have only an inventory file, you can open the sandbox on its own page.',
  openFirstPage: 'To make an inventory from your requests, you can open the first page.',
  download: 'Download the inventory and the checklists',
  downloadHolds:
    'The download holds the inventory, and a folder named boundary that holds the checklists, their readiness statements, the register of open questions, summary.md and provenance.json, exactly as the boundary command writes them.',
  showSummary: 'Show summary.md exactly as Schemalyser will write it',
  clear: 'Clear everything',

  reconnected:
    'This page has gone back online. Schemalyser has stopped its analysis engine and discarded the contents of the requests. You can still download the inventory, or you can clear it. To carry on, choose Begin a new analysis, wait for Schemalyser to load again, take the page offline again and choose the files again, with the state that you saved if you saved one.',
  reconnectedNoInventory:
    'This page has gone back online. Schemalyser has stopped its analysis engine and discarded the contents of the requests. Schemalyser had not finished the inventory, so there is nothing to download. To carry on, reload the page while it is online, take it offline again and choose the files again.',

  safeguardsHeading: 'What Schemalyser does with your files',
  safeguards: [
    'Schemalyser reads the files on this computer. It does not send them, or anything taken from them, to any other computer.',
    'Schemalyser will not accept files while this page is online, and it stops if the page goes back online.',
    'Schemalyser writes only names that it finds in your catalogue and your conversion, together with counts. It does not write comments, values, aliases or the names of request files.',
    'Schemalyser shows you everything it has written before you download it.',
  ],
  checkYourself:
    "You can confirm that Schemalyser sends nothing by opening your browser's developer tools and watching the Network panel while it works.",

  keepsNothing: 'Schemalyser keeps nothing after you close this page.',
  version: (version: string, checksum: string) => `Version ${version}. Checksum: ${checksum}`,
};

// The six files of the inventory, in the order the page shows them.
export const packFiles = [
  {
    file: 'elements.csv',
    title: 'Columns in use',
    explanation:
      'This file lists each column that the requests use. The first count is the number of requests that use the column. The counts that follow are the numbers of requests that select it, filter on it, join on it, group by it and compute something from it.',
  },
  {
    file: 'joins.csv',
    title: 'Joins',
    explanation:
      "This file lists each pair of columns that the requests join, the kind of join, and the number of requests that make it. In a left, right or full join, the table being joined is on the right. A join marked 'where' is made in a WHERE clause or a correlated subquery.",
  },
  {
    file: 'filters.csv',
    title: 'Filters',
    explanation:
      'This file lists each column that the requests filter on, the comparison they use and the kind of value they compare it with. Schemalyser does not write the values themselves.',
  },
  {
    file: 'derivations.csv',
    title: 'Computed expressions',
    explanation:
      "This file lists the expressions that the requests compute from columns. Schemalyser has rewritten each one with the catalogue's names and with a placeholder in place of every value.",
  },
  {
    file: 'comparisons.csv',
    title: 'Comparisons between columns',
    explanation:
      'This file lists each pair of columns that the requests compare as earlier and later, or smaller and larger, and the number of requests that do so.',
  },
  {
    file: 'requests.csv',
    title: 'Requests',
    explanation:
      'This file lists each request by number, with the number of statements it contains, the number of parts that Schemalyser could not read and the columns it uses. Schemalyser does not write the names of the files.',
  },
  {
    file: 'checks.csv',
    title: 'Check results',
    explanation:
      'This file holds the check results that Schemalyser accepted: the size of each table, a description of each joined column, and the values that each filtered column holds.',
  },
  {
    file: 'coverage.txt',
    title: 'Coverage',
    explanation: 'This file records how much of the requests Schemalyser was able to read.',
  },
];

// The catalogue query shown in the first step. It reads metadata only.
export const catalogueQuery = `SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, ORDINAL_POSITION,
       DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, NUMERIC_PRECISION,
       NUMERIC_SCALE, IS_NULLABLE
FROM INFORMATION_SCHEMA.COLUMNS
ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION;`;

// ---------------------------------------------------------------------------------------------
// The wording of the GitHub section, as approved on 4 October 2026. Change it only with approval.
// ---------------------------------------------------------------------------------------------
export const githubStrings = {
  heading: 'Fetch the files from GitHub',
  intro:
    'Schemalyser can fetch its files from your private repositories on GitHub, in place of files chosen from this computer. Schemalyser fetches them while the browser is online, and it analyses nothing until the browser is offline.',
  requestsRepository: "The repository that holds the data team's requests, written as owner/name:",
  stateRepository:
    "The repository that holds Schemalyser's own files, such as the catalogue and the site rules, written as owner/name:",
  requestsRef: 'The branch, tag or commit to fetch from the requests repository:',
  stateRef: "The branch, tag or commit to fetch from Schemalyser's repository:",
  refNote: "If you leave this empty, Schemalyser fetches from the repository's default branch.",
  token: 'A fine-grained access token that can read the contents of these repositories and nothing else:',
  tokenNote:
    'Schemalyser keeps the token in memory only, sends it to GitHub and nowhere else, and discards it when the files have been fetched.',
  fetch: 'Fetch the files',
  progress: 'Schemalyser is fetching the files from GitHub.',
  fetched: (count: number, repository: string, commit: string) =>
    `Schemalyser has fetched ${count} ${files(count)} from ${repository} at commit ${commit}.`,
  closed: 'Schemalyser has closed its connection to GitHub. Take this page offline to continue.',
  skipped: (count: number) => `Schemalyser left out ${count} ${files(count)} that are larger than 2 MB.`,
  tooMany:
    'Schemalyser stopped at 5,000 files, and the repository holds more. If the requests are in one folder, fetch from a repository that holds only that folder.',
  truncated:
    'GitHub returned only part of the list of files, because the repository is very large. Schemalyser has fetched what GitHub listed.',
  unauthorised: (repository: string) =>
    `GitHub did not accept the token for ${repository}. If the token has expired or cannot read this repository, create a new token and fetch again.`,
  notFound: (repository: string, ref: string) =>
    `GitHub could not find ${repository} at ${ref}. If the name or the branch is different, correct it and fetch again.`,
  offline: 'Schemalyser could not reach GitHub. If the page is offline, bring it back online and fetch again.',
  provenance: (repository: string, commit: string) => `These results come from ${repository} at commit ${commit}.`,

  // Written for this feature beyond the first list of strings, and approved with it.
  defaultBranch: 'its default branch',
  rateLimited:
    'GitHub has limited the number of requests that this token can make. If you wait for up to an hour, you can fetch again.',
  otherError: (repository: string) =>
    `GitHub has not been able to provide the files from ${repository}. If the problem continues, you can try again later.`,
  popupBlocked:
    'The browser did not open the small window that Schemalyser uses to fetch from GitHub. If the browser has blocked a pop-up window, allow pop-ups for this page and fetch again.',
  windowClosed: 'The window that fetches from GitHub closed before it had finished. You can fetch again.',
  policyBefore:
    'Schemalyser cannot confirm that this browser limits the fetch to GitHub, so it has not fetched anything. Please use Chrome, Edge or Firefox.',
  policyAfter:
    'Schemalyser cannot confirm that its connection to GitHub has closed, so it has discarded the files it fetched. Please use Chrome, Edge or Firefox.',
};
