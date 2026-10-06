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
    "Schemalyser reads your team's SQL files on this computer. For each audit question, it shows what still needs to be settled and which of your existing SQL files would settle it, and it builds an inventory, which is a list of the tables and columns that the files use.",

  steps: [
    'Before you start',
    'Take this page offline',
    'Choose the files',
    'Work through the checklist together',
    'What Schemalyser found in the SQL files',
    'Download the results',
    'Try the SQL files in a practice database',
  ],
  catalogueWhy: "Schemalyser helps a clinician and a colleague who writes SQL to work out, together, how to answer an audit question from the hospital's database. The clinician brings a folder that describes the audit, and the colleague brings the team's existing SQL files. Schemalyser reads both on this computer, and it asks only questions that the colleague can answer and short queries that return names or rounded counts.",
  querySafe: "The query reads only SQL Server's own list of tables and columns. It does not read any patient table.",
  copyQuery: 'Copy the query',
  skipCatalogue: "Most people can skip this, because in the third step Schemalyser writes a shorter query of only the tables that the audit needs.",
  connected: 'This page is online.',
  isOffline: 'This page is offline.',

  loading:
    'Schemalyser is loading its analysis engine. Please keep the page online until loading has finished.',
  loadFailed:
    'Schemalyser has not been able to load its analysis engine. If the page is online, you can reload it to try again.',
  policyFailed:
    'Schemalyser cannot confirm that this browser will keep your files on this computer, so it has not started. Please use Chrome, Edge or Firefox.',
  loaded: "Schemalyser has finished loading. Please take this page offline now, so that you can see for yourself that nothing can leave it. Your SQL window can stay connected.",
  policyHeld: "When it loaded, Schemalyser asked the browser to reach two outside addresses, to make sure that the browser refuses them. The browser refused both, as it should, so the page cannot send anything anywhere. The browser's console shows the two refusals as messages about its Content Security Policy. Had either not been refused, Schemalyser would have stopped and said so.",
  offlineHow: [
    'In Chrome or Edge, press F12 to open the developer tools, choose the Network panel, open the menu that reads No throttling, and choose Offline. Only this tab goes offline. Leave the developer tools open while you use the page, because the tab goes back online when they close.',
    'In Firefox, open the File menu and choose Work Offline. If you cannot see the menu bar, press the Alt key. Firefox then takes all of its own tabs offline, but the rest of the computer, including your SQL window, stays connected.',
    'If the developer tools are not available on this computer, use Firefox and Work Offline, or disconnect the computer from the network while the page holds your files and reconnect it to run each query.',
    'The page must stay offline for as long as you use it. If it goes back online while it holds your files, Schemalyser locks the page and discards what it has read. To carry on, choose Begin a new analysis, wait for the page to load again, take it offline again and choose the files again; if you saved the state, nothing that you answered is lost.',
  ],
  noFilesWhileConnected: 'Schemalyser will not accept any files while this page is online.',
  exampleHeading: 'Try the invented example',
  exampleWhat:
    'If you would like to see the whole checklist working before you bring any files, Schemalyser can load an invented example: a folder for an audit with four audit questions, and fifteen SQL files of the kind that a team keeps, all made up for testing. None of it comes from a hospital. Load it while the page is online, then take the page offline and analyse it in the next step.',
  exampleLoad: 'Load the invented example',
  exampleLoading: 'Schemalyser is loading the invented example.',
  exampleLoaded: (state: number, requests: number) =>
    `Schemalyser has loaded the invented example, which holds ${state} ${files(state)} for the audit's folder and ${requests} SQL ${files(requests)}. Take the page offline, then choose Analyse the requests in the next step.`,
  exampleFailed:
    'Schemalyser could not load the invented example. If the page is online, you can try again.',
  exampleChosen:
    'The invented example is loaded in place of your own files. Everything in it is made up, and none of it comes from a hospital. If you choose any file of your own, Schemalyser discards the example and anything worked out from it, and starts clean.',
  exampleBanner:
    'This checklist comes from the invented example. Its tables, codes, requests and results are made up, and none of them comes from a hospital.',

  offline: 'This page is offline. You can now choose the files.',
  chooseState: "The clinician's folder for this audit:",
  stateNote: "The clinician brings this folder. It describes the audit: the audit question, how the hospital's tables are read to answer it, and the answers saved from earlier meetings. It may also hold a list of tables and columns.",
  stateFound: (catalogue: boolean, conversion: number, targets: number) =>
    `The folder you chose describes ${targets ? `${targets} audit ${targets === 1 ? 'question' : 'questions'}` : 'no audit question'}${
      conversion ? '' : ', without the steps that read the tables'
    }${catalogue ? ', and it holds a list of tables and columns' : ''}.`,
  keptForComparison:
    'Schemalyser has kept the checklist from the previous analysis, and it will show what the next analysis answers.',
  chooseCatalogue: "The list of tables and columns, if you already have it as a CSV file:",
  catalogueNote: "This is the CSV file that the query in the first step produces. Without it, Schemalyser writes a shorter query for you below.",
  chooseRules: 'Choose the site rules file, if you have one:',
  rulesNote: 'The site rules file tells Schemalyser how this hospital names its tables and codes. Schemalyser works without one.',
  chooseFolder: 'The SQL files that you already have for this database, your own or your team\'s:',
  folderNote: 'Choose the folder that holds them. Schemalyser reads them here to see how you already join and filter these tables, and it keeps no text from them.',
  folderCount: (n: number) => `The folder you chose contains ${n} SQL ${files(n)}.`,
  chooseChecks: 'Earlier query results saved by Schemalyser, if you have them:',
  checksNote: 'You need this only if an earlier meeting saved its query results as a separate file. The file saved at the end of an earlier meeting already holds them.',
  checksError:
    'Schemalyser could not read the file of query results. Please check that you have chosen checks.csv from an earlier meeting, or the CSV file of results from the check script.',
  analyse: 'Analyse the requests',
  catalogueError:
    'Schemalyser could not read the list of tables and columns. The file needs the columns TABLE_NAME, COLUMN_NAME and DATA_TYPE. Please check that you have chosen the CSV file that the query in the first step produces.',
  analysisFailed: 'Schemalyser has not been able to finish reading the requests. You can choose the files and try again.',

  progress: (done: number, total: number) =>
    `Schemalyser is reading the requests. It has read ${done} of ${total} ${files(total)}.`,
  boundaryProgress: 'Schemalyser has read the requests and is now working out the checklist for each target query.',

  // The checklist.
  checklistIntro: "For each audit question, Schemalyser shows what it still needs, with a question for you or a short query beside each. Schemalyser sets each mark itself once an item is settled. The reporting database is the copy of the hospital's records that your SQL window reads, and the team that looks after it, usually the hospital's data or reporting team, can grant access and answer questions about its tables.",
  noChecklist:
    "To see a checklist for each audit question, choose the clinician's folder for this audit, or fetch Schemalyser's repository from GitHub. The folder must hold a folder named conversion, with the steps that read the hospital's tables, and a folder named targets, with the audit questions.",
  boundaryProblems: {
    bad_rules:
      'Schemalyser could not read site-rules.json, so it has not made the checklists. If the file is meant to hold the site rules, check that it is valid JSON and uses only the known keys.',
    bad_options:
      'Schemalyser could not read boundary.json, so it has not made the checklists. The file may set only includeSpans and includeFanout, each to true or false.',
    bad_checks:
      'Schemalyser could not read checks.csv as query results, so it has not made the checklists.',
    bad_conversion:
      "Schemalyser could not read the folder named conversion, which holds the steps that read the hospital's tables, so it has not made the checklists. Please check that conversion.json lists each step and that each step's file is present.",
    bad_profile:
      'Schemalyser could not read core-profile.csv as the results of the queries for the central OMOP team, so it has not made the checklists.',
    bad_evidence:
      'Schemalyser could not read sql_evidence.json, because a finding in it names something that the list of tables and columns does not hold or does not have the expected form, so it has not made the checklists.',
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
  groupSql: 'Points that the team\'s existing SQL could settle later',
  groupSqlNote: 'Nothing is needed from you for these in the meeting. If one of your team\'s SQL files does what an item describes, add the file below and analyse again.',
  groupSqlNone: 'No point for this audit question waits for the team\'s SQL.',
  groupOther: 'Points for the clinician or the central OMOP team after the meeting',
  groupOtherNote: 'Nothing is needed from you for these in the meeting. Each says who settles it and how.',
  groupAnswered: (n: number) => `Show the ${n} answered ${items(n)}`,
  groupGiven: 'The answers that you have given',
  groupGivenNote: 'Each answer that you have given stands here beside the button that changes it. If an answer turns out to be wrong, change it, and Schemalyser asks the question again.',
  blocking: 'The audit query must rest on this.',
  notBlocking: 'This makes the answer more certain, and the audit query does not wait for it.',
  statusNames: { answered: 'Answered.', partly: 'Partly answered.', open: 'Open.' } as Record<string, string>,
  readinessInFull: "Show the developer's detail",

  // The plain queries on the checklist, and the results pasted back.
  sizesHeading: 'Table sizes',
  queryStates: {
    ready: 'This query is ready to run.',
    waiting: 'The query for this item appears once the result of the table sizes query, at the head of this checklist, has been pasted.',
    large: 'Schemalyser offers no query for this item, because one of the tables that it would read is too large to count without slowing the database for everyone else.',
    ran: 'The query for this item has run.',
  } as Record<string, string>,
  queryShownEarlier: 'The query shown with an earlier item answers this item as well.',
  pasteHeading: 'Paste the results of the queries',
  pasteWhat: 'Run a query from the checklist below in your SQL window, copy its results grid with the headers, and paste it here. In SQL Server Management Studio, click the empty square at the top left of the results grid to select all of it, then right-click and choose Copy with Headers. You can paste the results of several queries at once. Schemalyser adds them to what it holds and works out the checklist again.',
  pasteLabel: 'The results, copied from the results grid or from a CSV file:',
  readPaste: 'Read the pasted results',
  pasteReading: 'Schemalyser is reading the pasted results and working out the checklist again.',
  pasted: (read: number, accepted: number) =>
    accepted === 0
      ? `Schemalyser has not kept any of the ${read} pasted ${rows(read)}, because none of them is a row that a query from this page could have returned.`
      : accepted === read
        ? `Schemalyser has read the ${read} pasted ${rows(read)}.`
        : `Schemalyser has kept ${accepted} of the ${read} pasted rows, and left out the others, because a query from this page could not have returned them.`,
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
  profileHeading: 'For the central OMOP team, the team that runs the hospital\'s main OMOP database',
  profileWhat:
    "These queries run on the OMOP database, not on the reporting database. The first reads only SQL Server's own records of the tables, and the others wait for its result, because Schemalyser offers no query on a table whose size it does not know.",
  queryInProfile: 'The query for this item is in the section for the central OMOP team, above.',
  profilePasteHeading: 'Paste the results of the queries for the central OMOP team',
  profilePasteWhat:
    'When the central OMOP team returns the results of its queries, paste them here, as copied from the results grid or from a CSV file. Schemalyser adds them to what it already knows about the OMOP database, which it calls the core profile, and works out the checklist again.',
  profilePasteLabel: 'The results of the queries for the central OMOP team:',
  readProfilePaste: 'Read the pasted results for the OMOP database',
  profilePasteUnreadable:
    'Schemalyser could not read the pasted text as the results of a query for the central OMOP team. Each row needs the six columns that the query returns, from ITEM_CATEGORY to VALUE_05.',
  saveProfile: 'Save the core profile as core-profile.csv',
  saveProfileNote:
    'The file holds what the folder for this audit already recorded about the OMOP database, together with every result that you have pasted. If you put it in the folder for this audit in place of core-profile.csv, the next analysis begins from it.',
  // The first ask, for a project that starts without a catalogue.
  firstHeading: 'Start without a list of tables and columns',
  firstWhat: "Without a list of tables and columns, Schemalyser writes one short query that asks SQL Server which columns the tables of the audit and of your SQL files have, and how large each table is. Choose the folders above, then write the query. Run it in your SQL window, then click the empty square at the top left of the results grid to select all of it, right-click and choose Copy with Headers, and paste the result below. The query holds the table names from your files, so Schemalyser shows it only here and writes it into no file.",
  firstWrite: 'Write the first query',
  firstNames: (n: number, leftOut: number) =>
    leftOut
      ? `The query asks about ${n} tables, which are those that the most files read. Schemalyser left out ${leftOut} more, and you can ask about them later, starting from the list of tables and columns that this meeting saves.`
      : `The query asks about the ${n} ${n === 1 ? 'table' : 'tables'} that your files read.`,
  firstNone: 'Schemalyser found no table name in the files that it could use, so it has not written a query.',
  firstCopy: 'Copy the query',
  firstPasteLabel: 'The result of the first query, copied from the results grid with its headers:',
  firstRead: 'Read the result',
  firstReadDone: (tables: number, columns: number, sized: number) =>
    `Schemalyser has read ${columns} columns of ${tables} tables.${
      sized === 0
        ? ' This login cannot see the sizes of the tables, so Schemalyser will offer no query on a table that could be large, and will ask you instead.'
        : sized < tables
          ? ` SQL Server gave no size for ${tables - sized} of them, as happens for a view, which is a saved query that SQL Server presents as a table, so Schemalyser will offer no query on those that could be large.`
          : ''
    } A table or column that the audit needs and that did not come back is not visible to this login: it may not exist here, or this login may not be allowed to see it, and Schemalyser will ask you which. You can now analyse the files.`,
  firstUnreadable:
    'Schemalyser could not read the pasted text as the result of the first query. Each row needs the ten columns that the query returns, from TABLE_SCHEMA to TABLE_ROWS.',
  saveState: 'Save what has been settled today',
  // The note to send with the first query, as docs/first-ask-note.md gives it.
  noteHeading: 'Show the note to send with the first query',
  noteCopy: 'Copy the note',
  firstAskNote: NOTE,

  // Questions that a colleague can answer from knowledge.
  questionsHeading: 'Questions for a colleague',
  questionsWhat: 'These are all the questions for you on one list, which you can copy. Answer each one beside its item below.',
  questionsCopy: 'Copy the questions',
  whoLabel: "Your name, if you would like it kept with your answers (it is kept only in the file that you save at the end):",
  factYes: 'Yes, this is right',
  factNo: "No",
  factInstead: "If you know the columns that do match, choose them here, then save the answer:",
  factSaveNo: 'Save the answer that this is not right',
  codesLabel: 'The local codes, separated by commas:',
  codesSave: 'Save the codes',
  factUnreadable:
    'Schemalyser could not record that answer, because it names a table or column that is not in the list of tables and columns, or a code that cannot be accepted.',
  factRecorded: 'Schemalyser has recorded the answer and worked out the checklist again.',
  questionFirst: 'The question for you:',
  queryAlternative: 'If neither of you knows the answer, run the query below, which answers the question instead.',
  groupUnneeded: (n: number) => `Show the ${n} ${items(n)} that this question does not depend on`,
  unneededWhat:
    'The answer to this question does not depend on these items, so they do not count against answering it from the reporting database. They will be needed later, when the audit\'s steps are copied into the hospital\'s OMOP database.',

  // The end of the first phase: the specification, the check of a hand-written query, and the generated query.
  auditWaiting: "Schemalyser will offer the reference query once nothing above remains to be settled.",
  specHeading: 'The specification of the audit query',
  specWhat: "This page is for the person who writes the audit query. It names this hospital's tables and codes, so it is for use inside the hospital only.",
  specCopy: 'Copy the specification',
  specSave: 'Save the specification',
  checkHeading: 'Checking a query written by hand',
  checkWhat:
    'Schemalyser can run a query written by hand on the synthetic database, with the planted cases, beside the target query, and say whether the two tables agree. Please run python -m schemalyser.target WORLD CONVERSION TARGET.sql --check-query FILE --out FOLDER from the state, which shows only the synthetic results and writes nothing of the query.',
  auditRestructured: "Schemalyser wrote this query from the steps that read the tables, arranged to start from the children of the question. It gives the right answer on the practice database. It is a reference for the person who writes the audit query.",
  generatedHeading: 'The generated query, for reference',
  generatedWarning:
    'Schemalyser could not rearrange this query to start from the audit\'s own anaesthetics, so it is simply every step that reads the tables, one after another, and its header says why. It builds every row of those steps before it keeps the rows of the question, so it must not be run on a large database. It is shown here as a reference for the specification.',
  saveChecks: 'Save the query results as checks.csv',
  saveStateNote: "The file holds everything that you have settled today: the list of tables and columns, the results that you pasted, your answers and the codes that you chose. Please keep it in the clinician's folder for this audit, on the hospital's network, and choose it as that folder at the next meeting, so that nothing is asked twice.",
  // The two stages of each checklist.
  releaseHeading: 'For the later OMOP release',
  releaseWhat:
    "These items matter only later, when the audit's steps are copied into the hospital's OMOP database, which holds the hospital's records in OMOP, a standard layout for research databases; this page calls that database the core. They do not count against answering the question from the reporting database.",
  auditHeading: 'The audit query',
  auditWhat: "This is the question itself, written as one query over the hospital's tables, as a reference for the person who writes the audit query. Before any query like it is run on a large database, choose a study period, run it on the reporting copy rather than the live system, and run it out of hours.",
  auditTables: (tables: { name: string; rows: number | null }[]) =>
    `It reads ${tables.map((t) => (t.rows === null ? `${t.name}, whose size is not known` : `${t.name}, which holds about ${t.rows.toLocaleString('en-AU')} rows`)).join('; ')}.`,
  auditCost:
    'This is the one query that reads the large tables through its own joins, so how long it takes depends on how the reporting database is set up, and cannot be known in advance. It reads each table WITH (NOLOCK), which means that it neither waits for nor holds up other people\'s work on the same rows. SQL Server still stops anyone changing the design of those tables while it runs, so it must not run during the nightly load, when the reporting database is refreshed.',
  auditCounts: 'It returns only counts, and no row for any one record. It leaves blank any count from 1 to 4.',
  auditRows:
    'It returns a row for each record that it finds, so its result holds patient-level data and belongs under the approval of the audit itself.',
  auditSave: 'Save the audit query',
  auditCopy: 'Copy the audit query',
  saveChecksNote:
    'The file holds the query results that the folder for this audit already held, together with every result that you have pasted. If you put it in the folder for this audit in place of checks.csv, the next analysis begins from it.',

  // Adding requests and analysing again.
  addHeading: 'Add more of your team\'s SQL files',
  addWhat: 'If you find one of your team\'s SQL files that does what an item describes, add it here and analyse again. Schemalyser keeps the files that you have already chosen.',
  addFiles: 'Add SQL files:',
  addFolder: 'Add a folder of SQL files:',
  held: (n: number, added: number) =>
    added
      ? `Schemalyser holds ${n} of your team's SQL ${files(n)}, including ${added} that you have added since the previous analysis.`
      : `Schemalyser holds ${n} of your team's SQL ${files(n)}.`,
  reanalyse: 'Analyse again',
  restartWhat:
    'If you have more SQL files to add, for example by fetching again from GitHub, you can begin a new analysis. Schemalyser keeps the checklist so that it can show what the new files answer.',
  restart: 'Begin a new analysis',
  noHeaders:
    'The list of tables and columns has no column headers, so Schemalyser has assumed that its columns are in the order of the query in the first step.',
  unreadHeading: 'What Schemalyser could not read',
  readBeforeDownload:
    'The inventory below, which lists the tables, columns and joins that your SQL files use, is everything that Schemalyser will write, together with the checklists in the previous step. Please read it before you download it.',
  namesOnly:
    'Every name in the inventory should be a table or a column from your list of tables and columns. Where a SQL file contained a value, such as a date or a record number, Schemalyser has written a placeholder such as <string> or <number> in its place.',
  doNotDownload: 'If you see anything that should not leave your team, please clear the inventory and do not download it.',
  showExactly: 'Show this file exactly as Schemalyser will write it',
  indexNote:
    'Schemalyser refers to each request by a number. This list shows which file each number refers to, and Schemalyser does not include the list in the inventory.',
  checksNoHeaders:
    'The file of query results has no column headers, so Schemalyser has assumed that its columns are in the order in which the check script writes them.',
  checkScriptWhat:
    'The checklist gives, beside each item, the short queries that it needs. If your team prefers to run every check in one go, the check script, which is one long script of the same kind of short queries, asks the reporting database what the SQL files and the audit\'s steps leave open: how large each table is, which columns used in joins hold a different value in every row, which values the filtered columns hold, how far apart pairs of dates fall, and how many rows share each value of a column used in joins.',
  checkScriptSafe:
    'The script reads the reporting database and changes nothing in it. It rounds every count down to the nearest ten, and it lists a value only when at least ten rows hold it.',
  checkScriptHow:
    'Please run the script in SQL Server Management Studio and save the results as a CSV file. You can then paste the results into the checklist, or begin again from the third step and choose that file as well.',
  downloadCheckScript: 'Download the check script',
  usesChecks: 'The practice database uses the values and the table sizes from your query results.',
  runsChosenRequests: 'Schemalyser will run the requests that you chose in the third step.',
  openSandbox: 'If you have only an inventory file, you can open the sandbox on its own page.',
  openFirstPage: 'To make an inventory from your requests, you can open the first page.',
  download: 'Download the inventory and the checklists',
  downloadHolds:
    'The download holds the inventory, and a folder named boundary that holds the checklists, Schemalyser\'s account of how ready each is, the list of open questions, a summary (summary.md) and a record of which files were read (provenance.json), exactly as Schemalyser\'s command-line tool writes them.',
  showSummary: 'Show summary.md exactly as Schemalyser will write it',
  clear: 'Clear everything',

  reconnected:
    'This page has gone back online. Schemalyser has stopped its analysis engine and discarded the contents of the SQL files. You can still download the inventory, or you can clear it. To carry on, choose Begin a new analysis, wait for Schemalyser to load again, take the page offline again and choose the files again, with the state that you saved if you saved one.',
  reconnectedNoInventory:
    'This page has gone back online. Schemalyser has stopped its analysis engine and discarded the contents of the SQL files. Schemalyser had not finished the inventory, so there is nothing to download. To carry on, reload the page while it is online, take it offline again and choose the files again.',

  safeguardsHeading: 'What Schemalyser does with your files',
  safeguards: [
    'Schemalyser reads the files on this computer. It does not send them, or anything taken from them, to any other computer.',
    'Schemalyser will not accept files while this page is online, and it stops if the page goes back online.',
    'Schemalyser writes only names that it finds in your list of tables and columns and in the audit\'s steps, together with counts. It does not write comments, values, the short names that a query gives its tables, or the names of your SQL files.',
    'Schemalyser shows you everything it has written before you download it.',
  ],
  checkYourself:
    "You can confirm that Schemalyser sends nothing by opening your browser's developer tools and watching the Network panel while it works.",

  offlineHowSummary: 'How to take this page offline',
  policySummary: 'How this page checks that nothing can leave it',
  githubSummary: 'Fetch the files from GitHub instead (not needed in a meeting)',
  catalogueSummary: 'If your team already keeps the full list of tables and columns',
  // The tally at the head of a checklist, by who can settle each point, as the list at the end groups them.
  needs: (you: number, team: number, clinician: number) => {
    const total = you + team + clinician;
    if (!total) return 'Schemalyser needs nothing more for this audit question.';
    const parts = [
      you ? `${you} for you now` : '',
      team ? `${team} for the team that looks after the reporting database` : '',
      clinician ? `${clinician} for the clinician after the meeting` : '',
    ].filter(Boolean);
    const list = parts.length > 1 ? `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}` : parts[0];
    return `Schemalyser needs ${total} more ${total === 1 ? 'thing' : 'things'} for this audit question: ${list}.`;
  },
  readyNow: (lessCertain: number) =>
    `Everything that the audit query must rest on is settled, so the query can be written now.${
      lessCertain ? ` ${lessCertain === 1 ? 'One further point is' : `${lessCertain} further points are`} less certain; the query does not wait for ${lessCertain === 1 ? 'it' : 'them'}, and the specification lists ${lessCertain === 1 ? 'it' : 'them'}.` : ''
    }`,
  notReadyYet: 'Some of what the audit query must rest on is not yet settled. The list at the end says what remains and who can settle it.',
  notSure: 'Not sure',
  routeAbsent: 'It does not exist here',
  routeHidden: 'It exists, but I cannot see it',
  searchAbove: 'The name search with the item above also finds these codes.',
  searchPasteLabel: 'The result of the search, copied from the results grid with its headers:',
  searchRead: 'Show the names',
  searchPrivate: "The names that the search returns are the hospital's own. Schemalyser shows them only on this page, and keeps only the codes that you choose, in the file that you save at the end.",
  searchNone: 'Schemalyser could not read any code and name in the pasted text. Each row needs the two columns that the search returns, code and name.',
  searchName: 'Name',
  searchCode: 'Code',
  searchChoice: 'What it is',
  searchNeither: 'Neither',
  searchSave: 'Save the choices',
  searchTooMany: (n: number, shown: number) =>
    `The search returned ${n.toLocaleString('en-AU')} rows, which is more than this page can sensibly list, so Schemalyser shows only the first ${shown} below. Narrow the words above, write the search again, and run it once more, so that every row it returns can be seen and chosen.`,
  // Each query that reaches the table of readings is a short script that starts from a temporary table of the cohort.
  scriptTemporary: "The script works in two parts. Part 1 makes one temporary table, called #cohort, which is a small table that exists only in your own SQL window and disappears when you close that window. #cohort holds only the identifying numbers of the audit's anaesthetics and of their records. Part 2 then fetches the readings of those anaesthetics and no others. The script creates or changes nothing else.",
  scriptTimeout: 'Before you run anything here, set a time limit, so that a query that runs too long stops by itself. In SQL Server Management Studio, with your query window open, open the Query menu, choose Query Options, then Execution, and enter a number of seconds, such as 300, in Execution time-out; Management Studio sets no limit unless you enter one. Then select part 1 of the script, from its first line down to the line that begins -- Part 2, and run only that selection, because SQL Server can show the plan of part 2 only once #cohort exists.',
  scriptPlan: 'Next, select part 2 and press Ctrl+L. This shows the estimated plan, which is SQL Server\'s description of how it intends to run the query, drawn as boxes joined by arrows, and it runs nothing. Find each box that names the table of readings, which is the very large table that holds every value charted during an anaesthetic. If that box reads Table Scan, Index Scan or Clustered Index Scan, SQL Server would read the whole table, which could take hours and slow the database for everyone; if a box that reads Hash Match has an arrow coming in from it, the same is true. In either case, do not run part 2: right-click the plan, choose Save Execution Plan As, and give the file to the clinician leading the audit, who will take it to the team that looks after the reporting database. If the table of readings appears only in boxes that read Index Seek or Clustered Index Seek, which means that SQL Server goes straight to the rows it needs, you can run part 2.',
  scriptCostly: (table: string, rows: number) =>
    `Part 1, like the count by year, reads ${table} (about ${rows.toLocaleString('en-AU')} rows) to find the anaesthetics, so it can be slow on its own. If part 1 reaches the time limit, it stops by itself; in that case, do not run it again, and tell the clinician leading the audit.`,
  scriptMonth: 'Where the period is longer than a month, run the script first for one month: change the two dates in part 1 to the first month of the period, and run the whole period only once that month has finished quickly.',
  scriptWorst: (worst: string) => `The count by year shows ${worst}, and the script asks only for the readings of those anaesthetics.`,
  countCostly: (table: string, rows: number) =>
    `The count by year reads ${table} (about ${rows.toLocaleString('en-AU')} rows) to find the anaesthetics, so it can be slow. If it reaches the time limit, it stops by itself; in that case, do not run it again, and tell the clinician leading the audit.`,
  countPlan: 'This count does not read the table of readings, which is the very large table that holds every value charted during an anaesthetic. Before you run it, set a time limit: in SQL Server Management Studio, with your query window open, open the Query menu, choose Query Options, then Execution, and enter a number of seconds, such as 300, in Execution time-out. Then select the query and press Ctrl+L, which shows its estimated plan, SQL Server\'s description of how it intends to run the query, without running it. If any box in the plan names one of the tables of readings, do not run the count, and show the plan to the clinician leading the audit.',
  auditReferenceOnly: 'This query is shown as a reference for the person who writes the audit query, and it must not be run as it stands on the hospital\'s databases.',
  listedHeading: 'What is charted on the audit\'s anaesthetics',
  listedWhat: (column: string, year: number) =>
    `This script lists every code of ${column} that was charted on the audit's anaesthetics that started in ${year}, with how often each was charted and its names, most charted first. The notes below say how to run it safely, and the comment lines at its top say which tables it reads. Run it, paste the result below, and mark the rows that matter. A row that you leave unmarked is simply not chosen. A code that the hospital stopped using appears only in the years in which it was used, so work through the study period a year at a time: choose each year in turn, run the list again and mark it. Schemalyser remembers what you have marked in each year, and saving records the marks of every year.`,
  listedYear: 'The year to list:',
  listedPasteLabel: 'The result of the list, copied from the results grid with its headers:',
  listedRead: 'Show the list',
  listedNone: 'Schemalyser could not read any code in the pasted text. Each row needs the code, the two counts and the names that the list returns.',
  listedFilter: 'Type to show only the rows that hold these words',
  listedReadings: 'Readings',
  listedAnaesthetics: 'Anaesthetics',
  listedNotChosen: 'Not chosen',
  listedCalculated: 'A mean calculated from systolic and diastolic, which the audit leaves out',
  listedLikely: '(likely)',
  listedFound: (n: number, likely: number) =>
    `The list holds ${n.toLocaleString('en-AU')} ${n === 1 ? 'code' : 'codes'}, and ${likely} of them ${likely === 1 ? 'has' : 'have'} a name that holds a word for the meanings sought, marked as likely. Mark each row that matters, then save the choices.`,
  listedKept: (n: number, year: number) =>
    n ? `Schemalyser has kept the choices from the list for ${year}, which held ${n.toLocaleString('en-AU')} ${n === 1 ? 'code' : 'codes'}.`
      : `The list for ${year} came back empty. The list of what remains says what to check first.`,
  listedAfterCount: 'Once you have seen the count by year, Schemalyser offers a list of the codes charted on the audit\'s anaesthetics in one year, with their counts and names, and the codes for this meaning are chosen from it.',
  listedInstead: 'The codes for this meaning are chosen from the list of what is charted on the audit\'s anaesthetics, further down this section.',
  chartedHeading: 'How often each chosen code is charted',
  chartedWhat: (from: string, to: string) =>
    `This script is optional. It counts, for the chosen codes only, how often each was charted on the audit's anaesthetics that started from ${from} to ${to}, the last year of the study period, and on how many of those anaesthetics. Like the list above, it works in two parts and fetches only the readings of those anaesthetics; the notes below say how to run it safely. Schemalyser carries the counts into the specification.`,
  chartedPasteLabel: 'The result of the count, copied from the results grid with its headers:',
  chartedRead: 'Keep the counts',
  chartedNone: 'Schemalyser could not read any code in the pasted text. Each row needs the three columns that the count returns: code, readings and anaesthetics.',
  chartedKept: 'Schemalyser has kept the counts for this period, and the specification lists them with the chosen codes. If the codes or the period change, Schemalyser offers the count again.',
  searchFound: (n: number) => `The search found ${n} ${n === 1 ? 'row' : 'rows'}. For each, choose what it is. Several rows may have the same meaning, and most will be neither.`,
  settingsHeading: 'The study period and the kinds of anaesthetic',
  settingsWhat: 'The study period applies to the start of each anaesthetic. Without a period, the audit counts every anaesthetic on record. Without a kind chosen, every kind counts. Both are carried into the specification and the reference query.',
  settingsFrom: 'From',
  settingsTo: 'To',
  settingsKinds: 'Count only these kinds of anaesthetic (none ticked means every kind, including sedation and procedures at the bedside):',
  settingsApply: 'Apply the period and the kinds',
  endingHeading: 'Where this audit question stands',
  endingSettled: (n: number) => `${n} ${n === 1 ? 'thing is' : 'things are'} settled.`,
  endingRemaining: 'These remain, grouped by who can settle them.',
  endingYouHeading: 'You can settle these now, in the meeting:',
  endingTeamHeading: 'The team that looks after the reporting database can settle these:',
  endingClinicianHeading: 'The clinician settles these after the meeting, in the folder for this audit, as each point says: by recording what each code means where the two of you could not choose, or by changing the audit\'s steps or the reference query:',
  remainingRose: (n: number, added: string[]) =>
    `That answer leaves ${n} more ${n === 1 ? 'thing' : 'things'} to settle than before, because Schemalyser has learned something new: ${added.join(' ')}`,
  endingByQuestion: 'You can answer the question with it above.',
  endingChooseAgain: 'You can choose it again in the list of what is charted above, where its count stands beside it.',
  endingByQuery: 'Run the short query with it above, then paste its result.',
  endingCountSeen: 'The count has run. The clinician checks the joins and filters that it rests on after the meeting.',
  endingByTeam: 'The note to the team below asks about this.',
  endingTeamRoute: (missing: string) => `${missing} is not visible to this login. The note to the team below asks whether it exists here and, if it does, for a login that can read it.`,
  endingNothing: 'Nothing remains to be settled.',
  endingClinician: 'The clinician settles this after the meeting.',
  teamNoteHeading: 'A note for the team that looks after the reporting database',
  teamNoteWhat: 'These points can be settled only by the team that looks after the reporting database. The note gathers them, ready to copy and send.',
  teamNoteCopy: 'Copy the note',
  teamNote: (lines: string[]) =>
    `Hello,\n\nWe are working through an approved anaesthesia audit on the reporting database, and a few points can be settled only by the team that looks after it. We would be grateful for your help with each of the following.\n\n${lines.map((line, i) => `${i + 1}. ${line}`).join('\n')}\n\nThank you for your help.`,
  teamNoteRoute: (missing: string) =>
    `${missing} is not visible to our login. Please tell us whether it exists in the reporting database and, if it does, whether our login can be given the right to read it.`,
  teamNoteOther: (point: string) => `${point} We were not able to settle this in the meeting, and we would be grateful for what you know of it.`,
  chooseAbove: 'The codes for this meaning are chosen in the table of the first meaning of this column, above.',
  noName: 'no name in the table that names the codes',
  chooseNoNames: 'None of the codes has a name in the table that names them, so none can be confirmed from that table. If you know the codes for this meaning, enter them here, separated by commas:',
  withdrawAnswer: 'Change this answer',
  withdrawn: 'Schemalyser has withdrawn the answer and worked out the checklist again, so the question is asked again.',
  newlyAnsweredNow: (n: number) =>
    n === 1
      ? 'What you have just entered settles 1 item, and Schemalyser lists it first.'
      : `What you have just entered settles ${n} items, and Schemalyser lists them first.`,
  groupNewNow: 'Settled just now',
  settingsBeforeCount: (from: number, first: number) =>
    `The study period begins in ${from}, but the count shows records only from ${first}. Anaesthetics before ${first} are not in this database, so the audit cannot count them.`,
  firstDoubt: {
    most: 'Most of the tables that the first query asked about did not come back. Your SQL window may be connected to the wrong database, or your login may be allowed to see only some tables. Before you go on, check which database the SQL window is connected to: in SQL Server Management Studio, its name shows in the drop-down list on the toolbar. If it is the wrong one, choose the reporting database there and run the first query again.',
    lookups: 'None of the tables that hold the names of codes, which the site rules list, came back from the first query. Your SQL window may be connected to the wrong database, or your login may be allowed to see only some tables. Before you go on, check which database the SQL window is connected to: in SQL Server Management Studio, its name shows in the drop-down list on the toolbar. If it is the wrong one, choose the reporting database there and run the first query again.',
  } as Record<string, string>,
  endingLessCertain: (n: number) => `${n} further ${n === 1 ? 'point is' : 'points are'} less certain. The audit query does not wait for ${n === 1 ? 'it' : 'them'}, and the specification lists ${n === 1 ? 'it' : 'them'}.`,
  endingSave: "Save before you close the page, and keep the file in the clinician's folder for this audit, on the hospital's network. At the next meeting, choose that folder, and nothing will be asked twice.",
  specWhatOpen: 'This page is for the person who writes the audit query. Some points are not yet settled, and it shows each as an assumption. It names this hospital\'s tables and codes, so it is for use inside the hospital only.',
  itemMore: 'More about this point',
  groupYou: 'Questions for you, and short queries',
  groupYouNote: 'Answer each question beside it. Where you are not sure, choose Not sure, and Schemalyser offers a short query that settles it instead. Several questions ask whether the audit joins two tables on the right columns; answer Yes if your team\'s SQL joins them on the same columns.',
  groupYouNone: 'Nothing here needs you now.',
  groupLater: (n: number) => `Show the ${n} other ${n === 1 ? 'point' : 'points'}, which wait for the team's SQL, the clinician or the central team`,
  otherFilesSummary: 'Other files from an earlier meeting, if you have them',
  firstMissing: (names: string[]) =>
    `The first query asked about ${names.length} ${names.length === 1 ? 'table' : 'tables'} that did not come back: ${names.join(', ')}. Each is not visible to this login: it may not exist here, or this login may not be allowed to see it.`,
  auditNeedsPeriod: 'Schemalyser will offer the reference query once a study period has been entered above, because without one it would read every anaesthetic on record.',
  searchWords: 'The words that the search looks for, separated by semicolons:',
  searchRewrite: 'Write the search again with these words',
  searchText: 'The blood pressure as text, from which the reference query does not read a mean',
  countPasteLabel: 'The result of the count, copied from the results grid with its headers:',
  countRead: 'Show the counts',
  countNone: 'Schemalyser could not read any year in the pasted text. Each row needs the three columns that the count returns.',
  countYear: 'Year',
  countAll: 'Anaesthetics',
  countCohort: 'In the cohort',
  countUnderTen: 'under 10',
  countAsk: "Compare these numbers with the hospital's usual workload, then choose one:",
  countNoKind: 'No kind recorded',
  countEmpty: "The count found no anaesthetic at all. Compare this with the hospital's usual workload, then choose one:",
  countRight: 'About right',
  countFew: 'Too few',
  countMany: 'Too many',
  decisionsHeading: 'Decisions for the clinicians',
  decisionsWhat: 'The audit makes these choices. Make each one together, and add a short note if you wish; the notes are kept only in the saved file. Each decision is carried into the specification.',
  decisionNote: 'A short note, if you wish',
  decisionRecorded: 'The reference query does not yet apply this decision. If you choose a change, the list of what remains says what would make it real.',
  decisions: [
    { key: 'pressures', title: 'Which pressures count once an arterial line is running:', applied: true, options: [
      ['preferred', 'The arterial reading is preferred only where an arterial and a cuff reading share a time (the rule in force)'],
      ['arterial_only', 'The arterial line alone, from its first reading to its last']] as [string, string][] },
    { key: 'floor', title: 'A mean pressure below this is treated as an artefact of zeroing or sampling and left out (leave empty for no floor):', applied: true, options: [] as [string, string][] },
    { key: 'ceiling', title: 'A mean pressure above this is treated as an artefact of a flush and left out (leave empty for no ceiling):', applied: true, options: [] as [string, string][] },
    { key: 'isolated', title: 'A single isolated low reading, that is, a single reading below 40 with the readings either side of it at 40 or above:', applied: false, options: [
      ['counts', 'It counts (the rule in force)'], ['ignored', 'It is ignored']] as [string, string][] },
    { key: 'bypass', title: 'Time on cardiopulmonary bypass:', applied: false, options: [
      ['counted', 'It is counted (the rule in force)'], ['left_out', 'It is left out']] as [string, string][] },
    { key: 'ecmo', title: 'Time on ECMO:', applied: false, options: [
      ['counted', 'It is counted (the rule in force)'], ['left_out', 'It is left out']] as [string, string][] },
    { key: 'age', title: 'Age:', applied: false, options: [
      ['postnatal', 'Under 28 days of postnatal age (the rule in force)'], ['postmenstrual', 'A limit on postmenstrual age']] as [string, string][] },
  ],
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
      "This file lists each pair of columns that the requests join, the kind of join, and the number of requests that make it. In a left, right or full join, the table being joined is on the right. A join marked 'where' is made in a WHERE clause or in a query nested inside another.",
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
      "This file lists the expressions that the requests compute from columns. Schemalyser has rewritten each one with the names from the list of tables and columns and with a placeholder in place of every value.",
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
      'This file holds the query results that Schemalyser accepted: the size of each table, a description of each column used in joins, and the values that each filtered column holds.',
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
