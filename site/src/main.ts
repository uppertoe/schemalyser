import { sandboxStrings } from './sandbox-strings';
import { catalogueQuery, githubStrings, packFiles, strings, type Guide } from './strings';
import { REPOSITORY, isStateFile, type FetchReply, type FetchRequest, type RepositoryReport } from './github-protocol';

declare const __VERSION__: string;

type State = 'loading' | 'load-failed' | 'ready' | 'analysing' | 'review' | 'locked';
type StepState = 'done' | 'current' | 'upcoming' | 'problem';
type Sandbox = 'none' | 'building' | 'built';

interface Summary {
  sentences: [string, string];
  unread: [string, number][];
}

// One item of a target query's checklist, as browser.boundary_run gives it.
interface Item {
  id: string;
  kind: string;
  // The question for the team that looks after the database, where the core words one for the item.
  note?: string;
  status: 'answered' | 'partly' | 'open';
  blocking: boolean;
  question: string;
  needed: string;
  inHand: string;
  actor: string;
  group: 'answered' | 'sql' | 'other';
  intent?: string;
  route?: string;
  // The plain queries that would answer the item, by identifier, why it needs them, and whether they can run.
  queryState?: '' | 'ready' | 'waiting' | 'large' | 'ran';
  queryReason?: string;
  queryIds?: string[];
  // Which phase needs the item: the answer from the source database, not this question, or only the release.
  stage?: 'source' | 'unneeded' | 'release';
  // The question that a colleague can answer from knowledge, and the answer a person gave.
  ask?: { kind: 'join' | 'filter' | 'codes' | 'route'; text: string; left?: string; right?: string; tables?: Record<string, string[]>;
    column?: string; vocabulary?: string; concept?: string; meaning?: string; search?: string; group?: string; choose?: string[][]; step?: string;
    steps?: string[]; words?: string[]; definition?: string[];
    // What the folder for this audit assumes that each code to choose from means, where nobody here has confirmed it.
    assumed?: Record<string, string> } | null;
  fact?: string;
  // The answers that a person gave about the item, as the core names them for withdrawal; whether the item heads what
  // remains; for a route, the name that is not visible; and, for the count by year, the counts once seen.
  withdraw?: Record<string, unknown>[];
  top?: boolean;
  missing?: string;
  years?: (number | null)[][];
  // Whether a result from a training database left the item to be asked again on the production copy.
  again?: boolean;
}

// One plain query that a checklist offers, written by the core. The page shows it and never changes it.
interface Query {
  id: string;
  sql: string;
  state: string;
}

interface Target {
  name: string;
  // The audit question in plain words, from the header comment of its file, and the decisions that can change its answer.
  title?: string;
  decisions?: string[];
  counts: { total: number; answered: number; partly: number; open: number };
  steps: boolean;
  verdict: string;
  readiness: string;
  rows: Item[];
  sizes?: { sql: string; reason: string; tables: string[] } | null;
  queries?: Query[];
  // The core profile's plain queries, which the central OMOP team runs, tier one first.
  profile?: Query[];
  // The audit query: the question as one query over the source tables, with what the page says about it.
  draft?: { sql: string; countsOnly: boolean; restructured?: boolean; tables: { name: string; rows: number | null }[]; waiting?: boolean;
    script?: Script | null } | null;
  stages?: { source: Target['counts']; release: Target['counts'] };
  stageVerdicts?: string[];
  questions?: string;
  specification?: string;
  charted?: { sql: string; from: string; to: string; codes: string[]; kept?: boolean; worst?: string; withheld?: string } | null;
  listed?: { sql: string; year: number | null; column: string; years: number[]; rows: number | null; link: string; waiting?: boolean; largest?: [string, number] | null;
    readings?: [string, number | null] | null;
    worst?: string; withheld?: string } | null;
  // The routes that the catalogue settled, where a step gave way to one of its alternatives, one sentence each.
  routes?: string[];
  settings?: { from?: string | null; to?: string | null; kinds?: number[]; pressures?: string | null; floor?: number | null; ceiling?: number | null;
    isolated?: string | null; bypass?: string | null; age?: string | null; notes?: Record<string, string>; database?: string | null };
  kinds?: [number, string][];
  // What choosing the kinds of anaesthetic costs, in one sentence from the core, shown above the ticks; it may be empty.
  kinds_cost?: string;
  needs?: { questions: number; queries: number; other: number; lessCertain: number; settled: number; remaining: string[]; again?: string[] };
}

interface Boundary {
  ok: boolean;
  problem?: string;
  conversion?: boolean;
  targetFiles?: number;
  targets?: Target[];
  notes?: string[];
  summary?: string;
  requests?: number;
  checks?: string;
  profile?: string;
}

// What the page remembers of a checklist, to show what the next analysis answers. It holds only the
// items' identifiers and statuses, which name catalogue and conversion names, as the download does.
type Snapshot = Map<string, { answered: number; statuses: Map<string, string>; points?: string[] }>;

interface Result {
  boundary: Boundary;
  seconds: number;
  pack: Record<string, string>;
  index: [number, string][];
  summary: Summary;
  nothingUnread: string;
  checks: { used: string; unanswered: string; noHeaders: boolean } | null;
  checkScript: string;
  noHeaders: boolean;
  zip: Uint8Array<ArrayBuffer>;
}

interface RunResult {
  status: 'ok' | 'unreadable' | 'unsupported' | 'database-error';
  columns?: string[];
  rows?: (string | null)[][];
  count?: number | null;
  message?: string;
  translated?: string;
}

const $ = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;
// A concept's name as the vocabulary gives it, with its number, after the plain Australian name: "general anaesthetic
// (Administration of general anesthetic, concept 4174669)". The page shows the plain name first and the vocabulary's own
// name and number after it in smaller type, wherever a concept is named outside a query or a text to copy.
const CONCEPT = /\(([^()]*\bconcept \d+)\)/g;
const KEEP_AS_TEXT = new Set(['PRE', 'TEXTAREA', 'OPTION', 'CODE', 'SUMMARY']);
function withConcepts(node: HTMLElement, content: string) {
  if (KEEP_AS_TEXT.has(node.tagName) || !CONCEPT.test(content)) {
    node.textContent = content;
    return;
  }
  CONCEPT.lastIndex = 0;
  let at = 0;
  for (const match of content.matchAll(CONCEPT)) {
    node.append(content.slice(at, match.index));
    const named = document.createElement('small');
    named.className = 'concept-name';
    named.textContent = `(${match[1]})`;
    node.append(named);
    at = match.index + match[0].length;
  }
  node.append(content.slice(at));
}
const el = <K extends keyof HTMLElementTagNameMap>(tag: K, content?: string, className?: string) => {
  const node = document.createElement(tag);
  if (content !== undefined) withConcepts(node, content);
  if (className) node.className = className;
  return node;
};

const catalogue = $<HTMLInputElement>('catalogue');
const rules = $<HTMLInputElement>('rules');
const folder = $<HTMLInputElement>('folder');
const checks = $<HTMLInputElement>('checks');
const stateFolder = $<HTMLInputElement>('state-folder');
const analyse = $<HTMLButtonElement>('analyse');
const addFiles = $<HTMLInputElement>('add-files');
const addFolder = $<HTMLInputElement>('add-folder');
const reanalyse = $<HTMLButtonElement>('reanalyse');
const inputs = [stateFolder, catalogue, rules, folder, checks, addFiles, addFolder];

let state: State = 'loading';
let sandbox: Sandbox = 'none';
let runningRequests = false;
let worker: Worker | null = null;
// Whether the current worker has been given any file. Once it has, it may hold request text even after
// an analysis fails or is cleared, so the page ends it when the network returns.
let workerHasFiles = false;
let zip: Uint8Array<ArrayBuffer> | null = null;
let checkScript = '';
// The check results that the checklists use, with every pasted result added, as the core writes checks.csv.
let checksText = '';
// Whether the checklist on the page was worked out again after a paste or an answer, rather than by an analysis.
let sinceAnalysis = false;
let pasting = false;
// The core profile that the checklists use, with every pasted result added, as the core writes core-profile.csv.
let profileText = '';
// The catalogue and the sizes read from the result of the first query, used in place of a catalogue file.
let firstCatalogue: File | null = null;
let firstChecks: File | null = null;
let names = new Map<number, string>();
// Requests added after an analysis. They are kept, with the requests chosen in the third step, until
// the page is cleared or the network returns.
let added: { path: string; file: File }[] = [];
let addedSinceAnalysis = 0;
// The checklist of the last analysis, and whether a result is on the page.
let snapshot: Snapshot | null = null;
let hasResult = false;
// Whether the analysis under way was asked for from the checklist, so that the page does not scroll.
let reanalysing = false;

// The GitHub path. The files are fetched by a separate window (github.html) and held here, in
// memory, until they are analysed or discarded.
interface Fetched {
  reports: RepositoryReport[];
  requests: { path: string; file: File }[];
  state: Map<string, File>;
}
const github = true;
const githubFields = ['github-requests', 'github-requests-ref', 'github-state', 'github-state-ref', 'github-token'].map(
  (id) => $<HTMLInputElement>(id),
);
const [requestsRepository, requestsRef, stateRepository, stateRef, tokenInput] = githubFields;
let fetchWindow: Window | null = null;
let pendingRequest: FetchRequest | null = null;
let fetched: Fetched | null = null;
// The invented example, fetched from this site while the page is online, and whether the checklist on the page came from it.
let example: { requests: { path: string; file: File }[]; state: Map<string, File> } | null = null;
let exampleLoading = false;
let fromExample = false;

function text(id: string, value: string) {
  $(id).textContent = value;
}

function stepStates(online: boolean): StepState[] {
  const later: StepState[] = ['upcoming', 'upcoming', 'upcoming', 'upcoming'];
  if (state === 'analysing') return ['done', 'done', 'done', 'current', 'upcoming', 'upcoming', 'upcoming'];
  if (state === 'review') return ['done', 'done', 'done', 'current', 'current', 'current', 'current'];
  if (state === 'locked') {
    return zip
      ? ['done', 'problem', 'done', 'current', 'current', 'current', 'upcoming']
      : ['done', 'problem', 'upcoming', ...later];
  }
  if (state === 'ready' && !online) return ['done', 'done', 'current', ...later];
  return ['current', state === 'load-failed' ? 'problem' : 'current', 'upcoming', ...later];
}

function show() {
  const online = navigator.onLine;
  stepStates(online).forEach((stepState, i) => ($(`step-${i + 1}`).dataset.state = stepState));

  $('t-loading').hidden = state !== 'loading';
  $('t-load-failed').hidden = state !== 'load-failed';
  $('b-loaded').hidden = !(state === 'ready' && online);
  $('t-reconnected').hidden = state !== 'locked';
  $('b-progress').hidden = state !== 'analysing';
  $('checklist').hidden = !hasResult || !(state === 'review' || state === 'analysing' || (state === 'locked' && zip !== null));
  $('checklist').dataset.busy = String(state === 'analysing');
  $('b-add').hidden = state !== 'review' && state !== 'analysing';
  $('b-restart').hidden = state !== 'locked' || snapshot === null;
  reanalyse.disabled = state !== 'review' || navigator.onLine;
  $('t-held').hidden = state !== 'review';
  text('t-held', strings.held(requestFiles().length, addedSinceAnalysis));
  $('t-kept').hidden = !(snapshot !== null && !hasResult && state === 'ready' && !online);
  $('results').hidden = !(state === 'review' || (state === 'locked' && zip !== null));
  $('index-block').hidden = state !== 'review';
  $('b-check-script').hidden = state !== 'review';
  $('b-paste').hidden = !(state === 'review' || state === 'analysing') || !$('checklists').childElementCount;
  $<HTMLButtonElement>('read-paste').disabled = state !== 'review' || pasting;
  // The state's own save button, at the end of each checklist, holds the check results and the core profile as well.
  $('save-checks').hidden = true;
  $('t-save-checks').hidden = true;
  // The core profile is for the later OMOP release, so its paste box appears only once that part is opened.
  const anyProfile = [...document.querySelectorAll('#checklists details.release[open] .profile-queries')].length > 0;
  $('b-profile-paste').hidden = !(state === 'review' || state === 'analysing') || !(anyProfile || profileText);
  $<HTMLButtonElement>('read-profile-paste').disabled = state !== 'review' || pasting;
  $('save-profile').hidden = true;
  $('t-save-profile').hidden = true;

  $('b-build-form').hidden = sandbox !== 'none';
  $('t-building').hidden = sandbox !== 'building';
  $('b-built').hidden = sandbox !== 'built';
  $<HTMLButtonElement>('run-requests').disabled = runningRequests;
  $('b-requests-progress').hidden = !runningRequests;

  const connection = $('t-connection');
  connection.textContent = online ? strings.connected : strings.isOffline;
  connection.dataset.online = String(online);

  $('t-folder-count').hidden = !folder.files?.length;
  text('t-folder-count', strings.folderCount(sqlFiles().length));
  const inFolder = stateFromFolder();
  $('t-state-found').hidden = !stateFolder.files?.length;
  text(
    't-state-found',
    strings.stateFound(
      inFolder.has('catalogue.csv'),
      [...inFolder.keys()].filter((path) => path.startsWith('conversion/')).length,
      [...inFolder.keys()].filter((path) => path.startsWith('targets/')).length,
    ),
  );
  const chosen = chosenFiles();
  analyse.disabled = !canAnalyse(chosen) || fetchWindow !== null;
  // Without a catalogue, the first query is offered once the folder for this audit or the requests are chosen and the computer
  // is offline: the audit's steps name the tables, so a colleague with no SQL files can still start.
  $('b-first').hidden = !(state === 'ready' && !online && (chosen.requests.length || stepFiles(chosen).length) && (!chosen.catalogue || firstCatalogue));
  $<HTMLButtonElement>('first-write').disabled = !worker || online;
  $('b-save-state').hidden = state !== 'review';
  $('b-example').hidden = state !== 'ready';
  $<HTMLButtonElement>('example-load').disabled = exampleLoading || example !== null;
  $('t-example-chosen').hidden = !(example !== null && state === 'ready' && !online);
  $('t-example-banner').hidden = !fromExample;
  $('t-save-state').hidden = $('b-save-state').hidden;

  showGitHub(online);
}

function showGitHub(online: boolean) {
  $('b-github').hidden = !(github && (state === 'loading' || state === 'ready'));
  $('b-github-form').hidden = !online;
  $('t-github-progress').hidden = fetchWindow === null;
  $('t-github-closed').hidden = !(fetched && online && fetchWindow === null);
  $<HTMLButtonElement>('github-fetch').disabled =
    fetchWindow !== null ||
    !REPOSITORY.test(requestsRepository.value.trim()) ||
    !(stateRepository.value.trim() === '' || REPOSITORY.test(stateRepository.value.trim())) ||
    tokenInput.value === '';
}

const byPath = (a: { path: string }, b: { path: string }) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0);

// The state folder chosen from this computer, in the layout of the boundary's state folder. Only the
// files that the state may hold are taken, by the same rule as the fetch from GitHub.
// The state files that the page writes itself, which a state folder chosen here may hold besides those of the state repository.
const SAVED_STATE_FILES = ['facts.json', 'sql_evidence.json', 'audit.json'];

function stateFromFolder() {
  const found = new Map<string, File>();
  for (const file of Array.from(stateFolder.files ?? [])) {
    const path = file.webkitRelativePath.split('/').slice(1).join('/');
    if (isStateFile(path) || SAVED_STATE_FILES.includes(path)) found.set(path, file);
  }
  return found;
}

// The state, merged: a fetched file gives way to the state folder, and both give way to a file
// chosen on its own.
function stateFiles() {
  const merged = new Map<string, File>(fetched?.state ?? example?.state ?? []);
  // The catalogue and the sizes from the first query stand in for files that the state does not hold.
  if (firstCatalogue && !merged.has('catalogue.csv')) merged.set('catalogue.csv', firstCatalogue);
  for (const [path, file] of stateFromFolder()) merged.set(path, file);
  for (const [path, input] of [
    ['catalogue.csv', catalogue],
    ['site-rules.json', rules],
    ['checks.csv', checks],
  ] as const) {
    const file = input.files?.[0];
    if (file) merged.set(path, file);
  }
  // The sizes from the first query are already merged with the check results that were chosen.
  if (firstChecks && merged.get('catalogue.csv') === firstCatalogue) merged.set('checks.csv', firstChecks);
  return merged;
}

// The requests: the folder chosen in the third step, or else the fetched ones, with any added since.
// An added file takes the place of an earlier file with the same path.
function requestFiles() {
  const fromFolder = sqlFiles();
  const merged = new Map<string, File>();
  for (const { path, file } of fromFolder.length ? fromFolder : (fetched?.requests ?? example?.requests ?? [])) merged.set(path, file);
  for (const { path, file } of added) merged.set(path, file);
  return [...merged].map(([path, file]) => ({ path, file })).sort(byPath);
}

