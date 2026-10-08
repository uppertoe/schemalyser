// The wording of the page, as approved on 3 and 4 October 2026, with the checklist's wording added on
// 4 October 2026 in the same voice, and rewritten on 7 October 2026 so that the page explains itself: an overview of the
// steps, each step and each part of a checklist in the same shape, one name for each thing, and a glossary.
// The owner no longer reviews this internal tool's wording.
// The sentences that summarise the results and say what could not be read come from the core
// (core/schemalyser/vocabulary.py), because the coverage file carries the same wording.

// The note that the clinician can send before the meeting. A test confirms that it is the text of docs/first-ask-note.md.
const NOTE = `Subject: A request for help with an approved anaesthesia audit, in short steps

Hello,

I am running an approved audit, under the approval [approval reference], and I would like your help to answer it from the source database. The audit asks [the question in one sentence]. It is also the first step towards an OMOP anaesthesia layer, so the answers that you give me will be used again when that layer is built.

The help that I need comes in short steps, and I will send each one only after the one before it.

1. One query that reads only SQL Server's own records. It lists the columns of the tables that our existing anaesthesia queries already read, and the number of rows that the server records for each, rounded down to the nearest ten. It reads no table.
2. A few questions that you may be able to answer from what you know, such as whether two columns join and which codes mean a mean arterial pressure, and, where a question cannot be answered that way, a few short counting queries.
3. The audit query itself, or a one-page specification of it if you would rather write it yourself.

Each query is a single SELECT that writes, creates and changes nothing. It reads WITH (NOLOCK), which means that it takes no row locks, but it holds a schema lock while it runs, so please do not run it during the nightly load. Each counting query rounds its counts down to the nearest ten and leaves out anything that fewer than ten rows hold, and a query on a large table reads a sample of about five million rows, so its counts are estimates. Each query has a comment at the top that says what it does, so that you can read it before you run it. The rounding and the leaving out of small counts reduce what a count can disclose, but they do not make the results anonymous, and repeated counts over slightly different groups can reveal more than one count does. The results are therefore for use inside the hospital until the hospital's own rules say otherwise.

You would paste each result back to me. I put the results into a page that runs in my browser on a hospital computer, with that browser tab taken offline so that the page cannot send anything anywhere. The results, and the facts that you confirm, are kept in a repository that the hospital controls, [the repository].

The queries are written by a tool that I built with the help of an AI model, [the model and the service]. These controls can be checked: the model worked only from invented examples and never saw any hospital data, any of your team's SQL or any name from our database; the tool runs offline; and its code is open to read. The use of AI in this work follows [the hospital's AI policy].

Thank you for considering it. I am glad to go through any of it with you in person.`;

const files = (n: number) => (n === 1 ? 'file' : 'files');

// What happens when the page goes back online, said in the same words wherever the page says it.
const ONLINE_AGAIN =
  'If this page goes back online while it holds your files, Schemalyser stops its analysis engine and discards what it has read from them, and keeps only the inventory and the checklists, which you can still download.';

// How large a table is, in words that match its size, so that a small table is never called large.
const sizeWords = (rows: number) =>
  rows < 100_000 ? 'which is small' : rows < 10_000_000 ? 'which is a moderate size' : 'which is large';
const costTail = 'If it reaches the time limit, it stops by itself; in that case, do not run it again, and tell the clinician leading the audit.';
const readingsWords = (readings?: [string, number | null] | null) =>
  readings
    ? `the table of readings, ${readings[0]}, which holds every value charted during an anaesthetic${
        readings[1] ? ` and here holds about ${readings[1].toLocaleString('en-AU')} rows, ${sizeWords(readings[1])}` : ', and which is usually the largest table in the reporting database'
      }`
    : 'the table of readings, which holds every value charted during an anaesthetic and is usually the largest table in the reporting database';
const items = (n: number) => (n === 1 ? 'item' : 'items');
const queries = (n: number) => (n === 1 ? 'query' : 'queries');
const rows = (n: number) => (n === 1 ? 'row' : 'rows');

// One stage of the meeting, in the same shape everywhere: what it is for and who does it, what to do, what you see when
// it has worked, what Schemalyser does with what you gave it, and what comes next. Any part may be left out.
export interface Guide {
  purpose: string;
  steps?: string[];
  worked?: string;
  does?: string;
  next?: string;
}

