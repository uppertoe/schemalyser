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
  rulesNote: 'The site rules file tells Schemalyser about naming conventions at your site. Schemalyser works without one.',
  chooseFolder: 'The SQL files that you already have for this database, your own or your team\'s:',
  folderNote: 'Choose the folder that holds them. Schemalyser reads them here to see how you already join and filter these tables, and it keeps no text from them.',
  folderCount: (n: number) => `The folder you chose contains ${n} SQL ${files(n)}.`,
  chooseChecks: 'Earlier query results saved by Schemalyser, if you have them:',
  checksNote: 'Only needed if an earlier meeting saved results as a separate file. A saved state from an earlier meeting already holds them.',
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
  checklistIntro: "For each audit question, Schemalyser shows what it still needs, with a question for you or a short query beside each. The marks show what is settled, and you cannot tick them by hand.",
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
  groupSql: 'Points that the team\'s existing SQL could settle later',
  groupSqlNote: 'Nothing is needed from you for these in the meeting. If one of your team\'s SQL files does what an item describes, add the file below and analyse again.',
  groupSqlNone: 'No point for this audit question waits for the team\'s SQL.',
  groupOther: 'Points for the clinician or the central team after the meeting',
  groupOtherNote: 'Nothing is needed from you for these in the meeting. Each says who settles it and how.',
  groupAnswered: (n: number) => `Show the ${n} answered ${items(n)}`,
  blocking: 'The audit query must rest on this.',
  notBlocking: 'This makes the answer more certain, and the audit query does not wait for it.',
  statusNames: { answered: 'Answered.', partly: 'Partly answered.', open: 'Open.' } as Record<string, string>,
  readinessInFull: "Show the developer's detail",

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
  pasteWhat: 'Run a query from the checklist below in your SQL window, copy its results grid with the headers, and paste it here. You can paste the results of several queries at once. Schemalyser adds them to what it holds and works out the checklist again.',
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
  firstWhat: "Without a list of tables and columns, Schemalyser writes one short query that asks SQL Server which columns the tables of the audit and of your SQL files have, and how large each table is. Choose the folders above, then write the query. The query holds the table names from your files, so Schemalyser shows it only here and writes it into no file.",
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
    `Schemalyser has read ${columns} columns of ${tables} tables.${
      sized === 0
        ? ' This login cannot see the sizes of the tables, so Schemalyser will offer no query on a table that could be large, and will ask you instead.'
        : sized < tables
          ? ` SQL Server gave no size for ${tables - sized} of them, as happens for a view, so Schemalyser will offer no query on those that could be large.`
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
  whoLabel: "Your name, if you would like it kept with your answers (it is kept only in the saved state):",
  factYes: 'Yes, this is right',
  factNo: "No",
  factInstead: "If it is not right, the columns that do match:",
  factSaveNo: 'Save the answer that this is not right',
  codesLabel: 'The local codes, separated by commas:',
  codesSave: 'Save the codes',
  factUnreadable:
    'Schemalyser could not record that answer, because it names something that the catalogue does not hold or a code that cannot be accepted.',
  factRecorded: 'Schemalyser has recorded the answer and worked out the checklist again.',
  questionFirst: 'The question for you:',
  queryAlternative: 'If nobody can answer the question from knowledge, the query below answers it instead.',
  groupUnneeded: (n: number) => `Show the ${n} ${items(n)} that this question does not depend on`,
  unneededWhat:
    'The answer to this question does not depend on these items, so they do not count against answering it from the source database. The OMOP release still needs them.',

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
    'Schemalyser could not restructure this query to start from the cohort, so it is the composition of the steps of the conversion as they are, and its header says why. It builds every row of those steps before it keeps the rows of the question, so it is not suitable to run on a large database. It is shown here as a reference for the specification.',
  saveChecks: 'Save the check results as checks.csv',
  saveStateNote: "The file holds everything that you have settled today: the list of tables and columns, the results that you pasted, your answers and the codes that you chose. Please keep it in the clinician's folder for this audit, on the hospital's network, and choose it as that folder at the next meeting, so that nothing is asked twice.",
  // The two stages of each checklist.
  releaseHeading: 'For the later OMOP release',
  releaseWhat:
    'These items matter only when the conversion is released into the OMOP database. They do not count against answering the question from the source database.',
  auditHeading: 'The audit query',
  auditWhat: "This is the question itself, written as one query over the hospital's tables, as a reference for the person who writes the audit query. Before any query like it is run on a large database, choose a study period, run it on the reporting copy rather than the live system, and run it out of hours.",
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

  offlineHowSummary: 'How to take this page offline',
  policySummary: 'How this page checks that nothing can leave it',
  githubSummary: 'Fetch the files from GitHub instead (not needed in a meeting)',
  catalogueSummary: 'If your team already keeps the full list of tables and columns',
  needs: (questions: number, queries: number, other: number) => {
    const total = questions + queries + other;
    if (!total) return 'Schemalyser needs nothing more for this audit question.';
    const parts = [
      questions ? `${questions} ${questions === 1 ? 'question' : 'questions'} for you` : '',
      queries ? `${queries} short ${queries === 1 ? 'query' : 'queries'}` : '',
      other ? `${other} ${other === 1 ? 'point' : 'points'} for the clinician after the meeting` : '',
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
  searchPrivate: "The names that the search returns are the hospital's own. Schemalyser shows them only on this page, and keeps only the codes that you choose, in the saved state.",
  searchNone: 'Schemalyser could not read any code and name in the pasted text. Each row needs the two columns that the search returns, code and name.',
  searchName: 'Name',
  searchCode: 'Code',
  searchChoice: 'What it is',
  searchNeither: 'Neither',
  searchSave: 'Save the choices',
  searchFound: (n: number) => `The search found ${n} ${n === 1 ? 'row' : 'rows'}. For each, choose what it is. Several rows may have the same meaning, and most will be neither.`,
  settingsHeading: 'The study period and the kinds of anaesthetic',
  settingsWhat: 'The study period applies to the start of each anaesthetic. Without a period, the audit counts every anaesthetic on record. Without a kind chosen, every kind counts. Both are carried into the specification and the reference query.',
  settingsFrom: 'From',
  settingsTo: 'To',
  settingsKinds: 'Count only these kinds of anaesthetic (none ticked means every kind, including sedation and procedures at the bedside):',
  settingsApply: 'Apply the period and the kinds',
  endingHeading: 'Where this audit question stands',
  endingSettled: (n: number) => `${n} ${n === 1 ? 'thing is' : 'things are'} settled.`,
  endingRemaining: 'These remain, each with who can settle it:',
  endingByQuestion: 'You can answer the question with it above.',
  endingByQuery: 'The short query with it above settles it.',
  endingNothing: 'Nothing remains to be settled.',
  endingClinician: 'The clinician settles this after the meeting.',
  endingLessCertain: (n: number) => `${n} further ${n === 1 ? 'point is' : 'points are'} less certain. The audit query does not wait for ${n === 1 ? 'it' : 'them'}, and the specification lists ${n === 1 ? 'it' : 'them'}.`,
  endingSave: "Save before you close the page, and keep the file in the clinician's folder for this audit, on the hospital's network. At the next meeting, choose that folder, and nothing will be asked twice.",
  specWhatOpen: 'This page is for the person who writes the audit query. Some points are not yet settled, and it shows each as an assumption. It names this hospital\'s tables and codes, so it is for use inside the hospital only.',
  itemMore: 'More about this point',
  groupYou: 'Questions for you, and short queries',
  groupYouNote: 'Answer each question beside it. Where you are not sure, choose Not sure, and Schemalyser offers a short query that settles it instead.',
  groupYouNone: 'Nothing here needs you now.',
  groupLater: (n: number) => `Show the ${n} other ${n === 1 ? 'point' : 'points'}, which wait for the team's SQL, the clinician or the central team`,
  otherFilesSummary: 'Other files from an earlier meeting, if you have them',
  firstMissing: (names: string[]) =>
    `The first query asked about ${names.length} ${names.length === 1 ? 'table' : 'tables'} that did not come back: ${names.join(', ')}. Each is not visible to this login: it may not exist here, or this login may not be allowed to see it.`,
  auditNeedsPeriod: 'Schemalyser will offer the reference query once a study period has been entered above, because without one it would read every anaesthetic on record.',
  searchWords: 'The words that the search looks for, separated by semicolons:',
  searchRewrite: 'Write the search again with these words',
  searchText: 'A blood pressure charted as text, with the mean in brackets',
  countPasteLabel: 'The result of the count, copied from the results grid with its headers:',
  countRead: 'Show the counts',
  countNone: 'Schemalyser could not read any year in the pasted text. Each row needs the three columns that the count returns.',
  countYear: 'Year',
  countAll: 'Anaesthetics',
  countCohort: 'In the cohort',
  countUnderTen: 'under 10',
  countAsk: 'Do these numbers look right for this hospital?',
  countEmpty: 'The count found no anaesthetic at all. Does that look right for this hospital?',
  countRight: 'About right',
  countFew: 'Too few',
  countMany: 'Too many',
  decisionsHeading: 'Decisions for the clinicians',
  decisionsWhat: 'The audit makes these choices. Make each one together, and add a short note if you wish; the notes are kept only in the saved file. Each decision is carried into the specification.',
  decisionNote: 'A short note, if you wish',
  decisionRecorded: 'The specification records this decision, and the reference query does not yet apply it.',
  decisions: [
    { key: 'pressures', title: 'Which pressures count once an arterial line is running:', applied: true, options: [
      ['preferred', 'The arterial reading is preferred only where an arterial and a cuff reading share a time (present rule)'],
      ['arterial_only', 'The arterial line alone, from its first reading to its last']] as [string, string][] },
    { key: 'floor', title: 'A mean pressure below this is treated as an artefact of zeroing, flushing or sampling (leave empty for no floor):', applied: true, options: [] as [string, string][] },
    { key: 'isolated', title: 'A single isolated low reading:', applied: false, options: [
      ['counts', 'It counts (present rule)'], ['ignored', 'It is ignored']] as [string, string][] },
    { key: 'bypass', title: 'Time on cardiopulmonary bypass or ECMO:', applied: false, options: [
      ['counted', 'It is counted (present rule)'], ['left_out', 'It is left out']] as [string, string][] },
    { key: 'age', title: 'Age:', applied: false, options: [
      ['postnatal', 'Under 28 days of postnatal age (present rule)'], ['postmenstrual', 'A limit on postmenstrual age']] as [string, string][] },
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