// The files to analyse. A file chosen from this computer takes the place of the fetched one.
// Whether an analysis can begin: there is a catalogue, and either requests or a state that holds target queries,
// whose saved evidence and answers stand without the request files.
function canAnalyse(chosen: ReturnType<typeof chosenFiles>) {
  return Boolean(chosen.catalogue && (chosen.requests.length || [...chosen.state.keys()].some((path) => path.startsWith('targets/'))));
}

// The audit's steps in the folder for this audit, whose table names the first query asks about.
function stepFiles(chosen: { state: Map<string, File> }) {
  return [...chosen.state].filter(([path]) => path.startsWith('conversion/') && path.endsWith('.sql')).map(([, file]) => file);
}

function chosenFiles() {
  const state = stateFiles();
  return {
    catalogue: state.get('catalogue.csv') ?? null,
    rules: state.get('site-rules.json') ?? null,
    checks: state.get('checks.csv') ?? null,
    state,
    requests: requestFiles(),
  };
}

// The repositories whose files an analysis is about to use, for the line shown with the results, and
// the commits that the boundary's provenance names: a commit only where every file came from it.
function provenance(chosen: ReturnType<typeof chosenFiles>) {
  const none = { lines: [] as string[], commits: { state: null as string | null, requests: null as string | null } };
  if (!fetched) return none;
  const [requestsReport, stateReport] = fetched.reports;
  const used: RepositoryReport[] = [];
  const fetchedRequests = new Set(fetched.requests.map(({ file }) => file));
  const requestsFetched = chosen.requests.filter(({ file }) => fetchedRequests.has(file)).length;
  if (requestsFetched && requestsReport) used.push(requestsReport);
  const fetchedState = new Set(fetched.state.values());
  const stateFetched = [...chosen.state.values()].filter((file) => fetchedState.has(file)).length;
  if (stateFetched && stateReport) used.push(stateReport);
  return {
    lines: used.map((report) => githubStrings.provenance(report.repository, report.commit)),
    commits: {
      requests: requestsReport && requestsFetched === chosen.requests.length ? requestsReport.commit : null,
      state: stateReport && stateFetched === chosen.state.size ? stateReport.commit : null,
    },
  };
}

function sqlFiles() {
  return Array.from(folder.files ?? [])
    .filter((file) => /\.sql$/i.test(file.name))
    .map((file) => ({ path: file.webkitRelativePath.split('/').slice(1).join('/') || file.name, file }))
    .sort(byPath);
}

function hideErrors() {
  for (const id of ['t-catalogue-error', 't-checks-error', 't-analysis-failed']) $(id).hidden = true;
}

function resetInputs() {
  for (const input of inputs) input.value = '';
  // The first query holds names from the requests, so it goes with them.
  firstCatalogue = null;
  firstChecks = null;
  $('first-query').textContent = '';
  $<HTMLTextAreaElement>('first-paste').value = '';
  $('b-first-query').hidden = true;
  $('t-first-result').hidden = true;
  $('t-first-doubt').hidden = true;
  added = [];
  addedSinceAnalysis = 0;
  hideErrors();
  discardFetched();
  example = null;
  fromExample = false;
  $('t-example-status').hidden = true;
}

// Lets go of the fetched files, in the same way as the file choosers are emptied.
function discardFetched() {
  fetched = null;
  $('github-fetched').replaceChildren();
}

function resetSandbox() {
  sandbox = 'none';
  runningRequests = false;
  $('query-result').hidden = true;
  $('requests-result').hidden = true;
  $('requests-table').replaceChildren();
  $<HTMLTextAreaElement>('sql').value = '';
}

function clearPaste() {
  $<HTMLTextAreaElement>('paste').value = '';
  $<HTMLTextAreaElement>('profile-paste').value = '';
  $('t-paste-result').hidden = true;
  $('t-profile-paste-result').hidden = true;
  pasting = false;
}

function clearChecklist() {
  hasResult = false;
  whoNow = '';
  checksText = '';
  profileText = '';
  clearPaste();
  $('checklists').replaceChildren();
  $('boundary-notes').replaceChildren();
  $('t-changes').hidden = true;
  $('t-summary').textContent = '';
}

function clearResult() {
  zip = null;
  checkScript = '';
  names = new Map();
  clearChecklist();
  $('github-provenance').replaceChildren();
  $('github-provenance').hidden = true;
  $('pack').replaceChildren();
  $('index').replaceChildren();
  $('unread').replaceChildren();
  resetSandbox();
}

// Reads CSV as the core writes it: fields quoted only where they contain a comma or a quote.
function parseCsv(csv: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = '';
  let quoted = false;
  for (let i = 0; i < csv.length; i++) {
    const ch = csv[i];
    if (quoted) {
      if (ch === '"' && csv[i + 1] === '"') {
        field += '"';
        i++;
      } else if (ch === '"') quoted = false;
      else field += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === ',') {
      row.push(field);
      field = '';
    } else if (ch === '\n') {
      row.push(field);
      rows.push(row);
      row = [];
      field = '';
    } else field += ch;
  }
  if (field || row.length) rows.push([...row, field]);
  return rows;
}

function grid(header: string[] | null, rows: (string | null)[][], wrap: string[] = []) {
  const result = el('table');
  if (header) {
    const head = result.createTHead().insertRow();
    for (const name of header) head.append(el('th', name));
  }
  const body = result.createTBody();
  for (const row of rows) {
    const line = body.insertRow();
    row.forEach((value, i) => {
      const cell = line.insertCell();
      cell.textContent = value ?? 'NULL';
      if (value === null) cell.className = 'null';
      else if (/^-?\d+(\.\d+)?$/.test(value)) cell.className = 'number';
      else if (header && wrap.includes(header[i])) cell.className = 'wrap';
    });
  }
  const frame = el('div', undefined, 'table-frame');
  frame.append(result);
  return frame;
}

// The question that a colleague can answer from knowledge, with the controls that record the answer as a fact. Every
// question has three answers: yes; no, with what is true instead; and not sure.
function sendFacts(box: Element, facts: Record<string, unknown>[]) {
  const section = box.closest('section.target');
  const who = ((section?.querySelector('input.fact-who') as HTMLInputElement | null)?.value ?? whoNow).trim();
  const date = new Date().toISOString().slice(0, 10);
  const stamped = facts.map((fact) => ({ ...fact, date, ...(who ? { who } : {}) }));
  if (stamped.length) {
    clearStale();
    beginWorking(box);
  }
  if (stamped.length === 1) worker?.postMessage({ type: 'fact', fact: JSON.stringify(stamped[0]) });
  else if (stamped.length) worker?.postMessage({ type: 'facts', facts: JSON.stringify(stamped) });
}

// The name of the person answering, kept while the page is open, as entered beside the questions.
let whoNow = '';

// Whether the answer that the worker is working on is one withdrawn, so that the page says so when the checklist returns.
let withdrawing = false;

// Withdraws the answers that a person gave about one item, so that the question is asked again.
function withdrawFacts(item: Item, button?: Element) {
  if (!worker || !item.withdraw?.length) return;
  clearStale();
  if (button?.parentElement) beginWorking(button.parentElement);
  withdrawing = true;
  worker.postMessage({ type: 'fact-withdraw', withdraw: JSON.stringify(item.withdraw) });
}

// What the last action settled is true only until the next one: the note of what it settled and the status of the paste
// or answer are cleared as soon as another action begins.
function clearStale() {
  for (const node of document.querySelectorAll<HTMLElement>('.fresh-note, .answer-confirmation')) node.remove();
  $('t-paste-result').hidden = true;
  $('t-changes').hidden = true;
}

// Where the person acted, so that the confirmation can stand beside it once the checklist has been worked out again: the
// audit question, the point by its identifier, or the part of the checklist, such as the settings.
let pendingAnchor: { target: string; id: string | null; part: string | null } | null = null;
let workingBox: HTMLElement | null = null;
const PARTS = ['listed', 'settings', 'database', 'charted'];

// While Schemalyser works the checklist out again, the place where the person acted says so, and its controls wait.
function beginWorking(from: Element) {
  endWorking();
  const section = from.closest<HTMLElement>('section.target');
  const item = from.closest<HTMLElement>('li.item');
  const part = from.closest<HTMLElement>('.sizes');
  pendingAnchor = { target: section?.dataset.target ?? '', id: item?.dataset.id ?? null,
    part: part ? PARTS.find((name) => part.classList.contains(name)) ?? null : null };
  const holder = (item?.querySelector<HTMLElement>('.item-body') ?? part ?? (from as HTMLElement));
  holder.dataset.working = 'true';
  for (const control of holder.querySelectorAll<HTMLButtonElement | HTMLInputElement | HTMLSelectElement>('button, input, select')) {
    if (!control.disabled) {
      control.disabled = true;
      control.dataset.waiting = 'true';
    }
  }
  const note = el('p', strings.working, 'status working-note');
  note.setAttribute('role', 'status');
  (from as HTMLElement).append(note);
  workingBox = holder;
}

// The controls come back where nothing was re-drawn, as when an answer could not be recorded.
function endWorking() {
  for (const note of document.querySelectorAll('.working-note')) note.remove();
  if (workingBox) {
    delete workingBox.dataset.working;
    for (const control of workingBox.querySelectorAll<HTMLButtonElement | HTMLInputElement | HTMLSelectElement>('[data-waiting]')) {
      control.disabled = false;
      delete control.dataset.waiting;
    }
  }
  workingBox = null;
}

// The confirmation of an answer, beside the point that was answered, wherever the point now stands, or at the head of the
// part in which the person acted.
function confirmBeside(said: string, kind: 'good' | 'problem') {
  const anchor = pendingAnchor;
  pendingAnchor = null;
  // A change that the page sent by itself, such as the database chosen before the checklist existed, needs no confirmation.
  if (!anchor && kind === 'good') return;
  const section = anchor ? document.querySelector<HTMLElement>(`section.target[data-target="${CSS.escape(anchor.target)}"]`) : null;
  const note = el('p', said, `status ${kind} answer-confirmation`);
  note.setAttribute('role', 'status');
  const item = anchor?.id && section ? section.querySelector<HTMLElement>(`li.item[data-id="${CSS.escape(anchor.id)}"] .item-body`) : null;
  const part = !item && anchor?.part && section ? section.querySelector<HTMLElement>(`.sizes.${anchor.part}`) : null;
  if (item) item.prepend(note);
  else if (part) (part.querySelector('h4') ?? part.firstChild)?.after(note) ?? part.prepend(note);
  else if (section) section.querySelector('h3')?.after(note);
  else $('checklists').prepend(note);
  note.scrollIntoView({ block: 'center' });
}

function askButton(label: string, className: string, onClick: () => void) {
  const b = el('button', label, className);
  b.type = 'button';
  b.addEventListener('click', onClick);
  return b;
}

// The codes of each meaning that a name search covers, gathered from the items of one target that share the search.
let searchGroups = new Map<string, { concept: string; vocabulary: string; meaning: string; column: string }[]>();

function askElement(item: Item) {
  const ask = item.ask!;
  const box = el('div', undefined, 'ask');
  box.dataset.ask = ask.kind;
  box.append(el('p', strings.questionFirst, 'ask-label'), el('p', ask.text, 'ask-text'));
  // Beside the question, in one line, why Schemalyser asks it and what the answer changes.
  if (strings.askWhy[ask.kind]) box.append(el('p', strings.askWhy[ask.kind], 'note ask-why'));
  const send = (fact: Record<string, unknown>) => sendFacts(box, [fact]);
  const actions = el('div', undefined, 'actions');
  if (ask.kind === 'route') {
    actions.append(
      ...([['absent', strings.routeAbsent, 'fact-yes'], ['hidden', strings.routeHidden, 'secondary fact-no'],
        ['unsure', strings.notSure, 'secondary fact-unsure']] as const).map(([answer, label, cls]) => askButton(label, cls, () =>
        sendFacts(box, (ask.steps?.length ? ask.steps : [ask.step]).map((step) => ({ kind: 'route', step, answer }))))));
    box.append(actions);
    return box;
  }
  if (ask.kind === 'join') {
    actions.append(askButton(strings.factYes, 'fact-yes', () => send({ kind: 'join', left: ask.left, right: ask.right, answer: 'yes' })),
      askButton(strings.notSure, 'secondary fact-unsure', () => send({ kind: 'join', left: ask.left, right: ask.right, answer: 'unsure' })));
    const [leftTable, rightTable] = [ask.left!.split('.')[0], ask.right!.split('.')[0]];
    const choose = (table: string) => {
      const select = el('select', undefined, 'fact-column');
      select.append(el('option', '', ''), ...(ask.tables?.[table] ?? []).map((name) => {
        const option = el('option', `${table}.${name}`);
        option.value = `${table}.${name}`;
        return option;
      }));
      return select as HTMLSelectElement;
    };
    const left = choose(leftTable);
    const right = choose(rightTable);
    // No opens a short list of the columns that could match instead; the answer is saved from there.
    const instead = el('div', undefined, 'join-instead');
    instead.hidden = true;
    instead.append(el('p', strings.factInstead, 'note'), left, right, askButton(strings.factSaveNo, 'secondary fact-no', () =>
      send({ kind: 'join', left: ask.left, right: ask.right, answer: 'no', ...(left.value && right.value ? { instead: [left.value, right.value] } : {}) })));
    actions.insertBefore(askButton(strings.factNo, 'secondary fact-open-no', () => { instead.hidden = false; }), actions.lastChild);
    box.append(actions, instead);
    return box;
  }
  if (ask.kind === 'filter') {
    actions.append(askButton(strings.factYes, 'fact-yes', () => send({ kind: 'filter', column: ask.column, answer: 'yes' })),
      askButton(strings.factNo, 'secondary fact-no', () => send({ kind: 'filter', column: ask.column, answer: 'no' })),
      askButton(strings.notSure, 'secondary fact-unsure', () => send({ kind: 'filter', column: ask.column, answer: 'unsure' })));
    box.append(actions);
    return box;
  }
  const unsure = { kind: 'codes', vocabulary: ask.vocabulary, concept: ask.concept, column: ask.column, codes: [], answer: 'unsure' };
  // The codes of a pasted lookup or list of values, with their names: the two of them read each name and choose what it
  // means, once for all the meanings of the column, and nothing is settled until they do.
  if (ask.choose && ask.group) {
    const group = searchGroups.get(ask.group) ?? [];
    if (group[0]?.concept !== ask.concept) box.append(el('p', strings.chooseAbove, 'note'));
    else box.append(chooseTable(group, ask.choose, ask.assumed ?? {}));
    // Where no code has a name, nothing can be confirmed from the table, so the codes are entered from what the two know.
    if (ask.choose.every(([, name]) => nameless(name))) {
      const label = el('label', strings.chooseNoNames);
      const input = el('input', undefined, 'fact-codes') as HTMLInputElement;
      input.type = 'text';
      label.append(input);
      box.append(label, askButton(strings.codesSave, 'fact-save-codes', () => {
        const codes = input.value.split(',').map((code) => code.trim()).filter(Boolean);
        if (codes.length) send({ kind: 'codes', vocabulary: ask.vocabulary, concept: ask.concept, column: ask.column, codes });
      }), askButton(strings.notSure, 'secondary fact-unsure', () => send(unsure)));
    }
    return box;
  }
  // Where the list of what is charted on the cohort can be had, the codes are chosen from it, and the name search is not shown.
  if (ask.search && ask.group && listedNow && listedNow.column.toUpperCase() === (ask.column ?? '').toUpperCase()) {
    box.append(el('p', listedNow.waiting ? strings.listedAfterCount : strings.listedInstead, 'note'));
    return box;
  }
  if (ask.search && ask.group) {
    const group = searchGroups.get(ask.group) ?? [];
    const first = group[0]?.concept === ask.concept;
    if (!first) {
      box.append(el('p', strings.searchAbove, 'note'));
      return box;
    }
    const [code, copy] = queryBlock(ask.search, `search:${ask.group}`);
    copy.textContent = strings.firstCopy;
    const copyRow = el('div', undefined, 'actions');
    copyRow.append(copy);
    const label = el('label', strings.searchPasteLabel);
    const area = el('textarea', undefined, 'search-paste') as HTMLTextAreaElement;
    area.rows = 5;
    area.spellcheck = false;
    label.append(area);
    const list = el('div', undefined, 'candidates');
    list.dataset.group = ask.group;
    // The words can be narrowed or widened on the page; the core writes the search again from them.
    const words = el('label', strings.searchWords);
    const wordsInput = el('input', undefined, 'search-words') as HTMLInputElement;
    wordsInput.type = 'text';
    wordsInput.value = (ask.words ?? []).join('; ');
    words.append(wordsInput);
    const rewrite = askButton(strings.searchRewrite, 'secondary search-rewrite', () => {
      const [table, column] = ask.column!.split('.');
      worker?.postMessage({ type: 'search-sql', group: ask.group, request: JSON.stringify({
        definition: ask.definition, table, column, words: wordsInput.value.split(';').map((w) => w.trim()).filter(Boolean) }) });
    });
    code.dataset.search = ask.group;
    box.append(code, copyRow, words, rewrite, label, askButton(strings.searchRead, 'search-read', () => {
      if (!area.value.trim()) return;
      // Two targets may share a search; the names are shown under the one whose button was pressed.
      searchList = list;
      worker?.postMessage({ type: 'codes-search', group: ask.group, text: area.value });
    }), list, el('p', strings.searchPrivate, 'note'));
    return box;
  }
  const label = el('label', strings.codesLabel);
  const input = el('input', undefined, 'fact-codes') as HTMLInputElement;
  input.type = 'text';
  label.append(input);
  box.append(label, askButton(strings.codesSave, 'fact-save-codes', () => {
    const codes = input.value.split(',').map((code) => code.trim()).filter(Boolean);
    if (codes.length) send({ kind: 'codes', vocabulary: ask.vocabulary, concept: ask.concept, column: ask.column, codes });
  }), askButton(strings.notSure, 'secondary fact-unsure', () => send(unsure)));
  return box;
}