export const strings = {
  title: 'Schemalyser',
  intro:
    "Schemalyser helps a clinician who leads an audit and a colleague who can read the hospital's reporting database to work out, together, how to answer the audit question from that database. The page writes each query that the colleague runs in a SQL window of their own, reads each result that the colleague pastes back, and ends with a specification of the audit query and a list of what remains, with who can settle each point.",

  // The overview at the top of the page: the stages of the meeting, numbered as the steps below are.
  overviewSummary: 'How the meeting runs, step by step',
  overviewLead:
    "Two people use this page together at one computer: the clinician who leads the audit, and a colleague who can read the reporting database, which is the copy of the hospital's records that SQL Server holds for reporting. The page has seven steps. The meeting itself is steps 1 to 6. Step 7 is an optional practice step, which either of you can use at any time, and which stands in for a SQL window when you try the invented example. The page opens each step in turn.",
  overviewPrivate: 'The whole page runs in this browser, and it sends nothing anywhere.',
  overviewExample:
    'If you would like to try the page before the meeting, you can load the invented example in step 2. Everything in it is made up, and nothing in it comes from a hospital.',
  overview: [
    ['Before you start', 'The clinician brings the folder for this audit, and the colleague brings any SQL files that they or their team already use with this database. Both of you read this overview, and nothing is chosen or run yet.'],
    ['Take this page offline', 'Whoever is at the keyboard waits for the page to load, then takes this browser tab offline, so that the page cannot send anything. The SQL window stays connected. At the end of this step the page is offline and holds nothing yet.'],
    ['Choose the folder for this audit and the SQL files', 'The clinician chooses the folder for this audit, and the colleague chooses the folder that holds the SQL files. If the folder for this audit holds no list of tables and columns, the colleague runs the tables and columns query in the SQL window and pastes its result back. At the end of this step you choose Analyse, and Schemalyser makes a checklist for each audit question.'],
    ['Work through the checklist together', 'The colleague answers the questions that they can, runs each short query, the count by year and the list of what is charted, and pastes each result back. The two of you choose the codes and make the clinical decisions together. At the end of this step you have the specification of the audit query, a list of what remains with who can settle each point, and a saved file that holds what was settled today.'],
    ['Check what Schemalyser found in the SQL files', 'The colleague reads the inventory, which lists the tables, columns and joins that the SQL files use, before anything is downloaded. A join is the way in which a query links the rows of two tables, by matching a column of one with a column of the other. At the end of this step you know exactly what a download would hold.'],
    ['Download the results', 'If the clinician would like a record of what the SQL files use, the clinician downloads the inventory and the checklists here. The file that the next meeting needs is the one saved in step 4.'],
    ['Try the SQL files in a practice database', 'This is the optional practice step, which is not one of the six steps of the meeting. Either of you can run the SQL files, or a query that the checklist offers, on a small practice database of invented rows that the page builds in this browser. With the invented example loaded, the practice database stands in for a SQL window.'],
  ] as [string, string][],

  steps: [
    'Before you start',
    'Take this page offline',
    'Choose the folder for this audit and the SQL files',
    'Work through the checklist together',
    'Check what Schemalyser found in the SQL files',
    'Download the results',
    'Try the SQL files in a practice database',
  ],

  // Each step, in the shape of the Guide above. The first step's purpose is catalogueWhy, which stands on its own.
  guides: [
    {
      purpose: '',
      steps: [
        'The clinician copies the folder for this audit onto this computer, from the USB stick or the email that it came on, and notes where it is.',
        'The colleague opens a SQL window connected to the reporting database, such as SQL Server Management Studio, and finds the folder that holds the SQL files of their team, if there is one.',
        'If neither of you has used this page before, you can load the invented example in step 2 and try the whole page with it first.',
      ],
      next: 'Next, once the page has loaded, take it offline in step 2.',
    },
    {
      purpose: 'This step takes this browser tab offline, so that nothing that you choose or paste later can leave the page. The person at the keyboard does it, and the SQL window on the same computer stays connected.',
      steps: [
        'Wait until the page says that Schemalyser has finished loading. Loading needs the network, and it can take a minute.',
        'If you would like to try the invented example, choose Load the invented example now, while the page is still online.',
        'Take the tab offline, as the instructions under How to take this page offline describe.',
      ],
      worked: 'When it has worked, the box beside the steps reads This page is offline, and step 3 opens.',
      does: `Schemalyser holds nothing yet. ${ONLINE_AGAIN}`,
      next: 'Next, choose the folder for this audit and the SQL files in step 3.',
    },
    {
      purpose: 'This step gives Schemalyser the two things that it reads: the folder for this audit, which the clinician brings, and the SQL files that the colleague brings.',
      steps: [
        'The clinician chooses the folder for this audit under 3.1.',
        'The colleague chooses the folder that holds the SQL files under 3.2. If the colleague has no SQL files, leave 3.2 empty, and Schemalyser writes the tables and columns query from the table names in the audit\'s steps alone.',
        'If the page then shows 3.3, the colleague runs the tables and columns query and pastes its result back, as 3.3 describes. If the folder for this audit already holds a list of tables and columns, 3.3 does not appear.',
        'Choose Analyse the folder and the SQL files, under 3.4.',
      ],
      worked: 'When it has worked, step 4 opens with a checklist for each audit question.',
      does: 'Schemalyser reads every file here, on this computer, and keeps nothing on any server. From the SQL files it takes only the names of tables and columns and the ways in which they are joined and filtered. It never writes out a comment or a file name from them, and it writes out a value only where the query results already list it as a code of that column.',
      next: 'Next, work through the checklist together in step 4.',
    },
    {
      purpose: 'This step is the meeting itself. Below is a checklist for each audit question, and each says what Schemalyser still needs before the audit query can be written. The colleague answers the questions and runs the queries, and the two of you make the clinical choices together. Each checklist has the same parts, in this order, and a part appears only when it is needed:',
      steps: [
        'Under 4.1, choose the database that your SQL window is connected to.',
        'Under 4.2, read the questions for the colleague, which you can copy as one list.',
        'Under 4.3, if it appears, run the table sizes query and paste its result into the box for the results of the short queries, near the top of this step.',
        'Under 4.4, work down the points. Answer each question beside it. For a short query, run it and paste its result into the box for the results of the short queries. For the count by year, paste its result into the box beside it.',
        'Under 4.5, once the count by year has been seen, run the list of what is charted, paste its result into the box beside it and mark the codes that matter.',
        'Under 4.6, enter the study period, choose the kinds of anaesthetic, make the decisions for the clinicians, and apply them.',
        'Under 4.7, if you wish, run the count of the chosen codes.',
        'Under 4.8, read where the audit question stands, and save what has been settled today.',
        'Under 4.9, copy or save the specification, and under 4.10, once nothing remains, the reference query.',
      ],
      worked: 'Each time you answer or paste, Schemalyser works the checklist out again, which can take several seconds. While it works, the point that you answered says so beside the button that you pressed. When it has finished, it says so in the same place, and 4.4 lists first, under Settled just now, the points that your answer settled.',
      does: 'Schemalyser keeps your answers and the pasted results on this page until you save them, and it never sends them anywhere. It writes them out only into the file that you save.',
      next: 'Next, once you have saved, you can check what Schemalyser found in the SQL files in step 5.',
    },
    {
      purpose: 'This step lets the colleague see exactly what Schemalyser took from the SQL files, before anything is downloaded. Nothing here needs an answer.',
      steps: [
        'Read the sentences at the top, which say how many files Schemalyser read and what it could not read.',
        'Read the inventory below, file by file.',
        'If you see anything that should not leave your team, choose Clear everything in step 6, and do not download anything.',
      ],
      does: 'The inventory holds only names from the list of tables and columns, with counts. Where a SQL file held a value, such as a date, the inventory holds a placeholder in its place, or leaves it blank. The one exception is the filters file, which keeps a code that a SQL file compares with a column where the query results already list that code for that column.',
      next: 'Next, download the results in step 6, if the clinician would like them.',
    },
    {
      purpose: 'This step saves the inventory and the checklists as one zip file, for the clinician\'s records. The next meeting does not need it, because the file saved in step 4 holds everything that the next meeting needs.',
      steps: [
        'Choose Download the inventory and the checklists, and keep the file with the folder for this audit.',
        'If your team would rather run every short query in one go, choose Download the check script, run it in your SQL window, and save its results as a CSV file.',
      ],
      does: 'Schemalyser writes the download from what this page shows. It holds no comment and no file name from the SQL files, and no value from them except the codes that the query results already list, as step 5 shows.',
      next: 'When you have finished, close the page, or choose Clear everything. Schemalyser keeps nothing after the page is closed.',
    },
    {
      purpose: 'This is the optional practice step, which is not one of the six steps of the meeting. It builds a small practice database of invented rows in this browser, shaped like the tables that the SQL files use, so that you can try a query without touching the hospital\'s database.',
      steps: [
        'Enter how many rows each table should hold, then choose Build the practice database.',
        'Type or paste a query into the box, and choose Run the query.',
        'To try every SQL file in turn, choose Run the SQL files.',
      ],
      does: 'The practice database exists only in this browser tab, holds only invented values, and goes when you close the page.',
    },
  ] as Guide[],

  catalogueWhy:
    "This step makes sure that the two of you have what the meeting needs before anything is chosen. The clinician needs the folder for this audit, which the clinician prepared with Schemalyser before the meeting. The colleague needs a SQL window connected to the reporting database and, if there are any, the SQL files that the colleague or their team already use with it. To get ready, follow these steps:",
  querySafe: "The query reads only SQL Server's own records of its tables and columns, and no row of any table.",
  copyQuery: 'Copy the query',
  skipCatalogue:
    "This query lists every table and column of the database that this login can see, without their sizes. Most meetings do not need it, because in step 3 Schemalyser writes the tables and columns query, which asks only about the tables that the audit and the SQL files read, and gives their sizes as well. If your team prefers to keep the full list, run this query, save its result as a CSV file, which is a plain text file with one row to a line and its values separated by commas, and choose that file in step 3 under Other files.",
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
    `The page must stay offline for as long as you use it. ${ONLINE_AGAIN} To carry on, choose Begin a new analysis, wait for the page to load again, take it offline again and choose the files again. If you saved what had been settled, nothing that you answered is lost.`,
  ],
  noFilesWhileConnected: 'Schemalyser will not accept any files while this page is online.',
  exampleHeading: 'Try the invented example',
  exampleWhat:
    'If you would like to see the whole page working before you bring any files, Schemalyser can load an invented example in their place: a folder for an audit with four audit questions, and fifteen SQL files of the kind that a team keeps, all made up for testing. None of it comes from a hospital. Load it while the page is online, then take the page offline, and choose Analyse the folder and the SQL files in step 3.',
  exampleLoad: 'Load the invented example',
  exampleLoading: 'Schemalyser is loading the invented example.',
  exampleLoaded: (state: number, requests: number) =>
    `Schemalyser has loaded the invented example, which holds ${state} ${files(state)} for the folder for this audit and ${requests} SQL ${files(requests)}. Take the page offline, then choose Analyse the folder and the SQL files in step 3. The example's query results came with its folder, so you can answer its questions without a SQL window. For the short queries and the count by year, the practice database in step 7 can stand in for a SQL window.`,
  exampleProgress: (done: number, total: number) =>
    `Schemalyser is loading the invented example, which takes a few seconds. It has fetched ${done} of its ${total} files. Please keep the page online until it has finished.`,
  exampleFailed:
    'Schemalyser could not fetch every file of the invented example, even after trying each one twice, so it has not loaded it. Keep the page online and choose Load the invented example again. If it fails a second time, reload the page while it is online, wait until Schemalyser has finished loading, and choose Load the invented example once more.',
  exampleFailedOffline:
    'Schemalyser could not fetch every file of the invented example, because the page went offline before loading had finished, so it has not loaded it. Bring the page back online, choose Load the invented example again, and take the page offline only once Schemalyser says that the example has loaded.',
  exampleChosen:
    'The invented example is loaded in place of your own files. Its folder already holds a list of tables and columns and the results of some queries, so the tables and columns query is not needed, and you can go straight to 3.4. Everything in it is made up, and none of it comes from a hospital. If you choose any file of your own, Schemalyser discards the example and anything worked out from it, and starts clean.',
  exampleBanner:
    'This checklist comes from the invented example. Its tables, codes, SQL files and results are made up, and none of them comes from a hospital. The results of some queries came with the example\'s folder, so some points are already settled, and where a point says that a result lists codes, that result came with the folder. You can still answer each question with the buttons beside it. The example has no SQL window, but the practice database in step 7 can stand in for one for the short queries and the count by year: build it, paste the query into its box, run it, then copy the result table and paste it back here, as you would from a SQL window.',

  offline: 'This page is offline. You can now choose the files.',
  stateHeading: '3.1 The folder for this audit',
  chooseState: 'Choose the folder for this audit:',
  stateNote: "The clinician leading the audit prepared this folder with Schemalyser before the meeting, and brings it on a USB stick or by email. It holds the audit's steps, which say how the hospital's tables are read to answer the question. It holds the audit question itself, written as a query. It holds the site rules, which say how this hospital names its tables and codes. A folder saved after an earlier meeting also holds the list of tables and columns and the answers given so far. Schemalyser reads the folder here, changes nothing in it, and keeps nothing from it on any server.",
  stateFound: (catalogue: boolean, conversion: number, targets: number) =>
    `Schemalyser has found in the folder for this audit ${targets ? `${targets} audit ${targets === 1 ? 'question' : 'questions'}` : 'no audit question'}${
      conversion ? '' : ', but not the steps that read the tables'
    }${catalogue ? ', and it holds a list of tables and columns' : ', and it holds no list of tables and columns yet'}.`,
  keptForComparison:
    'Schemalyser has kept the checklist from the previous analysis, and it will show what the next analysis answers.',
  otherFilesSummary: 'Other files, which most meetings do not need',
  chooseCatalogue: 'A list of tables and columns saved as a CSV file, if you have one:',
  catalogueNote: "Choose this only if your team keeps the full list of tables and columns, made with the query that is folded away in step 1, and the folder for this audit does not already hold one. Without it, Schemalyser offers the tables and columns query under 3.3, which gives the same list for the tables that the audit needs, with their sizes.",
  chooseRules: 'A site rules file, if it was given to you on its own:',
  rulesNote: 'The site rules file, site-rules.json, tells Schemalyser how this hospital names its tables and codes. It is usually already in the folder for this audit, and Schemalyser works without one.',
  sqlHeading: '3.2 The SQL files that you already have',
  chooseFolder: 'Choose the folder that holds the SQL files:',
  folderNote: "These are queries that the colleague or their team already use with this database, saved as .sql files. Nothing has to be fetched from anywhere else, and any number of files will do. Schemalyser reads them here to learn how your team already joins and filters these tables, so that it can ask fewer questions. It writes out the names of tables and columns that it finds in them, and never a comment or a file name. It writes out a value only where the query results already list it as a code of that column. If you have no SQL files, you can leave this empty, and the page will ask more questions instead. Schemalyser can still write the tables and columns query under 3.3 from the table names in the audit's steps alone.",
  folderCount: (n: number) => `Schemalyser has found ${n} SQL ${files(n)} in the folder that you chose.`,
  chooseChecks: 'Query results saved from an earlier meeting as a separate file, if you have them:',
  checksNote: 'You need this only if an earlier meeting saved its query results as a file of their own. The file saved at the end of an earlier meeting already holds them, so most meetings do not need this.',
  checksError:
    'Schemalyser could not read the file of query results. Please check that you have chosen checks.csv from an earlier meeting, or the CSV file of results from the check script.',
  analyseHeading: '3.4 Analyse the folder and the SQL files',
  analyseWhat: 'Once the folder for this audit and the SQL files are chosen, and the list of tables and columns is in hand, choose the button below. Schemalyser reads everything here, which can take a minute, and then makes a checklist for each audit question in step 4.',
  analyse: 'Analyse the folder and the SQL files',
  catalogueError:
    'Schemalyser could not read the list of tables and columns. The file needs the columns TABLE_NAME, COLUMN_NAME and DATA_TYPE. Please check that you have chosen the CSV file that the query in step 1 produces.',
  analysisFailed: 'Schemalyser has not been able to finish reading the folder and the SQL files. You can choose the files and try again.',

  progress: (done: number, total: number) =>
    `Schemalyser is reading the SQL files. It has read ${done} of ${total} ${files(total)}.`,
  boundaryProgress: 'Schemalyser has read the SQL files and is now making the checklist for each audit question.',

  // The checklist.
  checklistIntro: "Below is one checklist for each audit question in the folder for this audit, each headed by the question in plain words, with the name of its file after it in smaller type. The checklist names a column as TABLE.COLUMN, that is, the table's name, a full stop and the column's name. Each begins by saying what Schemalyser still needs and who can settle it, and Schemalyser sets each mark itself once a point is settled. The reporting database is the copy of the hospital's records that your SQL window reads, and the team that looks after it, usually the hospital's data or reporting team, can grant access and answer questions about its tables.",
  noChecklist:
    "To see a checklist for each audit question, choose the folder for this audit in step 3. That folder must hold a folder named conversion, with the audit's steps, and a folder named targets, with the audit questions. The clinician who prepared it with Schemalyser can check that both are there.",
  boundaryProblems: {
    bad_rules:
      'Schemalyser could not read site-rules.json, so it has not made the checklists. If the file is meant to hold the site rules, check that it is valid JSON and uses only the known keys.',
    bad_options:
      'Schemalyser could not read boundary.json, so it has not made the checklists. The file may set only includeSpans and includeFanout, each to true or false.',
    bad_checks:
      'Schemalyser could not read checks.csv as query results, so it has not made the checklists.',
    bad_conversion:
      "Schemalyser could not read the folder named conversion, which holds the audit's steps, so it has not made the checklists. Please check that conversion.json lists each step and that each step's file is present.",
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
      before !== undefined && before !== answered ? `, compared with ${before} when the files were last analysed` : ''
    }.`,
  newlyAnswered: (n: number) =>
    n === 1
      ? 'Since the previous analysis, 1 point has been settled, and Schemalyser lists it first.'
      : `Since the previous analysis, ${n} points have been settled, and Schemalyser lists them first.`,
  changes: (answered: number, targets: number) =>
    answered === 0
      ? 'Schemalyser has analysed the files again. No point that was open or partly settled before is settled yet.'
      : `Schemalyser has analysed the files again. Across the ${targets} audit ${targets === 1 ? 'question' : 'questions'}, ${answered} ${answered === 1 ? 'point' : 'points'} ${
          answered === 1 ? 'is' : 'are'
        } now settled that ${answered === 1 ? 'was' : 'were'} not settled before.`,
  groupNew: 'Settled since the previous analysis',
  groupSql: 'Points that the team\'s existing SQL could settle later',
  groupSqlNote: 'Nothing is needed from you for these in the meeting. If one of your team\'s SQL files does what a point describes, add the file at the end of this step and analyse again.',
  groupSqlNone: 'No point for this audit question waits for the team\'s SQL.',
  groupOther: 'Points for the clinician or the central OMOP team after the meeting',
  groupOtherNote: 'Nothing is needed from you for these in the meeting. Each says who settles it and how.',
  groupAnswered: (n: number) => `Show the ${n} settled ${n === 1 ? 'point' : 'points'}`,
  groupGiven: 'The answers that you have given',
  groupGivenNote: 'Each answer that you have given stands here beside the button that changes it. If an answer turns out to be wrong, change it, and Schemalyser asks the question again.',
  blocking: 'The audit query rests on this point, so Schemalyser cannot write it until the point is settled.',
  blockingSettled: 'The audit query rests on this point, which is now settled.',
  notBlocking: 'This point makes the answer more certain, and the audit query does not wait for it.',
  // In the fold beneath a question for the colleague, what settles the point, in the same terms as the buttons above it.
  askSettles: {
    join: "If the colleague answers Yes beside this point, the point is settled. A SQL file from your team that makes the same join is another way to settle it: if you have one, add it under Add more of your team's SQL files, at the end of this step, and choose Analyse again. Either is enough on its own.",
    filter: "If the colleague answers Yes beside this point, the point is settled. A SQL file from your team that compares this column with the same values is another way to settle it: if you have one, add it under Add more of your team's SQL files, at the end of this step, and choose Analyse again. Either is enough on its own.",
  } as Record<string, string>,
  statusNames: { answered: 'Settled.', partly: 'Partly settled.', open: 'Open.' } as Record<string, string>,
  againMark: 'To be asked again on the production copy.',
  readinessInFull: "Show the developer's detail",

  // The short queries on the checklist, and the box into which their results are pasted.
  sizesHeading: '4.3 The table sizes query',
  sizesHow:
    'Run the table sizes query in your SQL window, select the whole results grid, copy it with its headers, and paste it into the box for the results of the short queries, near the top of this step. It returns one row for each table, with its number of rows rounded down to the nearest ten. Once it is pasted, Schemalyser shows the short queries that were waiting for it.',
  queryStates: {
    ready: 'This short query is ready to run. Run it, copy its result with its headers, and paste it into the box for the results of the short queries, near the top of this step.',
    waiting: 'The short query for this point appears once the result of the table sizes query, under 4.3, has been pasted.',
    large: 'Schemalyser offers no query for this point, because one of the tables that it would read is too large to count without slowing the database for everyone else.',
    ran: 'The short query for this point has run.',
  } as Record<string, string>,
  queryShownEarlier: 'The short query shown with an earlier point answers this point as well.',
  pasteHeading: 'The box for the results of the short queries',
  pasteWhat: 'The short queries are the small counting queries that appear beside the points of each checklist below, together with the table sizes query. Each reads the reporting database, changes nothing in it, and returns only names and counts rounded down to the nearest ten. To use one, choose Copy the query beside it, run it in your SQL window, select the whole results grid, right-click and choose Copy with Headers, and paste the result here. In SQL Server Management Studio, the empty square at the top left of the results grid selects all of it. Do not run any query during the nightly load, which is when the reporting database is refreshed from the hospital\'s systems, usually overnight; the team that looks after the reporting database can tell you its hours. You can paste the results of several short queries at once. Schemalyser adds them to the results that it already holds, works out the checklist again and marks what they settle. The count by year, the list of what is charted, the count of the chosen codes and the name search each have a box of their own beside them.',
  pasteLabel: 'The results of the short queries, copied from the results grid with their headers, or from a CSV file:',
  readPaste: 'Read the results of the short queries',
  pasteReading: 'Schemalyser is reading the results of the short queries and working out the checklist again.',
  pasted: (read: number, accepted: number) =>
    accepted === 0
      ? `Schemalyser has not kept any of the ${read} pasted ${rows(read)}, because none of them is a row that a short query from this page could have returned. Please check that you copied the result of a short query, with its headers.`
      : accepted === read
        ? `Schemalyser has read the ${read} pasted ${rows(read)} of the results of the short queries, and added ${read === 1 ? 'it' : 'them'} to the results that it holds.`
        : `Schemalyser has kept ${accepted} of the ${read} pasted rows of the results of the short queries, and left out the others, because a short query from this page could not have returned them.`,
  pasteUnreadable:
    'Schemalyser could not read the pasted text as the results of a short query. Each row needs the nine columns that a short query returns, from check_kind to is_unique.',
  pasteFailed:
    'Schemalyser has not been able to read the pasted results. The checklist is as it was, and you can paste them again.',
  changesAfterPaste: (answered: number) =>
    answered === 0
      ? 'Schemalyser has worked out the checklist again with the pasted results. No point that was open or partly settled before is settled yet.'
      : `Schemalyser has worked out the checklist again with the pasted results, and ${answered} ${answered === 1 ? 'point' : 'points'} ${
          answered === 1 ? 'is' : 'are'
        } now settled that ${answered === 1 ? 'was' : 'were'} not settled before.`,
  profileHeading: 'For the central OMOP team, the team that runs the hospital\'s main OMOP database',
  profileWhat:
    "These queries run on the OMOP database, not on the reporting database. The first reads only SQL Server's own records of the tables, and the others wait for its result, because Schemalyser offers no query on a table whose size it does not know.",
  queryInProfile: 'The query for this point is in the section for the central OMOP team, above.',
  profilePasteHeading: 'Paste the results of the queries for the central OMOP team',
  profilePasteWhat:
    'When the central OMOP team returns the results of its queries, paste them here, as copied from the results grid or from a CSV file. Schemalyser adds them to what it already knows about the OMOP database, which it calls the core profile, and works out the checklist again.',
  profilePasteLabel: 'The results of the queries for the central OMOP team:',
  readProfilePaste: 'Read the results of the queries for the central OMOP team',
  profilePasteUnreadable:
    'Schemalyser could not read the pasted text as the results of a query for the central OMOP team. Each row needs the six columns that the query returns, from ITEM_CATEGORY to VALUE_05.',
  saveProfile: 'Save the core profile as core-profile.csv',
  saveProfileNote:
    'The file holds what the folder for this audit already recorded about the OMOP database, together with every result that you have pasted. If you put it in the folder for this audit in place of core-profile.csv, the next analysis begins from it.',

  // The tables and columns query, for a folder that holds no list of tables and columns.
  firstHeading: '3.3 The tables and columns query',
  firstWhat: "The folder for this audit holds no list of tables and columns yet, so Schemalyser needs one from the reporting database. The tables and columns query asks SQL Server which columns the tables of the audit's steps have, together with those of any SQL files that you chose, and how many rows each table holds. It reads only SQL Server's own records of its tables, and no row of any table.",
  firstHow: [
    'Choose the database that your SQL window is connected to, just below.',
    'Choose Write the tables and columns query. Schemalyser writes it from the table names in the audit\'s steps and in any SQL files that you chose.',
    'Choose Copy the tables and columns query, paste it into your SQL window and run it.',
    'In SQL Server Management Studio, click the empty square at the top left of the results grid to select all of it, then right-click and choose Copy with Headers.',
    'Paste the result into the box below the query, and choose Read the result of the tables and columns query.',
  ],
  firstShape: 'The result has one line for each column, and ten columns across: the schema, the table, the column, its position in the table, its type, three columns that describe its length and precision, whether it may be empty, and, last, the number of rows in the table, rounded down to the nearest ten.',
  firstUse: 'Schemalyser then uses the result as its list of tables and columns. It reads your SQL files against it, chooses from it how the audit reaches each table, and judges from the numbers of rows which tables are too large to count. A table that does not come back is not visible to this login, and Schemalyser will ask you why. The query holds the table names from your SQL files, so Schemalyser shows it only here and writes it into no file.',
  firstWrite: 'Write the tables and columns query',
  firstNames: (n: number, leftOut: number) =>
    leftOut
      ? `The tables and columns query asks about ${n} tables, which are those that the most files read. Schemalyser left out ${leftOut} more, and you can ask about them later, starting from the list of tables and columns that this meeting saves.`
      : `The tables and columns query asks about the ${n} ${n === 1 ? 'table' : 'tables'} that the audit's steps and any SQL files that you chose read.`,
  firstNone: "Schemalyser found no table name that it could use in the audit's steps or in any SQL files that you chose, so it has not written the tables and columns query.",
  firstCopy: 'Copy the tables and columns query',
  firstPasteLabel: 'The result of the tables and columns query, copied from the results grid with its headers:',
  firstRead: 'Read the result of the tables and columns query',
  firstReadDone: (tables: number, columns: number, sized: number) =>
    `Schemalyser has read the result of the tables and columns query: ${columns} columns of ${tables} tables.${
      sized === 0
        ? ' This login cannot see the sizes of the tables, so Schemalyser will offer no query on a table that could be large, and will ask you instead.'
        : sized < tables
          ? ` SQL Server gave no size for ${tables - sized} of them, as happens for a view, which is a saved query that SQL Server presents as a table, so Schemalyser will offer no query on those that could be large.`
          : ' SQL Server gave the number of rows for every one of them.'
    } This is now the page's list of tables and columns. Schemalyser will use it to read your SQL files, to choose how the audit reaches each table, and to judge which tables are too large to count. A table or column that the audit needs and that did not come back is not visible to this login: it may not exist here, or this login may not be allowed to see it, and Schemalyser will ask you which.`,
  firstReadNext: 'You can now choose Analyse the folder and the SQL files, under 3.4.',
  firstUnreadable:
    'Schemalyser could not read the pasted text as the result of the tables and columns query. Each row needs the ten columns that the query returns, from TABLE_SCHEMA to TABLE_ROWS. Please copy the whole results grid with Copy with Headers, and paste it again.',
  saveState: 'Save what has been settled today',
  // Which database the SQL window is connected to for the meeting, asked before anything is run.
  databaseLegend: 'Before you run anything, choose the database that your SQL window is connected to:',
  databaseWhat:
    "Schemalyser asks this because hospitals keep more than one copy of the reporting database. A training database holds the hospital's real tables, lookup tables and names of charted rows, but its patients are fictional, so its counts and its patterns of charting mean nothing. On a training database, Schemalyser still settles what depends on the tables alone, and it marks every result that depends on the data as to be asked again on the production copy.",
  databaseOptions: [
    ['production', 'The production reporting database, or a refreshed copy of it'],
    ['training', 'A training or play database with fictional patients'],
    ['unsure', 'I am not sure'],
  ] as [string, string][],
  databaseExample:
    "With the invented example loaded, there is no SQL window, and the practice database in step 7 stands in for one. Choose A training or play database with fictional patients, because the example's rows are invented and its counts mean nothing. Schemalyser has chosen it for you.",
  databaseUnsure:
    'Because you are not sure, Schemalyser treats the database as the production reporting database, so that no result is set aside without cause. If you later find that it is a training database, change this answer.',
  databaseHeading: '4.1 The database that your SQL window is connected to',
  databaseChanged: 'If you change this answer, Schemalyser works out the checklist again.',
  databaseRecorded: {
    production: 'The meeting used the production reporting database, or a refreshed copy of it.',
    training: 'The meeting used a training database with fictional patients.',
    unsure: 'The meeting was not sure which database it used, so Schemalyser has treated it as the production reporting database.',
  } as Record<string, string>,
  // The note that the clinician can send before the meeting, as docs/first-ask-note.md gives it.
  noteHeading: 'Show a note that the clinician can send to the colleague before the meeting, describing these steps',
  noteCopy: 'Copy the note',
  firstAskNote: NOTE,

  // Questions that a colleague can answer from knowledge.
  questionsHeading: '4.2 Questions for the colleague',
  questionsWhat: 'These are the questions that Schemalyser hopes the colleague can answer from what they know, gathered into one list that you can copy. Answer each one beside its point under 4.4, where each says why Schemalyser asks it. Where you are not sure, choose Not sure, and Schemalyser offers a short query in its place. Where a question asks whether the audit is right in joining on A = B, it asks whether a row of one table belongs with each row of the other in which B holds the same value as A.',
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
  settingsRecorded: 'Schemalyser has recorded the period, the kinds and the decisions, and worked out the checklist again.',
  factFailed: 'Schemalyser has not been able to record that. The checklist is as it was, and you can try again.',
  working: 'Schemalyser is recording what you have entered and working out the checklist again, which can take several seconds.',
  questionFirst: 'The question for you:',
  // Beside each point in step 4, who can answer it.
  whoAnswers: {
    knowledge: 'The colleague can answer this from what they know.',
    knowledgeOrQuery: 'The colleague can answer this from what they know, or by running the short query.',
    query: 'The colleague can answer this by running the query.',
    team: 'Only the team that looks after the reporting database can answer this, directly or through a SQL file of theirs.',
    clinician: 'The clinician leading the audit answers this after the meeting.',
    omop: 'The central OMOP team answers this.',
  } as Record<string, string>,
  // Beside each question, in one line, why Schemalyser asks it and what the answer changes.
  askWhy: {
    join: 'Schemalyser asks this because the audit links these two tables on these columns, and a wrong link would count the wrong records. If you answer Yes, the point is settled. If you answer No, you can choose the columns that do match, and Schemalyser uses them instead. If you are not sure, Schemalyser offers a short query that measures the link.',
    filter: 'Schemalyser asks this because the audit keeps or leaves out rows by this value. If you answer Yes, the point is settled. If you answer No, the point stays open for the clinician to change the audit\'s steps. If you are not sure, Schemalyser offers a short query that lists the values that the column holds.',
    codes: 'Schemalyser asks this because the audit finds these records by their local codes, which differ from one hospital to the next. The codes that you choose are written into the specification and the reference query.',
    route: 'Schemalyser asks this because the audit would read this table, and it is not visible to this login. If it does not exist here, Schemalyser keeps the other route that it has chosen. If it exists but you cannot see it, the note to the team under 4.8 asks for access. If you are not sure, the point stays open.',
  } as Record<string, string>,
  queryAlternative: 'If neither of you knows the answer, run the short query below, which answers the question instead.',
  groupUnneeded: (n: number) => `Show the ${n} ${n === 1 ? 'point' : 'points'} that this question does not depend on`,
  unneededWhat:
    'The answer to this question does not depend on these points, so they do not count against answering it from the reporting database. They will be needed later, when the audit\'s steps are copied into the hospital\'s OMOP database.',

  // The end of each checklist: the specification and the reference query.
  auditWaiting: "Schemalyser will offer the reference query under 4.10 once nothing above remains to be settled.",
  specHeading: '4.9 The specification of the audit query',
  specCopy: 'Copy the specification',
  specSave: 'Save the specification',
  specWhat: "The specification is for the person who writes the audit query. It sets out, in this hospital's own tables and codes, everything that the meeting has settled: the question, the study period, where each part comes from, how the tables are joined, the local codes, and what remains. Copy it, or save it as a text file. It names this hospital's tables and codes, so it is for use inside the hospital only.",
  checkHeading: 'Checking a query written by hand',
  checkWhat:
    'Schemalyser can run a query written by hand on the synthetic database, with the planted cases, beside the target query, and say whether the two tables agree. Please run python -m schemalyser.target WORLD CONVERSION TARGET.sql --check-query FILE --out FOLDER from the state, which shows only the synthetic results and writes nothing of the query.',
  auditRestructured: "Schemalyser wrote this query from the audit's steps, arranged to start from the anaesthetics of the question. It gives the right answer on the practice database. It is a reference for the person who writes the audit query.",
  generatedHeading: '4.10 The reference query, to read only',
  generatedWarning:
    'Schemalyser could not rearrange this query to start from the audit\'s own anaesthetics, so it is simply every one of the audit\'s steps, one after another, and its header says why. It builds every row of those steps before it keeps the rows of the question, so it must not be run on a large database. It is shown here as a reference for the specification.',
  saveChecks: 'Save the query results as checks.csv',
  saveStateNote: "Choose Save what has been settled today before you close the page. The browser saves a file named schemalyser-state.zip, which holds everything settled today: the list of tables and columns, the results that you pasted, your answers and the codes that you chose. Unzip it into the folder for this audit, replacing any files of the same name, and keep that folder on the hospital's network. At the next meeting, choose that folder in step 3, and nothing will be asked twice.",
  // The two stages of each checklist.
  releaseHeading: 'For the later OMOP release',
  releaseWhat:
    "These points matter only later, when the audit's steps are copied into the hospital's OMOP database, which holds the hospital's records in OMOP, a standard layout for research databases. This page calls that database the core. They do not count against answering the question from the reporting database.",
  auditHeading: '4.10 The reference query',
  auditWhat: "This is the audit question itself, written by Schemalyser as one query over the hospital's tables. It is a reference for the person who writes the audit query, who can copy it or save it below. Before any query like it is run on a large database, choose a study period, run it on the reporting copy rather than the live system, and run it out of hours.",
  auditTables: (tables: { name: string; rows: number | null }[]) =>
    `It reads ${tables.map((t) => (t.rows === null ? `${t.name}, whose size is not known` : `${t.name}, which holds about ${t.rows.toLocaleString('en-AU')} rows`)).join('; ')}.`,
  auditCost:
    'This is the one query that reads the large tables through its own joins, so how long it takes depends on how the reporting database is set up, and cannot be known in advance. It reads each table WITH (NOLOCK), which means that it neither waits for nor holds up other people\'s work on the same rows. SQL Server still stops anyone changing the design of those tables while it runs, so it must not run during the nightly load, when the reporting database is refreshed.',
  auditCounts: "It returns only counts, and no row for any one record. It leaves blank any count from 1 to 4, unless the audit's approval allows exact small numbers. This differs from the queries of the meeting, which leave blank any count under ten.",
  auditRows:
    'It returns a row for each record that it finds, so its result holds patient-level data and belongs under the approval of the audit itself.',
  auditSave: 'Save the reference query',
  auditCopy: 'Copy the reference query',
  saveChecksNote:
    'The file holds the query results that the folder for this audit already held, together with every result that you have pasted. If you put it in the folder for this audit in place of checks.csv, the next analysis begins from it.',

  // Adding SQL files and analysing again.
  addHeading: 'Add more of your team\'s SQL files',
  addWhat: 'If you find one of your team\'s SQL files that does what a point describes, add it here and choose Analyse again. Schemalyser keeps the files that you have already chosen and the answers that you have given.',
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
    'The list of tables and columns has no column headers, so Schemalyser has assumed that its columns are in the order of the query in step 1.',
  unreadHeading: 'What Schemalyser could not read',
  readBeforeDownload:
    'The inventory below, which lists the tables, columns and joins that your SQL files use, is everything that Schemalyser will write, together with the checklists in the previous step. Please read it before you download it.',
  namesOnly:
    'Every name in the inventory should be a table or a column from your list of tables and columns. Where a SQL file contained a value, such as a date or a record number, Schemalyser has written a placeholder such as <string> or <number> in its place, or left it blank. The filters file keeps a value only where the query results already list it as a code of that column.',
  doNotDownload: 'If you see anything that should not leave your team, please clear the inventory and do not download it.',
  showExactly: 'Show this file exactly as Schemalyser will write it',
  indexNote:
    'Schemalyser refers to each SQL file by a number. This list shows which file each number refers to, and Schemalyser does not include the list in the inventory.',
  checksNoHeaders:
    'The file of query results has no column headers, so Schemalyser has assumed that its columns are in the order in which the check script writes them.',
  checkScriptWhat:
    'The checklist gives, beside each point, the short queries that it needs. If your team prefers to run every check in one go, the check script, which is one long script of the same kind of short queries, asks the reporting database what the SQL files and the audit\'s steps leave open: how large each table is, which columns used in joins hold a different value in every row, which values the filtered columns hold, how far apart pairs of dates fall, and how many rows share each value of a column used in joins.',
  checkScriptSafe:
    'The script reads the reporting database and changes nothing in it. It rounds every count down to the nearest ten, and it lists a value only when at least ten rows hold it.',
  checkScriptHow:
    'Please run the script in SQL Server Management Studio and save the results as a CSV file. You can then paste the results into the box for the results of the short queries in step 4, or begin again from step 3 and choose that file under Other files.',
  downloadCheckScript: 'Download the check script',
  usesChecks: 'The practice database uses the values and the table sizes from your query results.',
  runsChosenRequests: 'Schemalyser will run the SQL files that you chose in step 3.',
  openSandbox: 'If you have only an inventory file, you can open the sandbox, which is the practice database of step 7 on a page of its own.',
  openFirstPage: 'To make an inventory from your requests, you can open the first page.',
  // Step 7, the practice database, in this page's own names.
  practiceBuildWhat:
    'Schemalyser builds one table for each table in the inventory, with every column that the list of tables and columns gives for it. It fills the tables with invented rows, and it gives joined columns matching values, so that the joins in your SQL files find rows.',
  practiceRows: 'Rows in each table:',
  practiceBuild: 'Build the practice database',
  practiceBuilding: 'Schemalyser is building the practice database.',
  practiceInvented: 'Every value in the practice database is invented. A result from it says nothing about real patients.',
  practiceQuery: 'Write a query in T-SQL, which is the form of SQL that SQL Server uses, or paste one. Schemalyser will translate it and run it on the practice database, which answers as SQL Server would, with whole numbers where SQL Server gives them. An empty value shows as NULL.',
  practiceRun: 'Run the query',
  practiceCopyResult: 'Copy the result with headers',
  practiceCopyPart: (shown: number) => `The copy holds the first ${shown} rows, which are the rows shown here.`,
  practiceFromPoint: (point: string) => `This is the query that step 4 offers with the point that reads: ${point} Paste its result back there, in the box that the point names.`,
  practiceFromPart: (heading: string) => `This is the query that step 4 offers under ${heading}. Paste its result back there, in the box that the part names.`,
  practiceBack: 'Go back to that point in step 4.',
  practiceRunFiles: 'Run the SQL files',
  download: 'Download the inventory and the checklists',
  downloadHolds:
    'The download holds the inventory, and a folder named boundary that holds the checklists, Schemalyser\'s account of how ready each is, the list of open questions, a summary (summary.md) and a record of which files were read (provenance.json), exactly as Schemalyser\'s command-line tool writes them.',
  showSummary: 'Show summary.md exactly as Schemalyser will write it',
  clear: 'Clear everything',

  reconnected:
    'This page has gone back online, so Schemalyser has stopped its analysis engine and discarded what it had read from your files, and kept only the inventory and the checklists. You can still download them, or you can clear them. To carry on, choose Begin a new analysis, wait for Schemalyser to load again, take the page offline again and choose the files again, with the folder for this audit into which you unzipped the saved file, if you saved one.',
  lockedHere:
    'This page went back online before Schemalyser had finished, so Schemalyser has stopped its analysis engine and has not recorded this. Step 2 says how to carry on.',
  lockedPressed:
    'This page has gone back online, so Schemalyser has stopped its analysis engine and cannot record this. Step 2 says how to carry on.',
  reconnectedNoInventory:
    'This page has gone back online, so Schemalyser has stopped its analysis engine and discarded what it had read from your files. Schemalyser had not finished the inventory, so there is nothing to download. To carry on, reload the page while it is online, take it offline again and choose the files again.',

  safeguardsHeading: 'What Schemalyser does with your files',
  safeguards: [
    'Schemalyser reads the files on this computer. It does not send them, or anything taken from them, to any other computer.',
    `Schemalyser will not accept files while this page is online. ${ONLINE_AGAIN}`,
    'Schemalyser writes only names that it finds in your list of tables and columns and in the audit\'s steps, together with counts. It does not write comments, the short names that a query gives its tables, or the names of your SQL files. It writes a value from a SQL file only where the query results already list it as a code of the column that the file compares it with.',
    'Schemalyser shows you everything it has written before you download it.',
  ],
  checkYourself:
    "You can confirm that Schemalyser sends nothing by opening your browser's developer tools and watching the Network panel while it works.",

  offlineHowSummary: 'How to take this page offline',
  policySummary: 'How this page checks that nothing can leave it',
  githubSummary: 'Fetch the files from GitHub instead (not needed in a meeting)',
  catalogueSummary: 'The query for the full list of tables and columns, which most meetings do not need',
  // The tally at the head of a checklist, by who can settle each point, as the list at the end groups them.
  needs: (you: number, team: number, clinician: number, again = 0) => {
    const total = you + team + clinician + again;
    if (!total) return 'Schemalyser needs nothing more for this audit question.';
    const parts = [
      you ? `${you} for you now` : '',
      team ? `${team} for the team that looks after the reporting database` : '',
      clinician ? `${clinician} for the clinician after the meeting` : '',
      again ? `${again} to ask again on the production copy` : '',
    ].filter(Boolean);
    const list = parts.length > 1 ? `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}` : parts[0];
    return `Schemalyser needs ${total} more ${total === 1 ? 'thing' : 'things'} for this audit question: ${list}.`;
  },
  readyNow: (lessCertain: number) =>
    `Everything that the audit query must rest on is settled, so the query can be written now.${
      lessCertain ? ` ${lessCertain === 1 ? 'One further point is' : `${lessCertain} further points are`} less certain. The query does not wait for ${lessCertain === 1 ? 'it' : 'them'}, and the specification lists ${lessCertain === 1 ? 'it' : 'them'}.` : ''
    }`,
  readyTraining: (again: number) =>
    `Everything that can be settled on a training database is settled, so the audit query can be drafted now. ${again === 1 ? 'One point is' : `${again} points are`} still to be asked again on the production copy, and nobody should rely on a result until ${again === 1 ? 'it has' : 'they have'} been.`,
  notReadyYet: 'Some of what the audit query must rest on is not yet settled. The list under 4.8 says what remains and who can settle it.',
  notSure: 'Not sure',
  routeAbsent: 'It does not exist here',
  routeHidden: 'It exists, but I cannot see it',
  searchAbove: 'The name search with the point above also finds these codes.',
  searchPasteLabel: 'The result of the name search, copied from the results grid with its headers:',
  searchRead: 'Show the names',
  searchPrivate: "The names that come back are the hospital's own. Schemalyser shows them only on this page, and keeps only the codes that you choose, in the file that you save at the end.",
  searchNone: 'Schemalyser could not read any code and name in the pasted text. Each row needs the two columns that the name search returns, code and name.',
  searchName: 'Name',
  searchCode: 'Code',
  searchChoice: 'What it is',
  searchNeither: 'Neither',
  searchSave: 'Save the choices',
  searchTooMany: (n: number, shown: number) =>
    `The name search returned ${n.toLocaleString('en-AU')} rows, which is more than this page can sensibly list, so Schemalyser shows only the first ${shown} below. Narrow the words above, write the search again, and run it once more, so that every row it returns can be seen and chosen.`,
  // Each query that reaches the table of readings is a short script that starts from a temporary table of the cohort.
  scriptTemporary: "The script works in two parts. Part 1 makes one temporary table, called #cohort, which is a small table that exists only in your own SQL window and disappears when you close that window. #cohort holds only the identifying numbers of the audit's anaesthetics and of their records. Part 2 then fetches the readings of those anaesthetics and no others. The script creates or changes nothing else.",
  scriptTimeout: 'Before you run anything here, set a time limit, so that a query that runs too long stops by itself. In SQL Server Management Studio, with your query window open, open the Query menu, choose Query Options, then Execution, and enter a number of seconds, such as 300, in Execution time-out; Management Studio sets no limit unless you enter one. Then select part 1 of the script, from its first line down to the line that begins -- Part 2, and run only that selection, because SQL Server can show the plan of part 2 only once #cohort exists.',
  scriptPlan: (readings?: [string, number | null] | null) =>
    `These instructions are for the colleague who runs the SQL. Next, select part 2 and press Ctrl+L. SQL Server then shows the estimated plan, which is its description of how it intends to run the query, drawn as boxes joined by arrows, and it runs nothing. Find each box that names ${readingsWords(readings)}. A box that reads Index Seek or Clustered Index Seek means that SQL Server goes straight to the rows that it needs, which is what you want. A box that reads Table Scan, Index Scan or Clustered Index Scan means that SQL Server would read the whole table. A box that reads Hash Match gathers everything that flows into it before it matches anything, so an arrow from the table of readings into a Hash Match means that the whole table would be read as well. If the table of readings appears only in Seek boxes, you can run part 2. If any box that names it reads Scan, or it has an arrow into a Hash Match, do not run part 2: right-click the plan, choose Save Execution Plan As, and give the file to the clinician leading the audit, who will take it to the team that looks after the reporting database. If you cannot follow these instructions, do not run part 2, and hand the script and the plan to the clinician leading the audit instead.`,
  scriptCostly: (table: string, rows: number) =>
    `Part 1, like the count by year, reads ${table} to find the anaesthetics. ${table} holds about ${rows.toLocaleString('en-AU')} rows, ${sizeWords(rows)}${
      rows < 100_000 ? ', so part 1 should finish quickly' : rows < 10_000_000 ? ', so part 1 may take a minute or more' : ', so part 1 can be slow'
    }. ${costTail}`,
  scriptMonth: 'Where the period is longer than a month, run the script first for one month: change the two dates in part 1 to the first month of the period, and run the whole period only once that month has finished quickly.',
  scriptWorst: (worst: string) => `The count by year shows ${worst}, and the script asks only for the readings of those anaesthetics.`,
  scriptTraining: 'A script that runs quickly on a small training database can still run slowly or read far too much on the production copy, so follow these notes there as well.',
  countCostly: (table: string, rows: number) =>
    `Of the tables that the count reads, the largest is ${table}, which holds about ${rows.toLocaleString('en-AU')} rows, ${sizeWords(rows)}${
      rows < 100_000 ? ', so the count should finish quickly' : rows < 10_000_000 ? ', so the count may take a minute or more' : ', so the count can be slow'
    }. ${costTail}`,
  // The count by year reads none of the readings, as the sentence above its query says with the tables that it reads, so it
  // needs only a time limit; the check of the estimated plan is for the scripts under 4.5 and 4.7, which do read them.
  countPlan: (_readings?: [string, number | null] | null) => 'Before you run the count, set a time limit: in SQL Server Management Studio, with your query window open, open the Query menu, choose Query Options, then Execution, and enter a number of seconds, such as 300, in Execution time-out. The count reads none of the charted readings, so it needs no check of its estimated plan. That check is for the scripts under 4.5 and 4.7, which do read the readings, and each of them says how to make it.',
  countHow: "Once it has run, select the whole results grid, copy it with its headers, and paste it into the box below, rather than into the box for the results of the short queries. The count returns one row for each year, with the anaesthetics that started in that year, those in the audit's cohort, which is the group of anaesthetics that the audit question is about, and those with no kind of anaesthetic recorded, each rounded down to the nearest ten. A count under ten comes back blank, as NULL, which is SQL's word for an empty value, and Schemalyser shows it as under 10. Schemalyser shows the numbers as a table, asks whether they look right, and then offers the list of what is charted, under 4.5.",
  auditReferenceOnly: 'This query is shown as a reference for the person who writes the audit query, and it must not be run as it stands on the hospital\'s databases.',
  listedHeading: '4.5 The list of what is charted on the audit\'s anaesthetics',
  listedWhat: (column: string, year: number) =>
    `This list is for choosing the local codes that the audit needs, from what was actually charted at this hospital. The script lists every code of ${column} that was charted on the audit's anaesthetics that started in ${year}, with how often each was charted and its names, most charted first. The notes below say how to run it safely, and the comment lines at its top say which tables it reads. Once it has run, copy the whole results grid with its headers, paste it into the box below the script, choose Show the list, and mark the rows that matter. A row that you leave unmarked is simply not chosen. A code that the hospital stopped using appears only in the years in which it was used, so work through the study period a year at a time: choose each year in turn, run the list again and mark it. Schemalyser remembers what you have marked in each year, and saving records the marks of every year.`,
  listedYear: 'The year to list:',
  listedPasteLabel: 'The result of the list of what is charted, copied from the results grid with its headers:',
  listedRead: 'Show the list',
  listedNone: 'Schemalyser could not read any code in the pasted text. Each row needs the code, the two counts and the names that the list of what is charted returns.',
  listedFilter: 'Type to show only the rows that hold these words',
  listedReadings: 'Readings',
  listedAnaesthetics: 'Anaesthetics',
  listedTraining:
    "This list comes from a training database with fictional patients. What is charted there reflects training and not practice, so rows may be missing or oddly frequent. You can still choose the codes from it, because training uses the hospital's real rows, but the choices should be confirmed again on the production copy.",
  listedReadingsTraining: 'Readings in training',
  listedAnaestheticsTraining: 'Anaesthetics in training',
  listedNotChosen: 'Not chosen',
  listedCalculated: 'A mean calculated from systolic and diastolic, which the audit leaves out',
  listedLikely: '(likely)',
  listedFound: (n: number, likely: number, year: number) =>
    `Schemalyser has read the result of the list of what is charted for ${year}. It holds ${n.toLocaleString('en-AU')} ${n === 1 ? 'code' : 'codes'}, and ${likely} of them ${likely === 1 ? 'has' : 'have'} a name that holds a word for the meanings sought, marked as likely. Mark each row that matters, then choose Save the choices. Schemalyser records the codes that you choose for each meaning, and writes them into the specification and the reference query.`,
  listedKept: (n: number, year: number) =>
    n ? `Schemalyser has kept the choices from the list of what is charted for ${year}, which held ${n.toLocaleString('en-AU')} ${n === 1 ? 'code' : 'codes'}.`
      : `The list of what is charted for ${year} came back empty. The list of what remains, under 4.8, says what to check first.`,
  listedAfterCount: 'Once you have seen the count by year, Schemalyser offers the list of what is charted on the audit\'s anaesthetics in one year, under 4.5, with their counts and names, and the codes for this meaning are chosen from it.',
  listedInstead: 'The codes for this meaning are chosen from the list of what is charted on the audit\'s anaesthetics, under 4.5.',
  chartedHeading: '4.7 The count of the chosen codes',
  chartedWhat: (from: string, to: string) =>
    `This script is optional, and it is for checking that the codes that you chose are charted about as often as you would expect. It counts, for the chosen codes only, how often each was charted on the audit's anaesthetics that started from ${from} to ${to}, the last year of the study period, and on how many of those anaesthetics. Like the list of what is charted, it works in two parts and fetches only the readings of those anaesthetics, and the notes below say how to run it safely. Paste its result into the box below the script, and Schemalyser carries the counts into the specification.`,
  chartedPasteLabel: 'The result of the count of the chosen codes, copied from the results grid with its headers:',
  chartedRead: 'Keep the counts',
  chartedNone: 'Schemalyser could not read any code in the pasted text. Each row needs the three columns that the count of the chosen codes returns: code, readings and anaesthetics.',
  chartedKept: 'Schemalyser has kept the result of the count of the chosen codes for this period, and the specification lists the counts with the chosen codes. If the codes or the period change, Schemalyser offers the count again.',
  searchFound: (n: number) => `Schemalyser has read the result of the name search, which found ${n} ${n === 1 ? 'row' : 'rows'}. For each, choose what it is, then choose Save the choices. Several rows may have the same meaning, and most will be neither.`,
  settingsHeading: '4.6 The study period, the kinds of anaesthetic and the decisions for the clinicians',
  settingsWhat: 'This part sets what the audit counts. The study period applies to the start of each anaesthetic, and the count by year fills in its first year for you. Without a period, the audit counts every anaesthetic on record, and without a kind chosen, every kind counts. Make your choices, then choose Apply the period, the kinds and the decisions, at the end of this part. Schemalyser carries them into the specification and the reference query.',
  settingsFrom: 'The first day of the study period:',
  settingsTo: 'The last day of the study period:',
  settingsKinds: 'Count only these kinds of anaesthetic (none ticked means every kind, including sedation and procedures at the bedside):',
  settingsApply: 'Apply the period, the kinds and the decisions',
  endingHeading: '4.8 Where this audit question stands',
  endingWhat: 'This part is for the end of the meeting. It says what is settled and what remains, grouped by who can settle each point, and it is where you save what has been settled today.',
  endingSettled: (n: number) => `${n} ${n === 1 ? 'thing is' : 'things are'} settled.`,
  endingRemaining: 'These remain, grouped by who can settle them.',
  endingYouHeading: 'You can settle these now, in the meeting:',
  endingTeamHeading: 'The team that looks after the reporting database can settle these:',
  endingAgainHeading:
    'These are to be asked again on the production copy, because their results came from a training database. For each, run the query again on the production copy and paste the result:',
  endingClinicianHeading: 'The clinician settles these after the meeting, in the folder for this audit, as each point says: by recording what each code means where the two of you could not choose, or by changing the audit\'s steps or the reference query:',
  remainingRose: (n: number, added: string[]) =>
    `That answer leaves ${n} more ${n === 1 ? 'thing' : 'things'} to settle than before, because Schemalyser has learned something new: ${added.join(' ')}`,
  endingByQuestion: 'You can answer the question with it above.',
  endingChooseAgain: 'You can choose it again in the list of what is charted, under 4.5, where its count stands beside it.',
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
      ? 'What you have just entered settles 1 point, and Schemalyser lists it first.'
      : `What you have just entered settles ${n} points, and Schemalyser lists them first.`,
  groupNewNow: 'Settled just now',
  settingsBeforeCount: (from: number, first: number) =>
    `The study period begins in ${from}, but the count shows records only from ${first}. Anaesthetics before ${first} are not in this database, so the audit cannot count them.`,
  firstDoubt: {
    most: 'Most of the tables that the tables and columns query asked about did not come back. Your SQL window may be connected to the wrong database, or your login may be allowed to see only some tables. Before you go on, check which database the SQL window is connected to: in SQL Server Management Studio, its name shows in the drop-down list on the toolbar. If it is the wrong one, choose the reporting database there and run the tables and columns query again.',
    lookups: 'None of the tables that hold the names of codes, which the site rules list, came back from the tables and columns query. Your SQL window may be connected to the wrong database, or your login may be allowed to see only some tables. Before you go on, check which database the SQL window is connected to: in SQL Server Management Studio, its name shows in the drop-down list on the toolbar. If it is the wrong one, choose the reporting database there and run the tables and columns query again.',
  } as Record<string, string>,
  endingLessCertain: (n: number, later = 0, now = 0) => {
    const one = n === 1;
    const where =
      later === n ? ` Under 4.4, ${one ? 'it is' : 'they are'} among the other points that are folded away.`
        : now === n ? ` Under 4.4, ${one ? 'it is' : 'they are'} among the points that need the colleague now.`
          : later && now ? ` Under 4.4, ${later} of them ${later === 1 ? 'is' : 'are'} among the other points that are folded away, and ${now} ${now === 1 ? 'is' : 'are'} among the points that need the colleague now.`
            : '';
    return `${n} further ${one ? 'point is' : 'points are'} less certain. The audit query does not wait for ${one ? 'it' : 'them'}, and the specification lists ${one ? 'it' : 'them'}.${where}`;
  },
  endingSave: "Before you close the page, choose Save what has been settled today, below. The browser saves a file named schemalyser-state.zip. Unzip it into the folder for this audit, replacing any files of the same name, and keep that folder on the hospital's network. At the next meeting, choose that folder in step 3, and nothing will be asked twice.",
  specWhatOpen: "The specification is for the person who writes the audit query. It sets out, in this hospital's own tables and codes, everything that the meeting has settled: the question, the study period, where each part comes from, how the tables are joined, the local codes, and what remains. Some points are not yet settled, and it shows each as an assumption. Copy it, or save it as a text file. It names this hospital's tables and codes, so it is for use inside the hospital only.",
  itemMore: 'More about this point',
  groupYou: '4.4 The points that need the colleague now',
  groupYouNote: 'Each point below says what Schemalyser still needs, with a question beside it, a short query, or both. Answer the question beside it. Where a short query is offered, run it and paste its result into the box for the results of the short queries, near the top of this step. Where you are not sure of an answer, choose Not sure, and Schemalyser offers a short query that settles it instead. Several questions ask whether the audit joins two tables on the right columns, and you can answer Yes if your team\'s SQL joins them on the same columns.',
  groupYouNone: 'Nothing here needs the colleague now.',
  groupLater: (n: number, lessCertain = 0) =>
    `Show the ${n} other ${n === 1 ? 'point' : 'points'}, which wait for the team's SQL, the clinician, or the central OMOP team, which runs the hospital's main OMOP database${
      lessCertain ? `. ${lessCertain === n ? (n === 1 ? 'It is' : 'All of them are') : `${lessCertain} of them ${lessCertain === 1 ? 'is' : 'are'}`} among the less certain points that 4.8 counts, which the audit query does not wait for` : ''
    }`,
  firstMissing: (names: string[]) =>
    `The tables and columns query asked about ${names.length} ${names.length === 1 ? 'table' : 'tables'} that did not come back: ${names.join(', ')}.`,
  auditNeedsPeriod: 'Schemalyser will offer the reference query under 4.10 once a study period has been entered under 4.6, because without one it would read every anaesthetic on record.',
  searchWords: 'The words that the name search looks for, separated by semicolons:',
  searchRewrite: 'Write the name search again with these words',
  searchText: 'The blood pressure as text, from which the reference query does not read a mean',
  countPasteLabel: 'The result of the count by year, copied from the results grid with its headers:',
  countRead: 'Read the result of the count by year',
  countReceived: (n: number, first: number, last: number) =>
    `Schemalyser has read the result of the count by year, which covers ${n} ${n === 1 ? 'year' : 'years'}, from ${first} to ${last}. The table below shows it as it came back.`,
  countNone: 'Schemalyser could not read any year in the pasted text. Each row needs the year and the counts that the count by year returns.',
  countYear: 'Year',
  countAll: 'Anaesthetics',
  countCohort: 'In the cohort',
  countUnderTen: 'under 10',
  countAsk: "Schemalyser asks whether these numbers look right, because numbers far from the hospital's usual workload would mean that the audit is finding the wrong anaesthetics. The point stays open until you choose About right. Compare the numbers with the usual workload, then choose one:",
  countNoKind: 'No kind recorded',
  countEmpty: "The count found no anaesthetic at all. Compare this with the hospital's usual workload, then choose one:",
  countTraining:
    'This is a training database with fictional patients, so these numbers mean nothing, and Schemalyser does not ask whether they look right. The count shows only whether the audit finds any records at all. Keep the counts, and Schemalyser marks the count to be asked again on the production copy.',
  countKeep: 'Keep the counts',
  countRight: 'About right',
  countFew: 'Too few',
  countMany: 'Too many',
  decisionsHeading: 'Decisions for the clinicians',
  decisionsWhat: 'The audit makes these clinical choices, each with the rule now in force chosen already. Make each one together, and add a short note if you wish. The notes are kept only in the file that you save. Each decision is carried into the specification, and the list of what remains says which ones the reference query does not yet apply.',
  decisionNote: 'A short note, if you wish',
  decisionRecorded: 'The reference query does not yet apply this decision. If you choose a change, the list of what remains says what would make it real.',
  decisionsNone:
    'None of the decisions for the clinicians can change the answer to this audit question, because it reads no charted reading and no age in days, so Schemalyser does not offer them here.',
  decisionsLeftOut: (topics: string[]) =>
    `The other ${topics.length === 1 ? 'decision' : 'decisions'}, about ${topics.length > 1 ? `${topics.slice(0, -1).join(', ')} and ${topics[topics.length - 1]}` : topics[0]}, cannot change the answer to this audit question, so Schemalyser does not offer ${topics.length === 1 ? 'it' : 'them'} here.`,
  decisionTopics: {
    pressures: 'which pressures count once an arterial line is running',
    floor: 'a floor for the mean pressure',
    ceiling: 'a ceiling for the mean pressure',
    isolated: 'a single isolated low reading',
    bypass: 'time on bypass',
    ecmo: 'time on ECMO',
    age: 'the limit of age',
  } as Record<string, string>,
  codeAssumed: (meaning: string) =>
    `The folder for this audit assumes that this code means ${meaning}, and nobody here has yet confirmed it.`,
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
  // The glossary at the end of the page: every term that the page explains, so that an explanation can be found again.
  glossarySummary: 'The words that this page uses, and what each one means',
  glossaryTop: 'The words that this page uses are explained at the end of the page, under The words that this page uses.',
  glossary: [
    ['The reporting database', "The copy of the hospital's records that SQL Server holds for reporting. The colleague's SQL window reads it, and the team that looks after it, usually the hospital's data or reporting team, can grant access and answer questions about its tables."],
    ['The production copy, and a training database', 'The production copy holds the real patients, or a refreshed copy of them. A training or play database has the same tables but fictional patients, so its counts mean nothing. Schemalyser asks which one your SQL window is connected to.'],
    ['The SQL window', 'A program such as SQL Server Management Studio, connected to the reporting database, in which the colleague runs each query that this page writes.'],
    ['The results grid, and Copy with Headers', 'The table in which SQL Server Management Studio shows the result of a query. Clicking the empty square at its top left selects all of it, and right-clicking and choosing Copy with Headers copies it with its column names, ready to paste into this page.'],
    ['The folder for this audit', "The folder that the clinician prepared with Schemalyser before the meeting and brings on a USB stick or by email. It holds the audit's steps, the audit question written as a query, the site rules and, after a meeting, the list of tables and columns and the answers given so far."],
    ["The audit's steps", "The queries, in the folder for this audit, that say how the hospital's tables are read to answer the audit question."],
    ['The site rules', 'A file, site-rules.json, that tells Schemalyser how this hospital names its tables and codes.'],
    ['The SQL files', 'Queries that the colleague or their team already use with this database, saved as .sql files. Schemalyser learns from them how the tables are joined and filtered, and writes out the names of tables and columns, with a value only where the query results already list it as a code of that column.'],
    ['The list of tables and columns', 'The list of the columns of each table that the audit needs, with the number of rows in each table. It comes from the tables and columns query, or from the folder for this audit once a meeting has saved it.'],
    ['The tables and columns query', "The query, under 3.3, that asks SQL Server which columns the audit's tables have and how many rows each holds. It reads only SQL Server's own records, and no row of any table."],
    ['The query for the full list of tables and columns', 'A longer query, folded away in step 1, that lists every table and column without sizes. Most meetings do not need it.'],
    ['The table sizes query', "A query, under 4.3, that reads the number of rows in each table from SQL Server's own records. Schemalyser offers it only when it does not yet know a table's size."],
    ['A short query', 'A small counting query beside a point of the checklist. It reads the reporting database, changes nothing, and returns only names and counts rounded down to the nearest ten. Its result goes into the box for the results of the short queries.'],
    ['Not visible to this login', 'A table or column that the audit needs did not come back from the tables and columns query. Either it does not exist in this database, or this login is not allowed to see it.'],
    ['Rounded down to the nearest ten, and small counts', "Every count that the meeting's queries return, from the short queries to the count of the chosen codes, is rounded down to the nearest ten, and a count under ten is left blank. This reduces what a count can disclose, but it does not make the results anonymous, and repeated counts over slightly different groups can reveal more than one count does, so the results are for use inside the hospital until the hospital's own rules say otherwise. The audit query's own result follows a different rule: it leaves blank any count from 1 to 4, unless the audit's approval allows exact small numbers, as section 7 of the specification says."],
    ['WITH (NOLOCK), and the nightly load', "WITH (NOLOCK) means that a query neither waits for nor holds up other people's work on the same rows. SQL Server still stops anyone changing the design of a table while the query runs, so no query should run during the nightly load, which is when the reporting database is refreshed from the hospital's systems, usually overnight. The team that looks after the reporting database can tell you its hours."],
    ['NULL', "SQL's word for an empty value. A count that a query leaves blank because it is under ten comes back as NULL, and Schemalyser reads it as under 10."],
    ['A CSV file', 'A plain text file with one row to a line and its values separated by commas, which SQL Server Management Studio can save from a results grid.'],
    ['A join, and joining on two columns', 'A join is the way in which a query links the rows of two tables. Joining on A = B means that a row of one table is linked to each row of the other in which B holds the same value as A.'],
    ['TABLE.COLUMN', "The way in which the page names a column: the table's name, a full stop, and the column's name."],
    ['T-SQL', 'The form of SQL that SQL Server uses, in which every query on this page is written.'],
    ["The audit's cohort", 'The group of anaesthetics that the audit question is about, such as the anaesthetics of infants under 28 days.'],
    ['The count by year', "A query, offered beside its point under 4.4, that counts the anaesthetics that started in each year, those in the audit's cohort and those with no kind of anaesthetic recorded. It shows from which year records exist and whether the audit finds the right anaesthetics."],
    ['The list of what is charted', "A script, under 4.5, that lists the codes charted on the audit's anaesthetics in one year, with their counts and names, so that the two of you can choose the codes that the audit needs."],
    ['Local codes', "The hospital's own numbers for each kind of charted reading, such as a mean arterial pressure. They differ from one hospital to the next, so they are chosen from what is charted here."],
    ['A mapping row', 'One line in the folder for this audit that says what one local code means. A mapping row that came with the folder and that nobody here has confirmed is an assumption, and the page says so wherever it shows the code.'],
    ['The name search', 'A query on the table that names the codes, which finds the codes whose names hold the words given. The page offers it where the list of what is charted cannot be written.'],
    ['The count of the chosen codes', 'An optional script, under 4.7, that counts how often each chosen code was charted on the audit\'s anaesthetics in the last year of the study period.'],
    ['A two-part script, and #cohort', "A query that reaches the table of readings, which is usually the largest table in the reporting database, is offered as a script in two parts. Part 1 puts the audit's anaesthetics into a small temporary table, #cohort, that exists only in your own SQL window. Part 2 then fetches the readings of those anaesthetics and no others."],
    ['The estimated plan', "SQL Server's description of how it intends to run a query, shown by pressing Ctrl+L, without running anything."],
    ['Index Seek, Index Scan and Hash Match', 'Boxes in the estimated plan. An Index Seek goes straight to the rows that the query needs. A Scan reads the whole table. A Hash Match gathers everything that flows into it before it matches anything, so a table that flows into one is read in full. The colleague who runs the SQL checks these before running part 2 of a script, and if they cannot, hands the script and the plan to the clinician leading the audit.'],
    ['The time limit', 'The number of seconds, set under Query Options in SQL Server Management Studio, after which a query stops by itself.'],
    ['The study period, and the kinds of anaesthetic', 'The dates between which an anaesthetic must start to be counted, and the kinds of anaesthetic that the audit counts, set under 4.6.'],
    ['The specification', 'A page of plain text, under 4.9, for the person who writes the audit query. It sets out, in the hospital\'s own tables and codes, everything that the meeting settled, and shows each open point as an assumption.'],
    ['The reference query, and the audit query', 'The reference query is the audit question written by Schemalyser as one query, offered under 4.10 once nothing remains. The audit query is the one that the person who writes it runs, using the specification and the reference query as guides.'],
    ['What remains', 'The points under 4.8 that are not yet settled, grouped by who can settle each: the colleague now, the team that looks after the reporting database, or the clinician after the meeting.'],
    ['The saved file', 'The file schemalyser-state.zip that Save what has been settled today writes. Unzipped into the folder for this audit, it lets the next meeting begin where this one ended.'],
    ['The inventory', 'The list of the tables, columns, joins and filters that the SQL files use, shown in step 5 and downloaded in step 6. It holds names and counts only.'],
    ['The check script', 'One long script, downloaded in step 6, that holds every short query, for a team that would rather run them all at once.'],
    ['OMOP, and the later OMOP release', 'OMOP is a standard layout for research databases. The points for the later OMOP release matter only when the audit\'s steps are copied into the hospital\'s OMOP database, and they do not hold up this audit.'],
    ['The central OMOP team', "The team that runs the hospital's main OMOP database. It runs the queries in the section for the central OMOP team, under the later OMOP release, and returns their results to the clinician."],
    ['The core profile', 'What Schemalyser knows about the hospital\'s main OMOP database, from the results of the queries that the central OMOP team runs.'],
    ['Offline', 'A browser tab that cannot reach the network. This page works only offline, so that nothing that it reads can leave it.'],
    ['The invented example', 'A made-up folder for an audit and fifteen made-up SQL files, served with the page, for trying the whole page before a meeting.'],
    ['The practice database', 'A small database of invented rows that step 7 builds in this browser, for trying a query without touching the hospital\'s database. It answers as SQL Server would, so it can stand in for a SQL window when you try the invented example.'],
    ['The sandbox', 'The practice database of step 7 on a page of its own, which can be opened with an inventory file alone.'],
    ['Parse', 'To read a piece of text as SQL, statement by statement. A part that Schemalyser could not parse is a part that it could not read as SQL.'],
    ['A concept, and its number', "A meaning in the standard vocabulary that OMOP uses, such as a general anaesthetic, with a number of its own. The page gives the plain name first and then, in smaller type, the vocabulary's own name and the concept's number. The vocabulary's names use American spelling, such as anesthesia."],
    ['The check results, or the query results', 'The results of the short queries that Schemalyser holds: those that came with the folder for this audit, and those pasted on this page. They are saved as checks.csv.'],
    ['Open, Partly settled and Settled', 'The state of each point in the checklist. An open point has nothing yet that settles it. A partly settled point, also called partly answered, has some of what it needs. A settled point needs nothing more.'],
    ['An assumption', "What the folder for this audit takes to be true and nobody here has yet confirmed, such as the meaning of a code. The queries mark each one in a comment, such as /* assumption 3 */, and the specification lists them."],
    ['dbo', 'The usual schema in SQL Server, that is, the usual group in which a database keeps its tables. A name such as [dbo].[VISIT] means the table VISIT in that group.'],
    ['TABLE_SCHEMA', 'The column, in the result of the tables and columns query, that gives the schema of each table, which is usually dbo.'],
    ['A temporary table', 'A table, whose name begins with #, that exists only in your own SQL window and disappears when you close that window.'],
    ['Reconcile ten to twenty anaesthetics against their charts', "To take ten to twenty of the anaesthetics that the audit query finds and check each one by hand against the patient's chart, so that the clinician can see that the query counts what it should."],
    ['An access token', 'A key that GitHub issues, which lets the page read the chosen repositories and nothing else. It is needed only to fetch the files from GitHub.'],
    ['A repository, and owner/name', "A repository is a folder of files that GitHub keeps with its history. It is written as owner/name, that is, the name of the person or team that owns it, a slash, and the repository's own name."],
    ['A branch', 'One line of the history of a repository on GitHub. If you leave it empty, the page fetches from the default branch.'],
    ['Provenance', 'A record of where the files came from, such as the repository and the commit, which the download keeps in provenance.json.'],
    ['The boundary', "The developer's name for the checklists and the account of how ready each audit question is, which the download keeps in the folder named boundary."],
    ['A stored procedure', 'A program saved on the server and run by its name. Schemalyser cannot see the tables that a stored procedure reads, because its SQL is not in the file that calls it.'],
  ] as [string, string][],
  keepsNothing: 'Schemalyser keeps nothing after you close this page.',
  version: (version: string, checksum: string) => `Version ${version}. Checksum: ${checksum}`,
};