// A name that is only a number, or empty, says nothing of what the code means, so it cannot confirm anything.
const nameless = (name: string) => /^[\s\d.,-]*$/.test(name ?? '');

// A code that the folder for this audit gives a meaning, and that nobody here has confirmed, says so beside it, as the
// specification does, so that an assumption is called an assumption wherever the code is shown.
function assumedNote(meaning: string | undefined) {
  return meaning ? [el('span', ` ${strings.codeAssumed(meaning)}`, 'note code-assumed')] : [];
}

function chooseTable(meanings: { concept: string; vocabulary: string; meaning: string; column: string }[], rows: string[][],
  assumed: Record<string, string> = {}) {
  const box = el('div', undefined, 'candidates choose-list');
  const table = el('table', undefined, 'candidate-table choose-table');
  const head = el('tr');
  head.append(el('th', strings.searchCode), el('th', strings.searchName), el('th', strings.searchChoice));
  table.append(head);
  const selects: [string, HTMLSelectElement][] = [];
  const option = (value: string, label: string) => {
    const o = el('option', label);
    o.value = value;
    return o;
  };
  for (const [code, name] of rows) {
    const line = el('tr');
    const select = el('select', undefined, 'candidate-choice choose-choice') as HTMLSelectElement;
    select.dataset.code = code;
    select.append(option('', strings.listedNotChosen), ...meanings.map((m) => option(m.concept, m.meaning)),
      option('neither', strings.searchNeither), option('unsure', strings.notSure));
    if (nameless(name)) {
      select.disabled = true;
      line.dataset.nameless = 'true';
    }
    const cell = el('td');
    cell.append(select);
    const named = el('td', nameless(name) ? strings.noName : name);
    named.append(...assumedNote(assumed[code]));
    line.append(el('td', code), named, cell);
    table.append(line);
    selects.push([code, select]);
  }
  const save = askButton(strings.searchSave, 'search-save choose-save', () => {
    const unsure = selects.filter(([, s]) => s.value === 'unsure').map(([code]) => code);
    const facts: Record<string, unknown>[] = meanings.map((m) => {
      const codes = selects.filter(([, s]) => s.value === m.concept).map(([code]) => code);
      return codes.length ? { kind: 'codes', vocabulary: m.vocabulary, concept: m.concept, column: m.column, codes, ...(unsure.length ? { uncertain: unsure } : {}) }
        : { kind: 'codes', vocabulary: m.vocabulary, concept: m.concept, column: m.column, codes: [], answer: 'unsure' };
    });
    sendFacts(box, facts);
  });
  box.append(table, save, el('p', strings.searchPrivate, 'note'));
  return box;
}

// The list under the name search whose result was last pasted.
let searchList: HTMLElement | null = null;

// The most rows of a name search that the page lists; a longer result is cut here, and the page says so.
const SEARCH_SHOWN = 40;

// The candidates that a name search found: every one is listed, and for each the two people choose what it is. Several rows
// may have one meaning. The choices become the codes of each meaning; a row marked not sure stays open, and a row charted
// as text with the mean in brackets is recorded as such.
function showCandidates(group: string, columns: string[], candidates: { code: string; values: string[] }[]) {
  const list = searchList?.isConnected && searchList.dataset.group === group ? searchList
    : document.querySelector<HTMLElement>(`.candidates[data-group="${CSS.escape(group)}"]`);
  const meanings = searchGroups.get(group) ?? [];
  if (!list) return;
  if (!candidates.length) {
    list.replaceChildren(el('p', strings.searchNone, 'status problem'));
    return;
  }
  const table = el('table', undefined, 'candidate-table');
  const head = el('tr');
  head.append(el('th', strings.searchCode), ...(columns.length ? columns : [strings.searchName]).map((c) => el('th', c)), el('th', strings.searchChoice));
  table.append(head);
  const selects: [string, HTMLSelectElement][] = [];
  // Beyond SEARCH_SHOWN rows the list is cut, and the page says so and asks for narrower words, so nothing is dropped silently.
  const shownCandidates = candidates.slice(0, SEARCH_SHOWN);
  for (const candidate of shownCandidates) {
    const row = el('tr');
    const select = el('select', undefined, 'candidate-choice') as HTMLSelectElement;
    select.dataset.code = candidate.code;
    const option = (value: string, label: string) => {
      const o = el('option', label);
      o.value = value;
      return o;
    };
    select.append(option('', strings.searchNeither), ...meanings.map((m) => option(m.concept, m.meaning)),
      option('text', strings.searchText), option('unsure', strings.notSure));
    const cell = el('td');
    cell.append(select);
    row.append(el('td', candidate.code), ...candidate.values.map((v) => el('td', v)), cell);
    table.append(row);
    selects.push([candidate.code, select]);
  }
  const save = askButton(strings.searchSave, 'search-save', () => {
    const unsure = selects.filter(([, s]) => s.value === 'unsure').map(([code]) => code);
    const facts: Record<string, unknown>[] = meanings.map((m) => {
      const codes = selects.filter(([, s]) => s.value === m.concept).map(([code]) => code);
      return codes.length ? { kind: 'codes', vocabulary: m.vocabulary, concept: m.concept, column: m.column, codes, ...(unsure.length ? { uncertain: unsure } : {}) }
        : { kind: 'codes', vocabulary: m.vocabulary, concept: m.concept, column: m.column, codes: [], answer: 'unsure' };
    });
    const text = selects.filter(([, s]) => s.value === 'text').map(([code]) => code);
    if (text.length && meanings[0]) facts.push({ kind: 'textbp', column: meanings[0].column, codes: text });
    sendFacts(list, facts);
  });
  const lead = el('p', strings.searchFound(shownCandidates.length), 'note');
  if (candidates.length > SEARCH_SHOWN) list.replaceChildren(el('p', strings.searchTooMany(candidates.length, SEARCH_SHOWN), 'status problem search-too-many'), lead, table, save);
  else list.replaceChildren(lead, table, save);
}

// The list of what is charted on the cohort's anaesthetics in one year: the query, its result as a table that can be
// filtered, the rows that match the words for the meanings sought marked as likely, and a choice for each row.
// More than one target may offer the list, so the block whose list was read is remembered, and the result is shown there
// with that target's meanings.
let listedNow: Target['listed'] = null;
// The last list that was read, and what the two of them have marked in it and in the lists of earlier years, by code, so that
// the list stays on the page after a save, a code marked not sure can be chosen again beside its count, and a code marked in
// one year is kept when another year is listed.
let listedShown: { column: string; year: number; columns: string[]; rows: { code: string; readings: number | null; anaesthetics: number | null; names: string[] }[] } | null = null;
const listedMarks = new Map<string, string>();
let listedTarget: Target | null = null;
let listedBox: HTMLElement | null = null;
// A query that reaches the table of readings is offered only as a script that starts from a temporary table of the cohort.
// Above it, the page says what the script creates, what to set first and the most that it reads; where the core does not
// offer it, the page says why instead.
type Script = { sql: string; worst?: string; withheld?: string };
// The largest table that the count by year and part 1 of each script read to find the anaesthetics, with its size.
let cohortLargest: [string, number] | null = null;
// The table of readings, with its size where the query results give it, so that the page says how large it is.
let readingsNow: [string, number | null] | null = null;
// Whether the checklist on the page was worked out for a training database with fictional patients, as the core recorded it.
let trainingNow = false;
// The database chosen where the first query is offered, before any checklist exists; it is sent once the first one arrives.
let databaseChoice: string | null = null;

// The three answers about the database that the SQL window is connected to, as radio buttons, with the sentence for "not sure".
function databaseOptions(name: string, current: string | null | undefined, onChoose: (value: string) => void) {
  const box = el('div', undefined, 'database-options');
  const unsure = el('p', strings.databaseUnsure, 'note database-unsure');
  unsure.hidden = current !== 'unsure';
  for (const [value, label] of strings.databaseOptions) {
    const wrap = el('label', ` ${label}`, 'database-option');
    const radio = el('input') as HTMLInputElement;
    radio.type = 'radio';
    radio.name = name;
    radio.value = value;
    radio.checked = current === value;
    radio.addEventListener('change', () => {
      if (!radio.checked) return;
      unsure.hidden = value !== 'unsure';
      onChoose(value);
    });
    wrap.prepend(radio);
    box.append(wrap);
  }
  box.append(unsure);
  return box;
}

// The database is kept with the audit's settings, so a change sends the settings as they stand with the new answer, and the
// core works the checklist out again.
function sendDatabase(target: Target, value: string, from?: Element) {
  if (!worker) return;
  clearStale();
  if (from) beginWorking(from);
  worker.postMessage({ type: 'settings', settings: JSON.stringify({ ...(target.settings ?? {}), database: value }) });
}

function databaseElement(target: Target) {
  const box = el('div', undefined, 'sizes database');
  box.append(el('h4', strings.databaseHeading), el('p', strings.databaseWhat, 'sizes-reason'));
  if (fromExample) box.append(el('p', strings.databaseExample, 'status example database-example'));
  box.append(databaseOptions(`database-${target.name}`, target.settings?.database, (value) => {
      databaseChoice = value;
      sendDatabase(target, value, box);
    }), el('p', strings.databaseChanged, 'note'));
  return box;
}

function scriptNotes(script: Script, long = false) {
  if (!script.sql) return script.withheld ? [el('p', script.withheld, 'sizes-reason status problem script-withheld')] : [];
  return [el('p', strings.scriptTemporary, 'sizes-reason script-note'), el('p', strings.scriptTimeout, 'sizes-reason script-note'),
    el('p', strings.scriptPlan(readingsNow), 'sizes-reason script-note'),
    ...(cohortLargest ? [el('p', strings.scriptCostly(...cohortLargest), 'sizes-reason script-note')] : []),
    ...(script.worst ? [el('p', strings.scriptWorst(script.worst), 'sizes-reason script-note')] : []),
    ...(long ? [el('p', strings.scriptMonth, 'sizes-reason script-note')] : []),
    // A script that is quick on a small training database can still misbehave on the production copy.
    ...(trainingNow ? [el('p', strings.scriptTraining, 'sizes-reason script-note script-training')] : [])];
}

function listedElement(target: Target) {
  const listed = target.listed!;
  const box = el('div', undefined, 'sizes listed');
  box.append(el('h4', strings.listedHeading), el('p', strings.listedWhat(listed.column, listed.year ?? 0), 'sizes-reason'),
    ...(trainingNow ? [el('p', strings.listedTraining, 'sizes-reason status problem listed-training')] : []),
    ...scriptNotes(listed));
  const yearLabel = el('label', strings.listedYear);
  const year = el('select', undefined, 'listed-year') as HTMLSelectElement;
  for (const y of listed.years) {
    const o = el('option', String(y));
    o.value = String(y);
    year.append(o);
  }
  year.value = String(listed.year);
  year.addEventListener('change', () => sendFacts(box, [{ kind: 'listed', column: listed.column, year: Number(year.value), rows: null }]));
  yearLabel.append(year);
  if (!listed.sql) {
    box.append(yearLabel);
    return box;
  }
  const [code, copy] = queryBlock(listed.sql, 'listed-query');
  code.dataset.query = 'listed';
  copy.dataset.query = 'listed';
  const label = el('label', strings.listedPasteLabel);
  const area = el('textarea', undefined, 'listed-paste') as HTMLTextAreaElement;
  area.rows = 5;
  area.spellcheck = false;
  label.append(area);
  const shown = el('div', undefined, 'listed-result');
  if (listed.rows !== null && listed.rows !== undefined) shown.append(el('p', strings.listedKept(listed.rows, listed.year ?? 0), listed.rows ? 'note' : 'status problem'));
  box.append(yearLabel, code, copy, label, askButton(strings.listedRead, 'listed-read', () => {
    if (!area.value.trim()) return;
    listedNow = listed;
    listedTarget = target;
    listedBox = box;
    worker?.postMessage({ type: 'listed', text: area.value });
  }), shown, el('p', strings.searchPrivate, 'note'));
  return box;
}

// The meanings sought in the listed column, and the words that name them, from the codes items of the target.
function listedMeanings(target: Target, column: string) {
  const meanings: { concept: string; vocabulary: string; meaning: string; column: string }[] = [];
  const words = new Set<string>();
  const assumed: Record<string, string> = {};
  for (const item of target.rows) {
    const ask = item.ask;
    if (ask?.kind !== 'codes' || (ask.column ?? '').toUpperCase() !== column.toUpperCase() || !ask.concept) continue;
    if (!meanings.some((m) => m.concept === ask.concept)) meanings.push({ concept: ask.concept, vocabulary: ask.vocabulary!, meaning: ask.meaning ?? ask.concept, column: ask.column! });
    for (const w of ask.words ?? []) words.add(w.toUpperCase());
    Object.assign(assumed, ask.assumed ?? {});
  }
  return { meanings, words: [...words], assumed };
}

const NOT_LIKELY = /(^|[^A-Z0-9])(PA|PAP|PULM[A-Z]*|CVP|CENTRAL VENOUS|AIRWAY|VENT[A-Z]*)($|[^A-Z0-9])/;