// The six files of the inventory, in the order the page shows them.
export const packFiles = [
  {
    file: 'elements.csv',
    title: 'Columns in use',
    explanation:
      'This file lists each column that the SQL files use. The first count is the number of SQL files that use the column. The counts that follow are the numbers of SQL files that select it, filter on it, join on it, group by it and compute something from it.',
  },
  {
    file: 'joins.csv',
    title: 'Joins',
    explanation:
      "This file lists each pair of columns that the SQL files join, the kind of join, and the number of SQL files that make it. In a left, right or full join, the table being joined is on the right. A join marked 'where' is made in a WHERE clause or in a query nested inside another.",
  },
  {
    file: 'filters.csv',
    title: 'Filters',
    explanation:
      'This file lists each column that the SQL files filter on, the comparison they use and the kind of value they compare it with. Schemalyser writes the value itself only where the query results already list that value for that column, as a code that at least ten rows hold, and it writes it as the query results spell it. Every other value is left blank.',
  },
  {
    file: 'derivations.csv',
    title: 'Computed expressions',
    explanation:
      "This file lists the expressions that the SQL files compute from columns. Schemalyser has rewritten each one with the names from the list of tables and columns and with a placeholder in place of every value.",
  },
  {
    file: 'comparisons.csv',
    title: 'Comparisons between columns',
    explanation:
      'This file lists each pair of columns that the SQL files compare as earlier and later, or smaller and larger, and the number of SQL files that do so.',
  },
  {
    file: 'requests.csv',
    title: 'SQL files',
    explanation:
      'This file lists each SQL file by number, with the number of statements that it contains, the number of parts that Schemalyser could not read and the columns it uses. Schemalyser does not write the names of the files.',
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
    explanation: 'This file records how much of the SQL files Schemalyser was able to read.',
  },
];

// The query for the full list of tables and columns, folded away in step 1. It reads metadata only.
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
    "The repository that holds the folder for this audit, with the list of tables and columns and the site rules, written as owner/name:",
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