function showListed(columns: string[], rows: { code: string; readings: number | null; anaesthetics: number | null; names: string[] }[]) {
  const shown = (listedBox?.isConnected ? listedBox : document).querySelector<HTMLElement>('.listed-result');
  const listed = listedNow;
  if (!shown || !listed || !listedTarget) return;
  if (!rows.length) {
    // An empty list is an open point at the head of what remains, so it is recorded at once.
    sendFacts(shown, [{ kind: 'listed', column: listed.column, year: listed.year, rows: 0 }]);
    return;
  }
  const { meanings, words, assumed } = listedMeanings(listedTarget, listed.column);
  const whole = words.map((w) => new RegExp(`(^|[^A-Z0-9])${w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}($|[^A-Z0-9])`));
  const filter = el('input', undefined, 'listed-filter') as HTMLInputElement;
  filter.type = 'search';
  filter.placeholder = strings.listedFilter;
  const table = el('table', undefined, 'candidate-table listed-table');
  const head = el('tr');
  head.append(el('th', strings.searchCode), el('th', trainingNow ? strings.listedReadingsTraining : strings.listedReadings),
    el('th', trainingNow ? strings.listedAnaestheticsTraining : strings.listedAnaesthetics),
    ...(columns.length ? columns : [strings.searchName]).map((c) => el('th', c)), el('th', strings.searchChoice));
  table.append(head);
  const selects: [string, HTMLSelectElement, HTMLElement][] = [];
  const option = (value: string, label: string) => {
    const o = el('option', label);
    o.value = value;
    return o;
  };
  for (const row of rows) {
    const line = el('tr');
    // A pulmonary artery, central venous, airway or ventilator mean is never likely for an arterial or cuff mean.
    const likely = whole.some((w) => row.names.some((n) => w.test(n.toUpperCase())))
      && !row.names.some((n) => NOT_LIKELY.test(n.toUpperCase()));
    if (likely) line.dataset.likely = 'true';
    const select = el('select', undefined, 'candidate-choice listed-choice') as HTMLSelectElement;
    select.dataset.code = row.code;
    select.append(option('', strings.listedNotChosen), ...meanings.map((m) => option(m.concept, m.meaning)),
      option('text', strings.searchText), option('calculated', strings.listedCalculated), option('neither', strings.searchNeither),
      option('unsure', strings.notSure));
    select.value = listedMarks.get(row.code) ?? '';
    select.addEventListener('change', () => listedMarks.set(row.code, select.value));
    const cell = el('td');
    cell.append(select);
    const count = (n: number | null) => (n === null ? strings.countUnderTen : n.toLocaleString('en-AU'));
    const coded = el('td', row.code + (likely ? ` ${strings.listedLikely}` : ''));
    coded.append(...assumedNote(assumed[row.code]));
    line.append(coded, el('td', count(row.readings)), el('td', count(row.anaesthetics)),
      ...(row.names.every(nameless) ? [el('td', strings.noName)] : row.names.map((n) => el('td', n))), cell);
    table.append(line);
    selects.push([row.code, select, line]);
  }
  filter.addEventListener('input', () => {
    const text = filter.value.trim().toUpperCase();
    for (const [, , line] of selects) line.hidden = !!text && !(line.textContent ?? '').toUpperCase().includes(text);
  });
  const save = askButton(strings.searchSave, 'search-save listed-save', () => {
    // The marks of every year listed so far are saved together, so that a code retired in an earlier year is kept.
    for (const [code, s] of selects) listedMarks.set(code, s.value);
    const marked = (value: string) => [...listedMarks].filter(([, v]) => v === value).map(([c]) => c);
    const unsure = marked('unsure');
    // A code marked not sure is one open point, on the first meaning that has codes, and not on every meaning.
    const holder = meanings.find((m) => marked(m.concept).length);
    const facts: Record<string, unknown>[] = meanings.map((m) => {
      const codes = marked(m.concept);
      return codes.length ? { kind: 'codes', vocabulary: m.vocabulary, concept: m.concept, column: m.column, codes, ...(unsure.length && m === holder ? { uncertain: unsure } : {}) }
        : { kind: 'codes', vocabulary: m.vocabulary, concept: m.concept, column: m.column, codes: [], answer: 'unsure' };
    });
    const text = marked('text');
    if (text.length) facts.push({ kind: 'textbp', column: listed.column, codes: text });
    const calculated = marked('calculated');
    if (calculated.length) facts.push({ kind: 'calculated', column: listed.column, codes: calculated });
    facts.push({ kind: 'listed', column: listed.column, year: listed.year, rows: rows.length });
    sendFacts(shown, facts);
  });
  shown.replaceChildren(el('p', strings.listedFound(rows.length, rows.filter((_, i) => selects[i][2].dataset.likely).length, listed.year ?? 0), 'note'),
    filter, table, save);
  listedShown = { column: listed.column, year: listed.year ?? 0, columns, rows };
}

// The optional count of how often each chosen code is charted on the cohort's anaesthetics in the last year of the period.
// Its result is pasted here and kept as a fact, which the specification reads.
let chartedNow: Target['charted'] = null;
function chartedElement(target: Target) {
  const charted = target.charted!;
  chartedNow = charted;
  const box = el('div', undefined, 'sizes charted');
  box.append(el('h4', strings.chartedHeading), el('p', strings.chartedWhat(charted.from, charted.to), 'sizes-reason'),
    ...scriptNotes(charted, charted.from.slice(0, 7) !== charted.to.slice(0, 7)));
  if (!charted.sql) return box;
  const [code, copy] = queryBlock(charted.sql, 'charted-query');
  code.dataset.query = 'charted';
  copy.dataset.query = 'charted';
  const label = el('label', strings.chartedPasteLabel);
  const area = el('textarea', undefined, 'charted-paste') as HTMLTextAreaElement;
  area.rows = 4;
  area.spellcheck = false;
  label.append(area);
  const shown = el('div', undefined, 'charted-result');
  if (charted.kept) shown.append(el('p', strings.chartedKept, 'status good'));
  box.append(code, copy, label, askButton(strings.chartedRead, 'charted-read', () => {
    if (area.value.trim()) worker?.postMessage({ type: 'charted', text: area.value });
  }), shown);
  return box;
}

// The count by year: its result is pasted here, shown as a small table, and the two people say whether it looks right. Each
// audit question has a box of its own, so the result is shown in the box whose button was pressed.
let countBox: HTMLElement | null = null;
function yearCountBox() {
  const box = el('div', undefined, 'year-count');
  const label = el('label', strings.countPasteLabel);
  const area = el('textarea', undefined, 'count-paste') as HTMLTextAreaElement;
  area.rows = 5;
  label.append(area);
  const shown = el('div', undefined, 'count-result');
  box.append(el('p', strings.countPlan(readingsNow), 'sizes-reason script-note'),
    ...(cohortLargest ? [el('p', strings.countCostly(...cohortLargest), 'sizes-reason script-note')] : []),
    el('p', strings.countHow, 'note count-how'));
  box.append(label, askButton(strings.countRead, 'count-read', () => {
    if (!area.value.trim()) return;
    countBox = shown;
    worker?.postMessage({ type: 'year-count', text: area.value });
  }), shown);
  return box;
}

function showYearCount(years: (number | null)[][]) {
  const shown = countBox?.isConnected ? countBox : document.querySelector<HTMLElement>('.count-result');
  if (!shown) return;
  const table = el('table', undefined, 'count-table');
  const head = el('tr');
  // A fourth value, where the count gives one, is the number of anaesthetics with no kind recorded.
  const noKind = years.some((row) => row.length > 3);
  head.append(el('th', strings.countYear), el('th', strings.countAll), el('th', strings.countCohort), ...(noKind ? [el('th', strings.countNoKind)] : []));
  table.append(head);
  const cell = (value: number | null | undefined) => el('td', value === null || value === undefined ? strings.countUnderTen : value.toLocaleString('en-AU'));
  for (const [year, all, cohort, none] of years) {
    const row = el('tr');
    row.append(el('td', String(year)), cell(all), cell(cohort), ...(noKind ? [cell(none)] : []));
    table.append(row);
  }
  const actions = el('div', undefined, 'actions');
  // On a training database with fictional patients the numbers mean nothing, so the page keeps them without asking.
  const ask = trainingNow ? el('p', strings.countTraining, 'ask-text count-training')
    : el('p', years.length ? strings.countAsk : strings.countEmpty, 'ask-text');
  if (trainingNow) actions.append(askButton(strings.countKeep, 'count-keep', () => sendFacts(shown, [{ kind: 'count', answer: 'training', years }])));
  else for (const [answer, label] of [['right', strings.countRight], ['few', strings.countFew], ['many', strings.countMany], ['unsure', strings.notSure]] as const) {
    actions.append(askButton(label, `count-${answer}`, () => sendFacts(shown, [{ kind: 'count', answer, years }])));
  }
  // The earliest year with anaesthetics is offered as the start of the study period, where none has been entered.
  const from = (shown.closest('section.target') ?? document).querySelector<HTMLInputElement>('input.setting-date');
  if (from && !from.value && years.length) from.value = `${years[0][0]}-01-01`;
  const received = years.length
    ? [el('p', strings.countReceived(years.length, Number(years[0][0]), Number(years[years.length - 1][0])), 'status good count-received')] : [];
  shown.replaceChildren(...received, table, ask, actions);
}

// A block of text that the core wrote, with buttons that copy it and, where given, save it.
function textBlock(className: string, heading: string, what: string, text: string, copyLabel: string, saveLabel?: string, fileName?: string) {
  const block = el('div', undefined, `sizes ${className}`);
  block.append(el('h4', heading), el('p', what, 'sizes-reason'));
  const [code, copy] = queryBlock(text, `${className}-text`);
  code.classList.add('wrap-text');
  copy.textContent = copyLabel;
  const actions = el('div', undefined, 'actions');
  actions.append(copy);
  if (saveLabel && fileName) {
    const keep = el('button', saveLabel, 'secondary');
    keep.type = 'button';
    keep.addEventListener('click', () => save([text], 'text/plain', fileName));
    actions.append(keep);
  }
  block.append(code, actions);
  return block;
}

// The audit query: the source draft, shown last in the first phase as a reference, with what it reads and returns.
function auditSection(target: Target) {
  const draft = target.draft!;
  const audit = el('div', undefined, 'sizes audit');
  audit.dataset.restructured = String(!!draft.restructured);
  // A query restructured to start from the cohort may be run; the composition of the steps as they are is a reference.
  if (draft.restructured) audit.append(el('h4', strings.auditHeading), el('p', strings.auditRestructured, 'sizes-reason'));
  else audit.append(el('h4', strings.generatedHeading), el('p', strings.generatedWarning, 'sizes-reason status problem'));
  audit.append(el('p', strings.auditWhat, 'sizes-reason'));
  if (draft.tables.length) audit.append(el('p', strings.auditTables(draft.tables), 'sizes-reason'));
  audit.append(el('p', strings.auditCost, 'sizes-reason'));
  audit.append(el('p', draft.countsOnly ? strings.auditCounts : strings.auditRows, draft.countsOnly ? 'sizes-reason' : 'sizes-reason status problem'));
  // The query reaches the readings, so it may be run only as the script; otherwise it is shown as a reference only.
  const script = draft.script ?? { sql: '' };
  audit.dataset.script = String(!!script.sql);
  if (!script.sql) audit.append(el('p', strings.auditReferenceOnly, 'sizes-reason status problem'));
  audit.append(...scriptNotes(script, !!target.settings?.from && target.settings.from.slice(0, 7) !== (target.settings.to ?? '').slice(0, 7)));
  const sql = script.sql || draft.sql;
  const [code, copy] = queryBlock(sql, 'audit-query');
  code.dataset.query = 'audit';
  copy.dataset.query = 'audit';
  copy.textContent = strings.auditCopy;
  const keep = el('button', strings.auditSave, 'secondary');
  keep.type = 'button';
  keep.id = `save-audit-${target.name}`;
  keep.addEventListener('click', () => save([sql], 'text/plain', `${target.name}_source.sql`));
  const actions = el('div', undefined, 'actions');
  actions.append(copy, keep);
  audit.append(code, actions);
  return audit;
}

// The query last copied from step 4, with the point or part that offers it, so that step 7 can name that point beside the
// result of the same query and link back to it.
let lastCopied: { sql: string; target: string; id: string | null; heading: string; text: string } | null = null;
const sameQuery = (a: string, b: string) => a.replace(/\s+/g, ' ').trim() === b.replace(/\s+/g, ' ').trim();

function rememberCopied(sql: string, from: Element) {
  const section = from.closest<HTMLElement>('section.target');
  const item = from.closest<HTMLElement>('li.item');
  const part = from.closest<HTMLElement>('.sizes');
  const said = item?.querySelector('.ask-text, .question')?.textContent ?? '';
  lastCopied = { sql, target: section?.dataset.target ?? '', id: item?.dataset.id ?? null,
    heading: item ? '' : part?.querySelector('h4')?.textContent ?? '', text: plainLine(said).trim().split(/(?<=[.?])\s+/)[0] };
}

// The place in step 4 from which a query was copied, found again after the checklist has been drawn anew.
function copiedPlace() {
  if (!lastCopied) return null;
  const section = document.querySelector<HTMLElement>(`section.target[data-target="${CSS.escape(lastCopied.target)}"]`);
  if (!section) return null;
  if (lastCopied.id) return section.querySelector<HTMLElement>(`li.item[data-id="${CSS.escape(lastCopied.id)}"]`);
  return [...section.querySelectorAll<HTMLElement>('.sizes')].find((part) => part.querySelector('h4')?.textContent === lastCopied!.heading) ?? null;
}

// A plain query, exactly as the core wrote it, with a button that copies it.
function queryBlock(sql: string, className: string) {
  const code = el('pre', sql, `code ${className}`);
  const copy = el('button', strings.copyQuery, 'secondary copy-query');
  copy.type = 'button';
  copy.addEventListener('click', async () => {
    if (!className.endsWith('-text')) rememberCopied(sql, copy);
    try {
      await navigator.clipboard.writeText(sql);
      copy.dataset.copied = 'true';
      setTimeout(() => delete copy.dataset.copied, 2000);
    } catch {
      // Without clipboard access the query can still be selected and copied by hand.
    }
  });
  return [code, copy];
}

// The queries that answer an item. A query that an earlier item on the page already shows is named
// rather than shown again, so that each query appears once.
function queryElements(item: Item, queries: Map<string, Query>, shown: Set<string>) {
  // The count by year has no query state of its own, and its query is shown whenever the item is open.
  if (item.status === 'answered' || (!item.queryState && !item.queryIds?.includes('yearcount'))) return [];
  const parts: HTMLElement[] = [];
  // Where a colleague can answer from knowledge, the query is the alternative, so it says so first.
  if (item.ask) parts.push(el('p', strings.queryAlternative, 'note'));
  if (item.queryState) {
    const state = el('p', strings.queryStates[item.queryState] ?? '', 'query-state');
    state.dataset.state = item.queryState;
    parts.push(state);
  }
  if (item.queryReason) parts.push(el('p', item.queryReason, 'query-reason'));
  // The core profile's queries are shown in the section for the central OMOP team, and named here.
  if ((item.queryIds ?? []).some((id) => id.startsWith('profile:'))) parts.push(el('p', strings.queryInProfile, 'note query-profile'));
  for (const id of item.queryIds ?? []) {
    if (id.startsWith('profile:')) continue;
    const query = queries.get(id);
    if (!query) continue;
    if (shown.has(id)) {
      parts.push(el('p', strings.queryShownEarlier, 'note query-earlier'));
      continue;
    }
    shown.add(id);
    const [code, copy] = queryBlock(query.sql, 'query');
    code.dataset.query = id;
    copy.dataset.query = id;
    parts.push(code, copy);
    // A count to be asked again on the production copy keeps its query, to be run there, but takes no paste here.
    if (id === 'yearcount' && !item.again) parts.push(yearCountBox());
  }
  return parts;
}

// The checklist of each target query: first what has just been answered, then the open items that
// existing SQL could settle, then those that need something else, and the answered items folded away.
function itemElement(item: Item, previous?: string, queries?: Map<string, Query>, shown?: Set<string>) {
  const line = el('li', undefined, 'item');
  line.dataset.id = item.id;
  line.dataset.status = item.status;
  if (item.again) line.dataset.again = 'true';
  if (previous !== undefined && previous !== 'answered' && item.status === 'answered') line.dataset.new = 'true';
  const mark = el('span', undefined, 'mark');
  mark.setAttribute('aria-hidden', 'true');
  const body = el('div', undefined, 'item-body');
  body.append(el('span', strings.statusNames[item.status], 'visually-hidden'));
  if (item.again) body.append(el('p', strings.againMark, 'status again-mark'));
  // Who can answer the point, in a few words, before anything else.
  if (item.status !== 'answered') {
    const query = item.queryState === 'ready' || item.queryState === 'waiting' || !!item.queryIds?.includes('yearcount');
    const who = item.ask && item.stage === 'source' && (!item.fact || !!item.ask.choose) ? (query ? 'knowledgeOrQuery' : 'knowledge')
      : query ? 'query'
        : /^The central OMOP team/.test(item.actor) ? 'omop'
          : /^The clinician/.test(item.actor) ? 'clinician'
            : item.group === 'sql' || /^The team/.test(item.actor) ? 'team' : '';
    if (who) body.append(el('p', strings.whoAnswers[who], 'who-answers'));
  }
  // The question for the colleague, where there is one, is what he reads; the checklist's own account of the point is
  // folded beneath it, one click away.
  const asking = item.status !== 'answered' && item.ask && item.stage === 'source' && (!item.fact || !!item.ask.choose);
  // The visible line leaves out vocabulary names and concept numbers, which the folded detail keeps.
  const plain = plainLine(item.question);
  if (!asking) body.append(el('p', plain, 'question'));
  // An answer that a person gave stays in view, with the button that withdraws it so that the question is asked again.
  // The sentences that record the answer move out of the folded account and stand beside the button.
  let inHand = item.inHand;
  if (item.withdraw?.length) {
    const sentences = item.inHand.split(/(?<=\.)\s+(?=[A-Z])/);
    const given = sentences.filter((s) => /^(You|A person) /.test(s));
    inHand = sentences.filter((s) => !given.includes(s)).join(' ');
    const answer = el('div', undefined, 'answer-given');
    if (given.length) answer.append(el('p', given.join(' '), 'answer-text'));
    const withdraw: HTMLButtonElement = askButton(strings.withdrawAnswer, 'secondary withdraw-answer', () => withdrawFacts(item, withdraw));
    answer.append(withdraw);
    body.append(answer);
  }
  if (item.status !== 'answered') {
    if (asking) body.append(askElement(item));
    if (queries && shown) body.append(...queryElements(item, queries, shown));
  }
  const more = el('details', undefined, 'item-more');
  more.append(el('summary', strings.itemMore));
  if (asking) more.append(el('p', item.question, 'question'));
  else if (plain !== item.question) more.append(el('p', item.question, 'question-full'));
  if (item.intent) more.append(el('p', item.intent, 'intent'));
  if (item.status !== 'answered') {
    if (item.route && item.status === 'open') more.append(el('p', item.route, 'route'));
    // Where the colleague is asked a join or a filter, the fold says what settles it in the same terms as the buttons:
    // an answer here, or a SQL file of the team's that does the same, and not the core's account of SQL files alone.
    const settles = asking && item.ask ? strings.askSettles[item.ask.kind] : undefined;
    if (settles) more.append(el('p', settles, 'needed'));
    else {
      more.append(el('p', item.needed, 'needed'));
      if (item.actor) more.append(el('p', item.actor, 'actor'));
    }
  }
  const weight = item.blocking ? (item.status === 'answered' ? strings.blockingSettled : strings.blocking) : strings.notBlocking;
  more.append(el('p', `${inHand} ${weight}`.trim(), 'in-hand'));
  body.append(more);
  line.append(mark, body);
  return line;
}

function itemList(group: string, entries: HTMLElement[]) {
  const list = el('ul', undefined, 'items');
  list.dataset.group = group;
  list.append(...entries);
  return list;
}

// The audit's settings: the study period, applied to the anaesthetic's start, and the kinds of anaesthetic that count.
function settingsElement(target: Target) {
  const box = el('div', undefined, 'sizes settings');
  box.append(el('h4', strings.settingsHeading), el('p', strings.settingsWhat, 'sizes-reason'));
  const date = (label: string, value?: string | null) => {
    const wrap = el('label', label);
    const input = el('input', undefined, 'setting-date') as HTMLInputElement;
    input.type = 'date';
    input.value = value ?? '';
    wrap.append(input);
    return [wrap, input] as const;
  };
  const [fromLabel, from] = date(strings.settingsFrom, target.settings?.from);
  const [toLabel, to] = date(strings.settingsTo, target.settings?.to);
  box.append(fromLabel, toLabel);
  // Where the applied period begins before the first year in which the count by year shows records, the page says so.
  const counted = target.rows.find((item) => item.id === 'count-by-year')?.years ?? [];
  const firstYear = counted.find(([, all]) => all !== 0)?.[0];
  const fromYear = target.settings?.from ? Number(target.settings.from.slice(0, 4)) : NaN;
  if (firstYear && fromYear < firstYear) box.append(el('p', strings.settingsBeforeCount(fromYear, firstYear), 'status problem settings-before-count'));
  const chosen = new Set(target.settings?.kinds ?? []);
  const boxes: [number, HTMLInputElement][] = [];
  if (target.kinds?.length) {
    const kinds = el('fieldset', undefined, 'setting-kinds');
    kinds.append(el('legend', strings.settingsKinds));
    if (target.kinds_cost) kinds.append(el('p', target.kinds_cost, 'note kinds-cost'));
    for (const [concept, name] of target.kinds) {
      const wrap = el('label', ` ${name}`);
      const tick = el('input') as HTMLInputElement;
      tick.type = 'checkbox';
      tick.checked = chosen.has(concept);
      tick.dataset.concept = String(concept);
      wrap.prepend(tick);
      kinds.append(wrap);
      boxes.push([concept, tick]);
    }
    box.append(kinds);
  }
  // The decisions that the two clinicians make together, each with its options, the present choice and a short note. Only
  // the decisions that can change the answer to this question are offered, and the page says which it leaves out.
  const decisions = el('div', undefined, 'decisions');
  const bearing = target.decisions ?? strings.decisions.map((d) => d.key);
  const leftOut = strings.decisions.filter((d) => !bearing.includes(d.key)).map((d) => strings.decisionTopics[d.key] ?? d.key);
  decisions.append(el('h4', strings.decisionsHeading));
  if (bearing.length) decisions.append(el('p', strings.decisionsWhat, 'sizes-reason'));
  if (!bearing.length) decisions.append(el('p', strings.decisionsNone, 'note decisions-none'));
  else if (leftOut.length) decisions.append(el('p', strings.decisionsLeftOut(leftOut), 'note decisions-left-out'));
  const chosenNow = target.settings ?? {};
  const pickers: [string, HTMLSelectElement | HTMLInputElement][] = [];
  const notes: [string, HTMLInputElement][] = [];
  for (const decision of strings.decisions.filter((d) => bearing.includes(d.key))) {
    const wrap = el('div', undefined, 'decision');
    wrap.dataset.decision = decision.key;
    const label = el('label', decision.title);
    let input: HTMLSelectElement | HTMLInputElement;
    if (decision.key === 'floor' || decision.key === 'ceiling') {
      const key = decision.key;
      input = el('input', undefined, `decision-${key}`) as HTMLInputElement;
      input.type = 'number';
      input.min = '1';
      input.max = '299';
      input.value = chosenNow[key] ? String(chosenNow[key]) : '';
    } else {
      input = el('select', undefined, 'decision-choice') as HTMLSelectElement;
      for (const [value, text] of decision.options) {
        const o = el('option', text);
        o.value = value;
        input.append(o);
      }
      input.value = ((chosenNow as Record<string, unknown>)[decision.key] as string) ?? decision.options[0][0];
    }
    label.append(input);
    const note = el('input', undefined, 'decision-note') as HTMLInputElement;
    note.type = 'text';
    note.maxLength = 300;
    note.placeholder = strings.decisionNote;
    note.value = chosenNow.notes?.[decision.key] ?? '';
    wrap.append(label, note);
    if (decision.applied === false) wrap.append(el('p', strings.decisionRecorded, 'note'));
    decisions.append(wrap);
    pickers.push([decision.key, input]);
    notes.push([decision.key, note]);
  }
  box.append(decisions);
  box.append(askButton(strings.settingsApply, 'settings-apply', () => {
    // The settings are shared by every audit question, so a decision that this question does not offer keeps its value.
    const kept = target.settings ?? {};
    const settings: Record<string, unknown> = { from: from.value || null, to: to.value || null, kinds: boxes.filter(([, t]) => t.checked).map(([c]) => c),
      database: kept.database ?? null,
      ...Object.fromEntries(strings.decisions.filter((d) => !bearing.includes(d.key)).map((d) => [d.key, (kept as Record<string, unknown>)[d.key] ?? null])) };
    for (const [key, input] of pickers) {
      if (key === 'floor' || key === 'ceiling') settings[key] = input.value ? Number(input.value) : null;
      else settings[key] = input.value === (strings.decisions.find((d) => d.key === key)?.options[0][0]) ? null : input.value;
    }
    settings.notes = { ...Object.fromEntries(Object.entries(kept.notes ?? {}).filter(([k]) => !bearing.includes(k))),
      ...Object.fromEntries(notes.filter(([, n]) => n.value.trim()).map(([k, n]) => [k, n.value.trim()])) };
    clearStale();
    beginWorking(box);
    worker?.postMessage({ type: 'settings', settings: JSON.stringify(settings) });
  }));
  return box;
}

// The last thing that was found about an item, so that the list of what remains says what was found and not only the question.
function lastSentence(text: string) {
  const sentences = text.split(/(?<=\.)\s+(?=[A-Z])/).filter((t) => /^(You|A query|The count|Because)/.test(t));
  return sentences.slice(-2).join(' ');
}

// A line as the page shows it: the names of mapping vocabularies and the numbers of concepts are left to the folded detail.
function plainLine(text: string) {
  return text
    .replace(/\s+under\s+[A-Z][A-Z0-9_]*_[A-Z0-9_]*(?:(?:,\s*|,?\s+and\s+)[A-Z][A-Z0-9_]*_[A-Z0-9_]*)*/g, '')
    .replace(/\s*\([^()]*\bconcept \d+\)/g, '')
    .replace(/\s+([,.])/g, '$1');
}

// One point that remains, with who can settle it, the line that the ending shows, and the line for the team's note.
type Point = { who: 'you' | 'team' | 'clinician' | 'again'; text: string; note?: string; ids: string[] };

// What remains, in the order that the core gives, one point for each thing to settle: the routes that rest on one name
// that is not visible are one point. Each point belongs to the colleague now, to the team that looks after the reporting
// database, or to the clinician after the meeting, and says in one sentence what to do.
function remainingPoints(target: Target, needs: NonNullable<Target['needs']>): Point[] {
  const byId = new Map(target.rows.map((item) => [item.id, item]));
  const points: Point[] = [];
  const missingSeen = new Map<string, Point>();
  const line = (...parts: string[]) => parts.join(' ').replace(/\s+/g, ' ').trim();
  for (const id of needs.remaining) {
    const item = byId.get(id);
    if (!item) continue;
    const question = plainLine(item.question);
    const found = item.status === 'answered' ? '' : plainLine(lastSentence(item.inHand));
    if (id.startsWith('route-')) {
      const missing = item.missing ?? '';
      const seen = missing ? missingSeen.get(missing) : undefined;
      if (seen) {
        seen.ids.push(id);
        continue;
      }
      const point: Point = missing
        ? { who: 'team', text: strings.endingTeamRoute(missing), note: strings.teamNoteRoute(missing), ids: [id] }
        : { who: 'team', text: line(question, strings.endingByTeam), note: strings.teamNoteOther(question), ids: [id] };
      if (missing) missingSeen.set(missing, point);
      points.push(point);
    } else if (item.top || id.startsWith('charted-')) {
      const action = item.ask && !item.fact ? strings.endingByQuestion : item.queryState === 'ready' || item.queryIds?.length ? strings.endingByQuery : '';
      points.push({ who: 'you', text: line(question, id === 'count-by-year' && item.years ? '' : action), ids: [id] });
    } else if (id === 'count-by-year') {
      // A count that has run is not described as still to run.
      points.push(item.years ? { who: 'clinician', text: line(question, found, strings.endingCountSeen), ids: [id] }
        : { who: 'you', text: line(question, strings.endingByQuery), ids: [id] });
    } else if (item.ask && (!item.fact || item.ask.choose)) {
      points.push({ who: 'you', text: line(question, strings.endingByQuestion), ids: [id] });
    } else if (item.kind === 'codes' && (item.fact === 'noted' || item.fact === 'unsure') && target.listed && !target.listed.waiting) {
      // A code marked not sure is chosen again in the room, from the list with its count beside it.
      points.push({ who: 'you', text: line(question, strings.endingChooseAgain), ids: [id] });
    } else if (item.queryState === 'ready' || (item.queryState === 'waiting' && item.queryIds?.length)) {
      points.push({ who: 'you', text: line(question, strings.endingByQuery), ids: [id] });
    } else if (item.queryState === 'large' || (item.fact === 'unsure' && !item.queryIds?.length)) {
      points.push({ who: 'team', text: line(question, found, strings.endingByTeam), note: item.note || strings.teamNoteOther(question), ids: [id] });
    } else {
      points.push({ who: 'clinician', text: line(question, found, strings.endingClinician), ids: [id] });
    }
  }
  // What a training database could not settle is to be asked again on the production copy, as a group of its own.
  for (const id of needs.again ?? []) {
    const item = byId.get(id);
    if (item) points.push({ who: 'again', text: plainLine(item.question), ids: [id] });
  }
  return points;
}

// Where the audit stands, in plain words, with what remains and who can settle each part, and where to keep the state.
function endingElement(target: Target, needs: NonNullable<Target['needs']>, ready: boolean, points: Point[], lessShown = { later: 0, now: 0 }) {
  const box = el('div', undefined, 'sizes ending');
  box.append(el('h4', strings.endingHeading), el('p', strings.endingWhat, 'sizes-reason'));
  box.append(el('p', strings.endingSettled(needs.settled), 'note'));
  if (points.length) {
    box.append(el('p', strings.endingRemaining, 'note'));
    for (const [who, heading] of [['you', strings.endingYouHeading], ['team', strings.endingTeamHeading], ['clinician', strings.endingClinicianHeading],
      ['again', strings.endingAgainHeading]] as const) {
      const mine = points.filter((point) => point.who === who);
      if (!mine.length) continue;
      const list = el('ul', undefined, 'remaining');
      list.dataset.who = who;
      for (const point of mine) {
        const entry = el('li', point.text);
        entry.dataset.ids = point.ids.join(' ');
        list.append(entry);
      }
      box.append(el('p', heading, 'remaining-heading'), list);
      // The team's questions, gathered into one note, ready to copy and send.
      if (who === 'team') {
        box.append(textBlock('team-note', strings.teamNoteHeading, strings.teamNoteWhat,
          strings.teamNote(mine.map((point) => point.note ?? point.text)), strings.teamNoteCopy));
      }
    }
  } else box.append(el('p', ready ? strings.endingNothing : strings.notReadyYet, 'note'));
  // Where 4.4 shows the less certain points is said only where the page's own count of them agrees with the core's.
  const placed = lessShown.later + lessShown.now === needs.lessCertain;
  if (needs.lessCertain) box.append(el('p', strings.endingLessCertain(needs.lessCertain, placed ? lessShown.later : 0, placed ? lessShown.now : 0), 'note'));
  const database = target.settings?.database;
  if (database && strings.databaseRecorded[database]) box.append(el('p', strings.databaseRecorded[database], 'note ending-database'));
  box.append(el('p', strings.endingSave, 'note'));
  box.append(askButton(strings.saveState, 'secondary ending-save', () => worker?.postMessage({ type: 'state-zip' })));
  return box;
}

function targetSection(target: Target, before?: { answered: number; statuses: Map<string, string>; points?: string[] }) {
  listedNow = target.listed ?? null;
  listedTarget = target;
  cohortLargest = target.listed?.largest ?? null;
  readingsNow = target.listed?.readings ?? null;
  trainingNow = target.settings?.database === 'training';
  const section = el('section', undefined, 'target');
  section.dataset.target = target.name;
  // The audit question in plain words heads its checklist, with the name of its file after it in smaller type.
  const heading = el('h3');
  if (target.title) heading.append(el('span', target.title, 'target-title'), document.createTextNode(' '));
  heading.append(el('span', `${target.name}.sql`, 'target-name'));
  section.append(heading);
  // The first stage leads: what a person still needs to do before the audit query can be written.
  const first = target.stages?.source ?? target.counts;
  const needs = target.needs ?? { questions: 0, queries: 0, other: first.open, lessCertain: first.partly, settled: first.answered, remaining: [] };
  // Ready means that nothing remains: no open item, and no doubt that a person raised and nothing has yet settled.
  const sourceReady = needs.remaining.length === 0 && target.steps;
  // The tally counts the points of the list at the end, by who can settle them, so that the two always agree.
  const points = target.needs ? remainingPoints(target, needs) : [];
  const count = (who: Point['who']) => points.filter((point) => point.who === who).length;
  // Where an answer leaves more to settle than before, the page says what was learned that added to it.
  if (before?.points && points.length > before.points.length) {
    const added = points.filter((point) => !before.points!.includes(point.text)).map((point) => point.text);
    if (added.length) section.append(el('p', strings.remainingRose(points.length - before.points.length, added), 'status problem remaining-rose'));
  }
  section.append(el('p', target.needs ? strings.needs(count('you'), count('team'), count('clinician'), count('again'))
    : strings.needs(needs.questions + needs.queries, 0, needs.other), 'tally'));
  section.append(el('p', sourceReady ? (count('again') ? strings.readyTraining(count('again')) : strings.readyNow(needs.lessCertain)) : strings.notReadyYet,
    sourceReady ? 'status good stage-verdict' : 'status stage-verdict'));
  section.append(databaseElement(target));
  // Searches gather the meanings of the codes they look for, so that the candidates can be chosen among them.
  searchGroups = new Map();
  for (const item of target.rows) {
    if (item.ask?.kind === 'codes' && item.ask.group && item.stage === 'source') {
      const list = searchGroups.get(item.ask.group) ?? [];
      list.push({ concept: item.ask.concept!, vocabulary: item.ask.vocabulary!, meaning: item.ask.meaning ?? item.ask.concept!, column: item.ask.column! });
      searchGroups.set(item.ask.group, list);
    }
  }
  if (target.routes?.length) {
    const routes = el('div', undefined, 'routes');
    routes.append(...target.routes.map((sentence) => el('p', sentence, 'note')));
    section.append(routes);
  }
  // 4.2: the questions that a colleague can answer from knowledge, as one list to send.
  if (target.questions) {
    const questions = textBlock('questions', strings.questionsHeading, strings.questionsWhat, target.questions, strings.questionsCopy);
    const who = el('label', strings.whoLabel);
    const input = el('input', undefined, 'fact-who') as HTMLInputElement;
    input.type = 'text';
    input.maxLength = 80;
    // The name stays from one render to the next, so that it is kept with every answer of the meeting.
    input.value = whoNow;
    input.addEventListener('input', () => (whoNow = input.value));
    who.append(input);
    questions.append(who);
    section.append(questions);
  }

  // 4.3: the table sizes query, before the points, because every other short query waits for the sizes it gives.
  if (target.sizes) {
    const sizes = el('div', undefined, 'sizes');
    sizes.append(el('h4', strings.sizesHeading), el('p', target.sizes.reason, 'sizes-reason'), el('p', strings.sizesHow, 'sizes-reason'));
    const [code, copy] = queryBlock(target.sizes.sql, 'sizes-query');
    code.dataset.query = 'sizes';
    copy.dataset.query = 'sizes';
    sizes.append(code, copy);
    section.append(sizes);
  }
  // The core profile's queries, for the central OMOP team, in a short section of their own, which is placed
  // under the heading for the later OMOP release.
  let profileSection: HTMLElement | null = null;
  if (target.profile?.length) {
    const profile = el('div', undefined, 'sizes profile-queries');
    profileSection = profile;
    profile.append(el('h4', strings.profileHeading), el('p', strings.profileWhat, 'sizes-reason'));
    for (const query of target.profile) {
      const [code, copy] = queryBlock(query.sql, 'profile-query');
      code.dataset.query = query.id;
      copy.dataset.query = query.id;
      profile.append(code, copy);
    }
  }
  const queries = new Map((target.queries ?? []).map((query) => [query.id, query]));
  const shown = new Set<string>();
  const offered = (item: Item) => itemElement(item, undefined, queries, shown);

  const previous = (item: Item) => before?.statuses.get(item.id);
  // The items settled by the action just taken, compared with the render before it, so that the note is true only now. They
  // are listed first within 4.4, the part to which they belong.
  const fresh = target.rows.filter((item) => item.status === 'answered' && before && previous(item) !== undefined && previous(item) !== 'answered');

  // Within a group, the open items come before those partly answered, and blocking items first, so
  // that what stops the simulation is read first. The order is otherwise the checklist's own.
  const rank = (item: Item) => (item.status === 'open' ? 0 : 2) + (item.blocking ? 0 : 1);
  const ordered = (list: Item[]) => list.map((item, i) => ({ item, i })).sort((a, b) => rank(a.item) - rank(b.item) || a.i - b.i).map(({ item }) => item);
  const firstStage = (item: Item) => (item.stage ?? 'source') === 'source';
  const later = target.rows.filter((item) => item.stage === 'release' && !fresh.includes(item));
  // What needs the colleague now: a question not yet answered, or a short query ready to run.
  const needsYou = (item: Item) => !item.again && ((item.ask && (!item.fact || !!item.ask.choose)) || item.queryState === 'ready' || !!item.queryIds?.includes('yearcount'));
  // The routes that rest on one name that is not visible are one point, so the page lists only the first of them.
  const missingShown = new Set<string>();
  const once = (item: Item) => {
    if (!item.id.startsWith('route-') || !item.missing) return true;
    if (missingShown.has(item.missing)) return false;
    missingShown.add(item.missing);
    return true;
  };
  // 4.4: the points that need the colleague now, each with its question or short query, the count by year among them.
  const open = target.rows.filter((item) => item.group !== 'answered' && firstStage(item)).filter(once);
  const yours = ordered(open.filter(needsYou));
  section.append(el('h4', strings.groupYou));
  if (fresh.length) {
    section.append(el('p', sinceAnalysis ? strings.newlyAnsweredNow(fresh.length) : strings.newlyAnswered(fresh.length), 'status good fresh-note'),
      el('h5', sinceAnalysis ? strings.groupNewNow : strings.groupNew));
    section.append(itemList('new', fresh.map((item) => itemElement(item, previous(item)))));
  }
  if (yours.length) section.append(el('p', strings.groupYouNote, 'note'), itemList('you', yours.map(offered)));
  else section.append(el('p', strings.groupYouNone, 'note'));
  // The answers that a person gave stay in view, each beside the button that changes it, and none is folded away: every
  // item that holds such an answer and is not already shown above is listed here.
  const given = (item: Item) => !!item.withdraw?.length && !fresh.includes(item) && !yours.includes(item);
  const givenItems = target.rows.filter(given).filter(once);
  if (givenItems.length) section.append(el('h4', strings.groupGiven), el('p', strings.groupGivenNote, 'note'),
    itemList('given', givenItems.map((item) => (item.status === 'answered' ? itemElement(item) : offered(item)))));
  const rest = open.filter((item) => !needsYou(item) && !given(item));
  // The points that 4.8 calls less certain: partly settled, not among what remains, and not to be asked again. They are a
  // part of the other points folded away below, and may be among the points that need the colleague now as well.
  const lessCertain = (item: Item) => item.status === 'partly' && !(needs.remaining ?? []).includes(item.id) && !item.again;
  const lessShown = { later: rest.filter(lessCertain).length, now: yours.filter(lessCertain).length };
  if (rest.length) {
    const folded = el('details', undefined, 'later');
    folded.append(el('summary', strings.groupLater(rest.length, lessShown.later + lessShown.now === needs.lessCertain ? lessShown.later : 0)));
    const sql = ordered(rest.filter((item) => item.group === 'sql'));
    if (sql.length) folded.append(el('h4', strings.groupSql), el('p', strings.groupSqlNote, 'note'), itemList('sql', sql.map(offered)));
    const other = ordered(rest.filter((item) => item.group !== 'sql'));
    if (other.length) folded.append(el('h4', strings.groupOther), el('p', strings.groupOtherNote, 'note'), itemList('other', other.map(offered)));
    section.append(folded);
  }

  const answered = target.rows.filter((item) => item.group === 'answered' && !fresh.includes(item) && !given(item) && firstStage(item)).filter(once);
  if (answered.length) {
    const folded = el('details', undefined, 'answered');
    folded.append(el('summary', strings.groupAnswered(answered.length)), itemList('answered', answered.map((item) => itemElement(item))));
    section.append(folded);
  }
  // The items that the answer to this question does not depend on, which do not count against the first phase.
  const unneeded = target.rows.filter((item) => item.stage === 'unneeded' && !fresh.includes(item) && !given(item));
  if (unneeded.length) {
    const folded = el('details', undefined, 'unneeded');
    folded.append(el('summary', strings.groupUnneeded(unneeded.length)), el('p', strings.unneededWhat, 'note'),
      itemList('unneeded', unneeded.map((item) => itemElement(item))));
    section.append(folded);
  }
  // 4.5: once the count by year has been seen, the codes are chosen from the list of what is charted on the cohort.
  if (target.listed && !target.listed.waiting) section.append(listedElement(target));
  // 4.6: the study period, the kinds of anaesthetic and the decisions; 4.7: the optional count of the chosen codes.
  section.append(settingsElement(target));
  if (target.charted) section.append(chartedElement(target));
  // The ending, whatever the state of the checklist: what is settled, what remains and who can settle it, the button
  // that saves the state, and the specification, in which each open point is an unsettled assumption.
  section.append(endingElement(target, needs, sourceReady, points, lessShown));
  if (target.specification) {
    section.append(textBlock('specification', strings.specHeading, sourceReady ? strings.specWhat : strings.specWhatOpen,
      target.specification, strings.specCopy, strings.specSave, `${target.name}_specification.txt`));
  }
  if (!sourceReady && target.draft) section.append(el('p', strings.auditWaiting, 'note audit-waiting'));
  // The developer's detail: the readiness statement in full, folded away.
  const readiness = el('details', undefined, 'readiness');
  readiness.append(el('summary', strings.readinessInFull), el('pre', target.readiness, 'code'));
  section.append(readiness);
  // The reference query waits for a study period, because without one it reads every anaesthetic on record.
  if (sourceReady && target.draft && !target.draft.waiting && target.settings?.from) section.append(auditSection(target));
  else if (sourceReady && target.draft) section.append(el('p', strings.auditNeedsPeriod, 'note audit-waiting'));

  // Everything that only the later OMOP release needs, folded away under one heading.
  if (later.length || target.profile?.length) {
    const release = el('details', undefined, 'release');
    release.append(el('summary', strings.releaseHeading), el('p', strings.releaseWhat, 'note'));
    if (target.stageVerdicts?.[1]) release.append(el('p', target.stageVerdicts[1], 'status'));
    if (profileSection) release.append(profileSection);
    const open = ordered(later.filter((item) => item.status !== 'answered' && !given(item)));
    if (open.length) release.append(itemList('release', open.map(offered)));
    const done = later.filter((item) => item.status === 'answered' && !given(item));
    if (done.length) release.append(itemList('release-answered', done.map((item) => itemElement(item))));
    section.append(release);
  }
  return section;
}

// What led to the checklist on the page: an analysis, a pasted result, or an answer given on the page.
type Cause = 'analysis' | 'paste' | 'answer';

function renderChecklist(boundary: Boundary, cause: Cause = 'analysis') {
  // After a paste or an answer, the checklist is compared with the render just before it, so that what is said to be newly
  // settled is what that action settled, and nothing older stays on the page.
  const previous = snapshot;
  sinceAnalysis = cause !== 'analysis';
  checksText = boundary.checks ?? '';
  profileText = boundary.profile ?? '';
  hasResult = true;
  const problem = boundary.ok ? '' : (strings.boundaryProblems[boundary.problem ?? 'other'] ?? strings.boundaryProblems.other);
  $('t-boundary-problem').hidden = boundary.ok;
  text('t-boundary-problem', problem);
  const targets = boundary.targets ?? [];
  $('t-no-checklist').hidden = !boundary.ok || targets.length > 0;
  text('t-no-checklist', strings.noChecklist);
  $('t-checklist-intro').hidden = targets.length === 0;
  $('boundary-notes').replaceChildren(...(boundary.notes ?? []).map((note) => el('p', note, 'status problem')));
  $('checklists').replaceChildren(...targets.map((target) => targetSection(target, previous?.get(target.name))));
  // The list last read stays on the page, with what has been marked, for as long as its year is the one chosen.
  if (listedShown && listedNow && listedNow.column === listedShown.column && listedNow.year === listedShown.year && listedNow.sql) {
    const target = targets.find((t) => t.listed?.column === listedShown!.column);
    if (target) {
      listedTarget = target;
      listedBox = document.querySelector<HTMLElement>(`section.target[data-target="${CSS.escape(target.name)}"] .listed`);
      showListed(listedShown.columns, listedShown.rows);
    }
  }

  // What changed since the previous analysis, across the target queries that both have.
  let newly = 0;
  for (const target of targets) {
    const before = previous?.get(target.name);
    if (!before) continue;
    newly += target.rows.filter((item) => item.status === 'answered' && before.statuses.has(item.id) && before.statuses.get(item.id) !== 'answered').length;
  }
  const compared = targets.filter((target) => previous?.has(target.name)).length;
  // After an answer the confirmation stands beside the button that was pressed, so the line at the top is for an analysis or a
  // paste only, and it says what actually happened.
  $('t-changes').hidden = !previous || compared === 0 || cause === 'answer';
  text('t-changes', cause === 'paste' ? strings.changesAfterPaste(newly) : strings.changes(newly, compared));

  snapshot = new Map(
    targets.map((target) => [
      target.name,
      { answered: (target.stages?.source ?? target.counts).answered, statuses: new Map(target.rows.map((item) => [item.id, item.status])),
        points: target.needs ? remainingPoints(target, target.needs).map((point) => point.text) : [] },
    ]),
  );
  $('t-download-holds').hidden = !boundary.ok;
  $('d-summary').hidden = !boundary.ok;
  text('t-summary', boundary.summary ?? '');
}

function render(result: Result) {
  renderChecklist(result.boundary);
  $('checklist').dataset.seconds = result.seconds.toFixed(1);
  zip = result.zip;
  checkScript = result.checkScript;
  names = new Map(result.index);
  text('t-files-sentence', result.summary.sentences[0]);
  text('t-found-sentence', result.summary.sentences[1]);
  $('t-no-headers').hidden = !result.noHeaders;

  $('t-checks-used').hidden = !result.checks;
  text('t-checks-used', result.checks?.used ?? '');
  $('t-checks-unanswered').hidden = !result.checks?.unanswered;
  text('t-checks-unanswered', result.checks?.unanswered ?? '');
  $('t-checks-no-headers').hidden = !result.checks?.noHeaders;

  $('t-nothing-unread').hidden = result.summary.unread.length > 0;
  text('t-nothing-unread', result.nothingUnread);
  $('unread').replaceChildren(
    ...result.summary.unread.flatMap(([label, count]) => [el('dt', label), el('dd', String(count))]),
  );

  const sections: HTMLElement[] = [];
  // Every file in the inventory is shown. A file without its own wording appears under its file name.
  const described = new Set(packFiles.map((entry) => entry.file));
  const others = Object.keys(result.pack).filter((file) => !described.has(file)).sort();
  for (const { file, title, explanation } of [
    ...packFiles,
    ...others.map((file) => ({ file, title: '', explanation: '' })),
  ]) {
    if (!(file in result.pack)) continue;
    const section = el('section', undefined, 'file');
    const heading = el('h3', title);
    heading.append(el('span', file, 'file-name'));
    section.append(heading);
    if (explanation) section.append(el('p', explanation));
    if (file.endsWith('.csv')) {
      const [header, ...rows] = parseCsv(result.pack[file]);
      const exact = el('details');
      exact.append(el('summary', strings.showExactly), el('pre', result.pack[file], 'code'));
      section.append(grid(header, rows, ['elements', 'columns', 'expression']), exact);
    } else {
      section.append(el('pre', result.pack[file], 'code'));
    }
    sections.push(section);
  }
  $('pack').replaceChildren(...sections);

  $('index').replaceChildren(
    ...result.index.map(([number, name]) => {
      const item = el('li', name);
      item.value = number;
      return item;
    }),
  );
}

function showRun(result: RunResult) {
  const s = sandboxStrings;
  const message = $('t-query-message');
  const count = result.count ?? 0;
  const shown = result.rows?.length ?? 0;
  message.className = result.status === 'ok' ? 'status good' : 'status problem';
  if (result.status === 'unreadable') message.textContent = s.unreadable;
  else if (result.status === 'unsupported') message.textContent = s.unsupported;
  else if (result.status === 'database-error') message.textContent = s.databaseError;
  else if (count === 0) message.textContent = s.returnedNone;
  else if (count > shown) message.textContent = s.returnedFirst(count, shown);
  else message.textContent = s.returned(count);

  $('t-database-message').hidden = result.status !== 'database-error';
  text('t-database-message', result.message ?? '');
  $('query-table').replaceChildren(
    ...(result.status === 'ok' && count > 0 ? [grid(result.columns ?? [], result.rows ?? [])] : []),
  );
  $('d-translated').hidden = !result.translated;
  text('t-translated', result.translated ?? '');
  // Beside a result with rows: a button that copies it with its headers, as a results grid would, ready to paste in step 4,
  // and, where the query is the one last copied from step 4, a line that names the point that asked for it.
  const extra: HTMLElement[] = [];
  if (result.status === 'ok' && count > 0) {
    const rows = [result.columns ?? [], ...(result.rows ?? []).map((row) => row.map((value) => value ?? 'NULL'))];
    const copy = askButton(strings.practiceCopyResult, 'secondary copy-result', async () => {
      try {
        await navigator.clipboard.writeText(rows.map((row) => row.join('\t')).join('\n') + '\n');
        copy.dataset.copied = 'true';
        setTimeout(() => delete copy.dataset.copied, 2000);
      } catch {
        // Without clipboard access the table can still be selected and copied by hand.
      }
    });
    const actions = el('div', undefined, 'actions');
    actions.append(copy);
    extra.push(actions);
    if (count > shown) extra.push(el('p', strings.practiceCopyPart(shown), 'note'));
  }
  const ran = $<HTMLTextAreaElement>('sql').value;
  if (lastCopied && sameQuery(ran, lastCopied.sql) && copiedPlace()) {
    const line = el('p', lastCopied.id ? strings.practiceFromPoint(lastCopied.text) : strings.practiceFromPart(lastCopied.heading), 'note practice-from');
    const back = el('a', strings.practiceBack);
    back.setAttribute('href', '#step-4');
    back.addEventListener('click', (event) => {
      event.preventDefault();
      const place = copiedPlace();
      place?.scrollIntoView({ block: 'center' });
      place?.classList.add('flash');
      setTimeout(() => place?.classList.remove('flash'), 2000);
    });
    line.append(' ', back);
    extra.push(line);
  }
  $('query-result').querySelectorAll('.practice-extra').forEach((node) => node.remove());
  const holder = el('div', undefined, 'practice-extra');
  holder.append(...extra);
  $('query-table').after(holder);
  $('query-result').hidden = false;
}

function onMessage(event: MessageEvent) {
  // A message from a worker that has been ended, or one that arrives after the lock, is ignored.
  if (state === 'locked' || event.target !== worker) return;
  const message = event.data;
  if (message.type === 'ready') {
    state = 'ready';
  } else if (message.type === 'load-failed' || message.type === 'policy-failed') {
    state = 'load-failed';
    if (message.type === 'policy-failed') text('t-load-failed', strings.policyFailed);
  } else if (message.type === 'progress') {
    text('t-progress', strings.progress(message.done, message.total));
    $<HTMLProgressElement>('progress-bar').value = message.total ? message.done / message.total : 0;
  } else if (message.type === 'boundary-progress') {
    text('t-progress', strings.boundaryProgress);
    $<HTMLProgressElement>('progress-bar').removeAttribute('value');
  } else if (message.type === 'catalogue-error' || message.type === 'checks-error') {
    state = 'ready';
    clearResult();
    $(`t-${message.type}`).hidden = false;
  } else if (message.type === 'result') {
    resetSandbox();
    render(message as Result);
    addedSinceAnalysis = 0;
    state = 'review';
    const first = (message as Result).boundary.targets?.[0];
    // The invented example has no SQL window, and its rows are invented, so its database is a training database with
    // fictional patients unless the person has chosen otherwise.
    if (fromExample && !databaseChoice && !first?.settings?.database) databaseChoice = 'training';
    if (first && databaseChoice && first.settings?.database !== databaseChoice) sendDatabase(first, databaseChoice);
  } else if (message.type === 'analysis-failed') {
    state = 'ready';
    clearResult();
    $('t-analysis-failed').hidden = false;
  } else if (message.type === 'built') {
    sandbox = 'built';
    text('t-built', message.sentence);
    $('t-no-values-yet').hidden = message.hasValues;
    $('t-uses-checks').hidden = !message.hasValues;
  } else if (message.type === 'build-failed') {
    sandbox = 'none';
  } else if (message.type === 'ran' || message.type === 'run-failed') {
    showRun(message.type === 'ran' ? (message.result as RunResult) : { status: 'database-error', message: '' });
    $<HTMLButtonElement>('run').disabled = false;
  } else if (message.type === 'requests-progress') {
    text('t-requests-progress', strings.progress(message.done, message.total));
    $<HTMLProgressElement>('requests-progress-bar').value = message.total ? message.done / message.total : 0;
  } else if (message.type === 'requests-result') {
    runningRequests = false;
    text('t-requests-sentence', message.sentence);
    $('requests-table').replaceChildren(
      grid(
        null,
        (message.outcomes as [number, string][]).map(([number, outcome]) => [
          String(number),
          outcome,
          names.get(number) ?? '',
        ]),
      ),
    );
    $('requests-result').hidden = false;
  } else if (message.type === 'requests-failed') {
    runningRequests = false;
  } else if (message.type === 'pasted' || message.type === 'profile-pasted') {
    pasting = false;
    const profile = message.type === 'profile-pasted';
    const result = $(profile ? 't-profile-paste-result' : 't-paste-result');
    result.hidden = false;
    if (!message.ok) {
      result.className = 'status problem';
      result.textContent = profile ? strings.profilePasteUnreadable : strings.pasteUnreadable;
    } else {
      result.className = message.pasted.accepted ? 'status good' : 'status problem';
      result.textContent = strings.pasted(message.pasted.read, message.pasted.accepted);
      if (message.boundary) {
        $<HTMLTextAreaElement>(profile ? 'profile-paste' : 'paste').value = '';
        renderChecklist(message.boundary as Boundary, 'paste');
        zip = message.zip;
        checkScript = message.checkScript;
      }
    }
  } else if (message.type === 'first-query') {
    $('b-first-query').hidden = !message.sql;
    text('first-query', message.sql ?? '');
    $('t-first-result').hidden = !!message.sql;
    if (!message.sql) text('t-first-result', strings.firstNone);
    text('t-first-names', strings.firstNames(message.names, message.leftOut));
  } else if (message.type === 'first-read') {
    const result = $('t-first-result');
    result.hidden = false;
    // Where most of the tables, or every lookup table, did not come back, the page says so first: the SQL window may be
    // connected to the wrong database or schema, or with a login whose rights are narrow.
    const doubt = message.ok ? strings.firstDoubt[message.doubt ?? ''] ?? '' : '';
    $('t-first-doubt').hidden = !doubt;
    text('t-first-doubt', doubt);
    if (!message.ok) {
      result.className = 'status problem';
      result.textContent = strings.firstUnreadable;
    } else {
      firstCatalogue = new File([message.catalogue], 'catalogue.csv', { type: 'text/csv' });
      firstChecks = new File([message.checks], 'checks.csv', { type: 'text/csv' });
      result.className = 'status good';
      result.textContent = strings.firstReadDone(message.facts.tables, message.facts.columns, message.facts.sized)
        + (message.missing?.length ? ` ${strings.firstMissing(message.missing)}` : '') + ` ${strings.firstReadNext}`;
      $<HTMLTextAreaElement>('first-paste').value = '';
    }
  } else if (message.type === 'state-zip') {
    save([message.zip], 'application/zip', 'schemalyser-state.zip');
  } else if (message.type === 'codes-found') {
    showCandidates(message.group, message.columns ?? [], message.ok ? message.candidates : []);
  } else if (message.type === 'search-sql') {
    const code = document.querySelector<HTMLElement>(`pre[data-search="${CSS.escape(message.group)}"]`);
    if (code && message.ok) code.textContent = message.sql;
  } else if (message.type === 'listed') {
    if (message.ok) showListed(message.columns ?? [], message.rows ?? []);
    else (listedBox?.isConnected ? listedBox : document).querySelector('.listed-result')?.replaceChildren(el('p', strings.listedNone, 'status problem'));
  } else if (message.type === 'charted') {
    const shown = document.querySelector<HTMLElement>('.charted-result');
    const charted = chartedNow;
    if (!message.ok || !charted) shown?.replaceChildren(el('p', strings.chartedNone, 'status problem'));
    else if (shown) sendFacts(shown, [{ kind: 'charted', from: charted.from, to: charted.to, codes: charted.codes, counts: message.rows }]);
  } else if (message.type === 'year-counted') {
    if (message.ok) showYearCount(message.years);
    else (countBox?.isConnected ? countBox : document.querySelector('.count-result'))?.replaceChildren(el('p', strings.countNone, 'status problem'));
  } else if (message.type === 'fact-added') {
    // The confirmation, or the reason that nothing was recorded, stands beside the button that was pressed.
    const said = message.ok ? (withdrawing ? strings.withdrawn : pendingAnchor?.part === 'settings' ? strings.settingsRecorded : strings.factRecorded)
      : strings.factUnreadable;
    withdrawing = false;
    if (message.ok && message.boundary) {
      renderChecklist(message.boundary as Boundary, 'answer');
      zip = message.zip;
      checkScript = message.checkScript;
      confirmBeside(said, 'good');
    } else {
      endWorking();
      confirmBeside(said, message.ok ? 'good' : 'problem');
    }
  } else if (['fact-failed', 'facts-failed', 'settings-failed', 'fact-withdraw-failed'].includes(message.type)) {
    withdrawing = false;
    endWorking();
    confirmBeside(strings.factFailed, 'problem');
  } else if (message.type === 'paste-failed' || message.type === 'profile-paste-failed') {
    pasting = false;
    const id = message.type === 'paste-failed' ? 't-paste-result' : 't-profile-paste-result';
    $(id).hidden = false;
    $(id).className = 'status problem';
    text(id, strings.pasteFailed);
  }
  show();
  if (message.type === 'result' && !reanalysing) $('step-4').scrollIntoView({ block: 'start' });
  if (message.type === 'result') reanalysing = false;
}

async function startWorker() {
  try {
    const response = await fetch(new URL('worker.js', document.baseURI));
    if (!response.ok) throw new Error('worker');
    // Started from a blob so that the worker inherits this page's content security policy.
    const source = new Blob([await response.text()], { type: 'text/javascript' });
    worker = new Worker(URL.createObjectURL(source), { type: 'module' });
    workerHasFiles = false;
    worker.onmessage = onMessage;
    worker.onerror = () => {
      if (state === 'loading') state = 'load-failed';
      show();
    };
    worker.postMessage({ type: 'load', base: new URL('.', document.baseURI).href });
  } catch {
    state = 'load-failed';
    show();
  }
}

// Where the page was working when it locked, or where a button that needs the analysis engine is pressed once it has locked,
// the locked sentence stands in that place, so that nothing is left saying that Schemalyser is still at work.
function lockedHere(place: Element, after = false) {
  const note = el('p', after ? strings.lockedPressed : strings.lockedHere, 'status problem locked-note');
  note.setAttribute('role', 'status');
  place.parentElement?.querySelector(':scope > .locked-note')?.remove();
  if (after) place.after(note);
  else place.replaceWith(note);
  return note;
}

function lockWorking() {
  for (const note of document.querySelectorAll('.working-note')) lockedHere(note);
  if (workingBox) delete workingBox.dataset.working;
  workingBox = null;
  pendingAnchor = null;
  // A status that said that Schemalyser was reading or working says instead that it has stopped.
  const working = new Set([strings.pasteReading, strings.boundaryProgress, strings.practiceBuilding]);
  for (const status of document.querySelectorAll<HTMLElement>('#step-4 .status, #step-7 .status')) {
    if (!status.hidden && working.has(status.textContent ?? '')) {
      status.textContent = strings.lockedHere;
      status.className = 'status problem locked-note';
    }
  }
}

function lock() {
  // The network is back while the page holds data: end the worker, which holds the requests
  // and the sandbox, and let go of the file names.
  lockWorking();
  worker?.terminate();
  worker = null;
  workerHasFiles = false;
  reanalysing = false;
  if (!zip) clearChecklist();
  clearPaste();
  $('index').replaceChildren();
  names = new Map();
  resetInputs();
  resetSandbox();
  text('t-reconnected', zip ? strings.reconnected : strings.reconnectedNoInventory);
  state = 'locked';
}

function save(parts: BlobPart[], type: string, name: string) {
  const link = el('a');
  link.href = URL.createObjectURL(new Blob(parts, { type }));
  link.download = name;
  link.click();
  URL.revokeObjectURL(link.href);
}

window.addEventListener('online', () => {
  if (state === 'analysing' || state === 'review' || (state === 'ready' && workerHasFiles)) lock();
  else if (state === 'ready') resetInputs();
  show();
  if (state === 'locked') $('step-2').scrollIntoView({ block: 'start' });
});
window.addEventListener('offline', show);
// Once the page has locked, a button in the checklist that needs the analysis engine says so beside itself. Copying and
// saving what the page already shows still work.
$('checklists').addEventListener('click', (event) => {
  if (state !== 'locked') return;
  const button = (event.target as Element).closest('button');
  if (!button || button.classList.contains('copy-query') || /^(Copy|Save the (specification|reference))/.test(button.textContent ?? '')) return;
  event.stopImmediatePropagation();
  event.preventDefault();
  lockedHere(button, true);
}, true);
// Opening the part for the later OMOP release shows its paste box.
document.addEventListener('toggle', show, true);

for (const input of inputs) input.addEventListener('change', () => {
  // A file of one's own replaces the invented example, and nothing worked out from the example is kept.
  if (input.files?.length) discardExample();
  show();
});

function discardExample() {
  if (example === null && !fromExample) return;
  example = null;
  $('t-example-status').hidden = true;
  if (fromExample) {
    worker?.postMessage({ type: 'clear' });
    clearResult();
    snapshot = null;
    fromExample = false;
    if (state === 'review') state = 'ready';
  }
}

// The invented example is served with the page. Its list of files is fetched first, then each file, while the page is online.
$('example-load').addEventListener('click', async () => {
  if (exampleLoading || state !== 'ready') return;
  exampleLoading = true;
  const status = $('t-example-status');
  status.hidden = false;
  status.className = 'status';
  text('t-example-status', strings.exampleLoading);
  show();
  try {
    const base = new URL('./example/', location.href);
    // Each fetch that fails is tried once more by itself, after a short pause, before the loading gives up.
    const get = async (path: string) => {
      for (let attempt = 0; ; attempt++) {
        try {
          const response = await fetch(new URL(path, base), { cache: attempt ? 'reload' : 'default' });
          if (response.ok) return response;
        } catch {
          // A failed fetch is tried again below.
        }
        if (attempt) throw new Error(path);
        await new Promise((resolve) => setTimeout(resolve, 1000));
      }
    };
    const manifest = (await (await get('manifest.json')).json()) as { state: string[]; requests: string[] };
    const paths = [...manifest.state, ...manifest.requests];
    let done = 0;
    text('t-example-status', strings.exampleProgress(0, paths.length));
    const take = async (path: string) => {
      const file = new File([await (await get(path)).blob()], path.split('/').pop() ?? path);
      text('t-example-status', strings.exampleProgress(++done, paths.length));
      return file;
    };
    // The files are fetched six at a time, which is much quicker than one after another.
    const files = new Map<string, File>();
    const queue = [...paths];
    await Promise.all(Array.from({ length: 6 }, async () => {
      for (let path = queue.shift(); path !== undefined; path = queue.shift()) files.set(path, await take(path));
    }));
    const loaded = { requests: [] as { path: string; file: File }[], state: new Map<string, File>() };
    for (const path of manifest.state) loaded.state.set(path.replace(/^state\//, ''), files.get(path)!);
    for (const path of manifest.requests) loaded.requests.push({ path: path.replace(/^requests\//, ''), file: files.get(path)! });
    example = loaded;
    status.className = 'status good';
    text('t-example-status', strings.exampleLoaded(loaded.state.size, loaded.requests.length));
  } catch {
    status.className = 'status problem';
    text('t-example-status', navigator.onLine ? strings.exampleFailed : strings.exampleFailedOffline);
  }
  exampleLoading = false;
  show();
});

function startAnalysis() {
  const chosen = chosenFiles();
  if (!worker || navigator.onLine || !canAnalyse(chosen) || fetchWindow !== null) return;
  const { requests } = chosen;
  fromExample = example !== null && !sqlFiles().length && !fetched && !stateFolder.files?.length && !catalogue.files?.length;
  const { lines, commits } = provenance(chosen);
  $('github-provenance').replaceChildren(...lines.map((line) => el('p', line)));
  $('github-provenance').hidden = lines.length === 0;
  hideErrors();
  text('t-progress', strings.progress(0, requests.length));
  $<HTMLProgressElement>('progress-bar').value = 0;
  state = 'analysing';
  show();
  workerHasFiles = true;
  worker.postMessage({
    type: 'analyse',
    catalogue: chosen.catalogue,
    rules: chosen.rules,
    checks: chosen.checks,
    state: [...chosen.state].map(([path, file]) => ({ path, file })).sort(byPath),
    requests,
    commits,
  });
}

analyse.addEventListener('click', () => {
  reanalysing = false;
  startAnalysis();
});

reanalyse.addEventListener('click', () => {
  if (state !== 'review') return;
  reanalysing = true;
  startAnalysis();
});

// Files added after an analysis join the requests already held, and the inputs are emptied so that
// the same file can be added again once it has changed.
function addRequests(input: HTMLInputElement, keepFolder: boolean) {
  const files = Array.from(input.files ?? []).filter((file) => /\.sql$/i.test(file.name));
  for (const file of files) {
    const path = (keepFolder && file.webkitRelativePath) || file.name;
    added = [...added.filter((entry) => entry.path !== path), { path, file }];
  }
  addedSinceAnalysis += files.length;
  input.value = '';
  show();
}
addFiles.addEventListener('change', () => addRequests(addFiles, false));
addFolder.addEventListener('change', () => addRequests(addFolder, true));

// After the lock, a new analysis can begin with a fresh worker. The checklist is kept, so that the
// next analysis can show what the new files answer; nothing from the requests is kept.
$('restart').addEventListener('click', () => {
  if (state !== 'locked') return;
  zip = null;
  checkScript = '';
  clearChecklist();
  $('pack').replaceChildren();
  $('unread').replaceChildren();
  $('github-provenance').replaceChildren();
  $('github-provenance').hidden = true;
  $('t-reconnected').hidden = true;
  state = 'loading';
  show();
  void startWorker();
});

$('download').addEventListener('click', () => {
  if (zip) save([zip], 'application/zip', 'schemalyser-inventory.zip');
});

$('download-check-script').addEventListener('click', () => {
  if (checkScript) save([checkScript], 'text/plain', 'schemalyser-checks.sql');
});

// The pasted results go to the worker as they are, and the core reads them by the rules of a check results
// file or of a core profile.
function sendPaste(box: string, result: string, type: 'paste' | 'profile-paste') {
  const pastedText = $<HTMLTextAreaElement>(box).value;
  if (!worker || state !== 'review' || pasting || !pastedText.trim()) return;
  pasting = true;
  clearStale();
  $(result).hidden = false;
  $(result).className = 'status';
  text(result, strings.pasteReading);
  show();
  worker.postMessage({ type, text: pastedText });
}
$('read-paste').addEventListener('click', () => sendPaste('paste', 't-paste-result', 'paste'));
$('read-profile-paste').addEventListener('click', () => sendPaste('profile-paste', 't-profile-paste-result', 'profile-paste'));
// The first query is written in the worker, which reads the chosen files; the page shows it and never changes it.
$('first-write').addEventListener('click', () => {
  const chosen = chosenFiles();
  const steps = stepFiles(chosen);
  if (!worker || navigator.onLine || !(chosen.requests.length || steps.length)) return;
  workerHasFiles = true;
  worker.postMessage({ type: 'first-ask', requests: chosen.requests, steps });
});
$('first-copy').addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText($('first-query').textContent ?? '');
    $('first-copy').dataset.copied = 'true';
    setTimeout(() => delete $('first-copy').dataset.copied, 2000);
  } catch {
    // Without clipboard access the query can still be selected and copied by hand.
  }
});
$('first-read').addEventListener('click', () => {
  const pastedText = $<HTMLTextAreaElement>('first-paste').value;
  const chosen = chosenFiles();
  if (!worker || navigator.onLine || !pastedText.trim()) return;
  const checksFile = chosen.state.get('checks.csv');
  worker.postMessage({ type: 'first-read', text: pastedText, rules: chosen.rules, checks: checksFile === firstChecks ? null : checksFile });
});
$('save-state').addEventListener('click', () => worker?.postMessage({ type: 'state-zip' }));
$('note-copy').addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(strings.firstAskNote);
    $('note-copy').dataset.copied = 'true';
    setTimeout(() => delete $('note-copy').dataset.copied, 2000);
  } catch {
    // Without clipboard access the note can still be selected and copied by hand.
  }
});

$('save-profile').addEventListener('click', () => {
  if (profileText) save([profileText], 'text/csv', 'core-profile.csv');
});

$('save-checks').addEventListener('click', () => {
  if (checksText) save([checksText], 'text/csv', 'checks.csv');
});

$('clear').addEventListener('click', () => {
  if (state === 'locked') {
    location.reload();
    return;
  }
  worker?.postMessage({ type: 'clear' });
  clearResult();
  resetInputs();
  snapshot = null;
  databaseChoice = null;
  firstDatabase();
  state = 'ready';
  show();
});

$('copy').addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(catalogueQuery);
    $('copy').dataset.copied = 'true';
    setTimeout(() => delete $('copy').dataset.copied, 2000);
  } catch {
    // Without clipboard access the query can still be selected and copied by hand.
  }
});

$('build').addEventListener('click', () => {
  if (!worker || state !== 'review') return;
  sandbox = 'building';
  show();
  const rows = Math.min(5000, Math.max(10, Number($<HTMLInputElement>('rows').value) || 500));
  worker.postMessage({ type: 'build', rows });
});

$('run').addEventListener('click', () => {
  const sql = $<HTMLTextAreaElement>('sql').value;
  if (!worker || sandbox !== 'built' || !sql.trim()) return;
  $<HTMLButtonElement>('run').disabled = true;
  worker.postMessage({ type: 'run', sql });
});

$('run-requests').addEventListener('click', () => {
  if (!worker || sandbox !== 'built' || navigator.onLine) return;
  const { requests } = chosenFiles();
  runningRequests = true;
  $('requests-result').hidden = true;
  text('t-requests-progress', strings.progress(0, requests.length));
  $<HTMLProgressElement>('requests-progress-bar').value = 0;
  show();
  workerHasFiles = true;
  worker.postMessage({ type: 'requests', requests });
});

function githubProblem(reply: Extract<FetchReply, { type: 'github-failed' }>) {
  const g = githubStrings;
  const { repository } = reply;
  return {
    unauthorised: g.unauthorised(repository),
    'not-found': g.notFound(repository, reply.ref || g.defaultBranch),
    'rate-limited': g.rateLimited,
    offline: g.offline,
    other: g.otherError(repository),
    'policy-before': g.policyBefore,
    'policy-after': g.policyAfter,
  }[reply.problem];
}

function fetchEnded(error: string | null) {
  fetchWindow = null;
  if (pendingRequest) pendingRequest.token = '';
  pendingRequest = null;
  $('t-github-error').hidden = error === null;
  text('t-github-error', error ?? '');
  show();
}

function receiveFiles(reply: Extract<FetchReply, { type: 'github-fetched' }>) {
  const named = (file: (typeof reply.files)[number]) => new File([file.bytes], file.path.split('/').pop() ?? file.path);
  fetched = {
    reports: reply.reports,
    requests: reply.files
      .filter((file) => file.from === 'requests')
      .map((file) => ({ path: file.path, file: named(file) }))
      .sort(byPath),
    state: new Map(
      reply.files.filter((file) => file.from === 'state' && isStateFile(file.path)).map((file) => [file.path, named(file)]),
    ),
  };
  const g = githubStrings;
  $('github-fetched').replaceChildren(
    ...reply.reports.flatMap((report) => [
      el('p', g.fetched(report.count, report.repository, report.commit), 'status good'),
      ...(report.skipped ? [el('p', g.skipped(report.skipped), 'note')] : []),
      ...(report.tooMany ? [el('p', g.tooMany, 'note')] : []),
      ...(report.truncated ? [el('p', g.truncated, 'note')] : []),
    ]),
  );
}

// Messages from the fetch window. Only the window this page opened, on this site, is heard.
window.addEventListener('message', (event: MessageEvent) => {
  if (!fetchWindow || event.source !== fetchWindow || event.origin !== location.origin) return;
  const reply = event.data as FetchReply;
  if (reply.type === 'github-ready' && pendingRequest) {
    // The token passes to the fetch window once, and this page keeps no copy of it.
    fetchWindow.postMessage(pendingRequest, location.origin);
    pendingRequest.token = '';
    pendingRequest = null;
  } else if (reply.type === 'github-fetched') {
    receiveFiles(reply);
    fetchEnded(null);
  } else if (reply.type === 'github-failed') {
    fetchEnded(githubProblem(reply));
  }
});

$('github-fetch').addEventListener('click', () => {
  if (!github || fetchWindow !== null || !navigator.onLine) return;
  const wanted = (repository: HTMLInputElement, ref: HTMLInputElement) => ({
    repository: repository.value.trim(),
    ref: ref.value.trim(),
  });
  const request: FetchRequest = {
    type: 'github-fetch',
    requests: wanted(requestsRepository, requestsRef),
    state: stateRepository.value.trim() ? wanted(stateRepository, stateRef) : null,
    token: tokenInput.value,
  };
  // The token leaves the page's form at once, whatever happens to the fetch.
  tokenInput.value = '';
  discardFetched();
  $('t-github-error').hidden = true;
  const opened = window.open(new URL('github.html', document.baseURI), 'schemalyser-github', 'popup,width=560,height=260');
  if (!opened) {
    request.token = '';
    fetchEnded(githubStrings.popupBlocked);
    return;
  }
  fetchWindow = opened;
  pendingRequest = request;
  text('t-github-progress', githubStrings.progress);
  show();
  // If the window is closed before it replies, the fetch has ended without files. The window closes
  // itself just after its last message, so a closed window is given one more turn to be heard.
  let closedSeen = false;
  const watch = window.setInterval(() => {
    if (fetchWindow !== opened) window.clearInterval(watch);
    else if (opened.closed && closedSeen) {
      window.clearInterval(watch);
      fetchEnded(githubStrings.windowClosed);
    } else closedSeen = opened.closed;
  }, 500);
});

for (const field of githubFields) field.addEventListener('input', show);

async function showVersion() {
  let checksum = '—';
  try {
    const response = await fetch(new URL('checksum.txt', document.baseURI));
    if (response.ok) checksum = (await response.text()).trim().slice(0, 16);
  } catch {
    // The development server has no checksum file.
  }
  text('t-version', strings.version(__VERSION__, checksum));
}

const fixed: Record<string, string> = {
  title: strings.title,
  intro: strings.intro,
  't-catalogue-why': strings.catalogueWhy,
  's-overview': strings.overviewSummary,
  't-overview-lead': strings.overviewLead,
  't-overview-private': strings.overviewPrivate,
  't-overview-example': strings.overviewExample,
  's-glossary': strings.glossarySummary,
  'glossary-top': strings.glossaryTop,
  'h-state': strings.stateHeading,
  'h-sql': strings.sqlHeading,
  'h-analyse': strings.analyseHeading,
  't-analyse-what': strings.analyseWhat,
  't-first-shape': strings.firstShape,
  't-first-use': strings.firstUse,
  query: catalogueQuery,
  copy: strings.copyQuery,
  't-query-safe': strings.querySafe,
  't-skip-catalogue': strings.skipCatalogue,
  't-loading': strings.loading,
  't-load-failed': strings.loadFailed,
  't-loaded': strings.loaded,
  't-no-files': strings.noFilesWhileConnected,
  't-policy-held': strings.policyHeld,
  's-offline-how': strings.offlineHowSummary,
  's-policy': strings.policySummary,
  's-github': strings.githubSummary,
  's-catalogue': strings.catalogueSummary,
  's-other-files': strings.otherFilesSummary,
  'h-example': strings.exampleHeading,
  't-example-what': strings.exampleWhat,
  'example-load': strings.exampleLoad,
  't-example-chosen': strings.exampleChosen,
  't-example-banner': strings.exampleBanner,
  't-offline': strings.offline,
  't-kept': strings.keptForComparison,
  'l-state': strings.chooseState,
  't-state-note': strings.stateNote,
  't-checklist-intro': strings.checklistIntro,
  'h-add': strings.addHeading,
  't-add-what': strings.addWhat,
  'l-add-files': strings.addFiles,
  'l-add-folder': strings.addFolder,
  reanalyse: strings.reanalyse,
  't-restart-what': strings.restartWhat,
  restart: strings.restart,
  't-download-holds': strings.downloadHolds,
  's-summary': strings.showSummary,
  'l-catalogue': strings.chooseCatalogue,
  't-catalogue-note': strings.catalogueNote,
  'l-rules': strings.chooseRules,
  't-rules-note': strings.rulesNote,
  'l-folder': strings.chooseFolder,
  't-folder-note': strings.folderNote,
  'l-checks': strings.chooseChecks,
  't-checks-note': strings.checksNote,
  analyse: strings.analyse,
  't-catalogue-error': strings.catalogueError,
  't-checks-error': strings.checksError,
  't-analysis-failed': strings.analysisFailed,
  't-no-headers': strings.noHeaders,
  't-checks-no-headers': strings.checksNoHeaders,
  'h-unread': strings.unreadHeading,
  't-read-before': strings.readBeforeDownload,
  't-names-only': strings.namesOnly,
  't-do-not-download': strings.doNotDownload,
  't-index-note': strings.indexNote,
  download: strings.download,
  clear: strings.clear,
  't-check-script-what': strings.checkScriptWhat,
  't-check-script-safe': strings.checkScriptSafe,
  't-check-script-how': strings.checkScriptHow,
  'h-paste': strings.pasteHeading,
  't-paste-what': strings.pasteWhat,
  'l-paste': strings.pasteLabel,
  'read-paste': strings.readPaste,
  'save-checks': strings.saveChecks,
  't-save-checks': strings.saveChecksNote,
  'h-first': strings.firstHeading,
  't-first-what': strings.firstWhat,
  'first-write': strings.firstWrite,
  'l-database': strings.databaseLegend,
  't-database-what': strings.databaseWhat,
  'first-copy': strings.firstCopy,
  'l-first-paste': strings.firstPasteLabel,
  'first-read': strings.firstRead,
  'save-state': strings.saveState,
  's-first-note': strings.noteHeading,
  'first-note': strings.firstAskNote,
  'note-copy': strings.noteCopy,
  't-save-state': strings.saveStateNote,
  'h-profile-paste': strings.profilePasteHeading,
  't-profile-paste-what': strings.profilePasteWhat,
  'l-profile-paste': strings.profilePasteLabel,
  'read-profile-paste': strings.readProfilePaste,
  'save-profile': strings.saveProfile,
  't-save-profile': strings.saveProfileNote,
  'download-check-script': strings.downloadCheckScript,
  't-build-what': strings.practiceBuildWhat,
  'l-rows': strings.practiceRows,
  build: strings.practiceBuild,
  't-building': strings.practiceBuilding,
  't-invented': strings.practiceInvented,
  't-no-values-yet': sandboxStrings.noValuesYet,
  't-uses-checks': strings.usesChecks,
  'l-sql': strings.practiceQuery,
  run: strings.practiceRun,
  's-translated': sandboxStrings.showTranslated,
  't-runs-chosen': strings.runsChosenRequests,
  'run-requests': strings.practiceRunFiles,
  'h-safeguards': strings.safeguardsHeading,
  't-check-yourself': strings.checkYourself,
  'a-sandbox': strings.openSandbox,
  't-keeps-nothing': strings.keepsNothing,
};
for (const [id, value] of Object.entries(fixed)) text(id, value);
// The database is chosen where the first query is offered, before anything is run, and kept until the page is cleared.
function firstDatabase() {
  $('database-options').replaceChildren(databaseOptions('database-first', databaseChoice, (value) => (databaseChoice = value)));
}
firstDatabase();
// The proposed GitHub wording is put on the page only when the GitHub path is asked for.
if (github) {
  const g = githubStrings;
  const proposed: Record<string, string> = {
    'h-github': g.heading,
    't-github-intro': g.intro,
    'l-github-requests': g.requestsRepository,
    'l-github-requests-ref': g.requestsRef,
    't-github-requests-ref-note': g.refNote,
    'l-github-state': g.stateRepository,
    'l-github-state-ref': g.stateRef,
    't-github-state-ref-note': g.refNote,
    'l-github-token': g.token,
    't-github-token-note': g.tokenNote,
    'github-fetch': g.fetch,
    't-github-closed': g.closed,
  };
  for (const [id, value] of Object.entries(proposed)) text(id, value);
}
strings.steps.forEach((heading, i) => text(`h-step-${i + 1}`, heading));
$('overview').replaceChildren(...strings.overview.map(([heading, what]) => {
  const stage = el('li');
  stage.append(el('strong', heading), document.createTextNode(`. ${what}`));
  return stage;
}));
// Each step in the same shape: what it is for and who does it, what to do, what you see when it has worked, what
// Schemalyser does with what you gave it, and what comes next.
function guide(box: HTMLElement, shape: Guide) {
  const parts: HTMLElement[] = [];
  if (shape.purpose) parts.push(el('p', shape.purpose, 'guide-purpose'));
  if (shape.steps?.length) {
    const list = el('ol', undefined, 'guide-steps');
    list.append(...shape.steps.map((step) => el('li', step)));
    parts.push(list);
  }
  for (const [line, className] of [[shape.worked, 'guide-worked'], [shape.does, 'guide-does'], [shape.next, 'guide-next']] as const) {
    if (line) parts.push(el('p', line, className));
  }
  box.replaceChildren(...parts);
}
strings.guides.forEach((shape, i) => guide($(`g-step-${i + 1}`), shape));
$('t-first-how').replaceChildren(...strings.firstHow.map((step) => el('li', step)));
// The link at the top of the page opens the glossary at the bottom and goes to it.
$('glossary-top').addEventListener('click', (event) => {
  event.preventDefault();
  $<HTMLDetailsElement>('d-glossary').open = true;
  $('d-glossary').scrollIntoView({ block: 'start' });
});
$('glossary').replaceChildren(...strings.glossary.flatMap(([term, meaning]) => [el('dt', term), el('dd', meaning)]));
$('safeguards').replaceChildren(...strings.safeguards.map((sentence) => el('li', sentence)));
$('t-offline-how').replaceChildren(...strings.offlineHow.map((sentence) => el('li', sentence)));
// The fetch from GitHub is folded away, as a meeting does not need it, and opened at once by a link that asks for it.
if (new URLSearchParams(location.search).get('source') === 'github') ($('b-github') as HTMLDetailsElement).open = true;

// The query box is emptied when the page is left, so that the browser does not keep its contents.
window.addEventListener('pagehide', () => {
  $<HTMLTextAreaElement>('sql').value = '';
  $<HTMLTextAreaElement>('paste').value = '';
  $<HTMLTextAreaElement>('profile-paste').value = '';
  $<HTMLTextAreaElement>('first-paste').value = '';
  $('first-query').textContent = '';
});

show();
void showVersion();
void startWorker();
