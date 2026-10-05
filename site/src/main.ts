import { sandboxStrings } from './sandbox-strings';
import { catalogueQuery, githubStrings, packFiles, strings } from './strings';
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
  ask?: { kind: 'join' | 'filter' | 'codes'; text: string; left?: string; right?: string; tables?: Record<string, string[]>;
    column?: string; vocabulary?: string; concept?: string } | null;
  fact?: string;
}

// One plain query that a checklist offers, written by the core. The page shows it and never changes it.
interface Query {
  id: string;
  sql: string;
  state: string;
}

interface Target {
  name: string;
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
  draft?: { sql: string; countsOnly: boolean; restructured?: boolean; tables: { name: string; rows: number | null }[] } | null;
  stages?: { source: Target['counts']; release: Target['counts'] };
  stageVerdicts?: string[];
  questions?: string;
  specification?: string;
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
type Snapshot = Map<string, { answered: number; statuses: Map<string, string> }>;

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
const el = <K extends keyof HTMLElementTagNameMap>(tag: K, content?: string, className?: string) => {
  const node = document.createElement(tag);
  if (content !== undefined) node.textContent = content;
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
  $('save-checks').hidden = !checksText;
  $('t-save-checks').hidden = !checksText;
  const anyProfile = [...document.querySelectorAll('#checklists .profile-queries')].length > 0;
  $('b-profile-paste').hidden = !(state === 'review' || state === 'analysing') || !(anyProfile || profileText);
  $<HTMLButtonElement>('read-profile-paste').disabled = state !== 'review' || pasting;
  $('save-profile').hidden = !profileText;
  $('t-save-profile').hidden = !profileText;

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
  // Without a catalogue, the first query is offered, once the requests are chosen and the computer is offline.
  $('b-first').hidden = !(state === 'ready' && !online && chosen.requests.length && (!chosen.catalogue || firstCatalogue));
  $<HTMLButtonElement>('first-write').disabled = !worker || online;
  $('b-save-state').hidden = !(state === 'review' && (checksText || profileText));
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
const SAVED_STATE_FILES = ['facts.json', 'sql_evidence.json'];

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
  const merged = new Map<string, File>(fetched?.state ?? []);
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
  for (const { path, file } of fromFolder.length ? fromFolder : (fetched?.requests ?? [])) merged.set(path, file);
  for (const { path, file } of added) merged.set(path, file);
  return [...merged].map(([path, file]) => ({ path, file })).sort(byPath);
}

// The files to analyse. A file chosen from this computer takes the place of the fetched one.
// Whether an analysis can begin: there is a catalogue, and either requests or a state that holds target queries,
// whose saved evidence and answers stand without the request files.
function canAnalyse(chosen: ReturnType<typeof chosenFiles>) {
  return Boolean(chosen.catalogue && (chosen.requests.length || [...chosen.state.keys()].some((path) => path.startsWith('targets/'))));
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
  added = [];
  addedSinceAnalysis = 0;
  hideErrors();
  discardFetched();
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

// The question that a colleague can answer from knowledge, with the controls that record the answer as a fact.
function askElement(item: Item) {
  const ask = item.ask!;
  const box = el('div', undefined, 'ask');
  box.dataset.ask = ask.kind;
  box.append(el('p', strings.questionFirst, 'ask-label'), el('p', ask.text, 'ask-text'));
  const send = (fact: Record<string, unknown>) => {
    const section = box.closest('section.target');
    const who = (section?.querySelector('input.fact-who') as HTMLInputElement | null)?.value.trim() ?? '';
    const date = new Date().toISOString().slice(0, 10);
    worker?.postMessage({ type: 'fact', fact: JSON.stringify({ ...fact, date, ...(who ? { who } : {}) }) });
  };
  const button = (label: string, className: string, onClick: () => void) => {
    const b = el('button', label, className);
    b.type = 'button';
    b.addEventListener('click', onClick);
    return b;
  };
  const actions = el('div', undefined, 'actions');
  if (ask.kind === 'join') {
    actions.append(button(strings.factYes, 'fact-yes', () => send({ kind: 'join', left: ask.left, right: ask.right, answer: 'yes' })));
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
    box.append(actions, el('p', strings.factInstead, 'note'), left, right);
    box.append(button(strings.factSaveNo, 'secondary fact-no', () =>
      send({ kind: 'join', left: ask.left, right: ask.right, answer: 'no', ...(left.value && right.value ? { instead: [left.value, right.value] } : {}) })));
    return box;
  }
  if (ask.kind === 'filter') {
    actions.append(button(strings.factYes, 'fact-yes', () => send({ kind: 'filter', column: ask.column, answer: 'yes' })),
      button(strings.factNo, 'secondary fact-no', () => send({ kind: 'filter', column: ask.column, answer: 'no' })));
    box.append(actions);
    return box;
  }
  const label = el('label', strings.codesLabel);
  const input = el('input', undefined, 'fact-codes') as HTMLInputElement;
  input.type = 'text';
  label.append(input);
  box.append(label, button(strings.codesSave, 'fact-save-codes', () => {
    const codes = input.value.split(',').map((code) => code.trim()).filter(Boolean);
    if (codes.length) send({ kind: 'codes', vocabulary: ask.vocabulary, concept: ask.concept, column: ask.column, codes });
  }));
  return box;
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
  const [code, copy] = queryBlock(draft.sql, 'audit-query');
  code.dataset.query = 'audit';
  copy.dataset.query = 'audit';
  copy.textContent = strings.auditCopy;
  const keep = el('button', strings.auditSave, 'secondary');
  keep.type = 'button';
  keep.id = `save-audit-${target.name}`;
  keep.addEventListener('click', () => save([draft.sql], 'text/plain', `${target.name}_source.sql`));
  const actions = el('div', undefined, 'actions');
  actions.append(copy, keep);
  audit.append(code, actions);
  return audit;
}

// A plain query, exactly as the core wrote it, with a button that copies it.
function queryBlock(sql: string, className: string) {
  const code = el('pre', sql, `code ${className}`);
  const copy = el('button', strings.copyQuery, 'secondary copy-query');
  copy.type = 'button';
  copy.addEventListener('click', async () => {
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
  if (item.status === 'answered' || !item.queryState) return [];
  const parts: HTMLElement[] = [];
  // Where a colleague can answer from knowledge, the query is the alternative, so it says so first.
  if (item.ask) parts.push(el('p', strings.queryAlternative, 'note'));
  const state = el('p', strings.queryStates[item.queryState] ?? '', 'query-state');
  state.dataset.state = item.queryState;
  parts.push(state);
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
  }
  return parts;
}

// The checklist of each target query: first what has just been answered, then the open items that
// existing SQL could settle, then those that need something else, and the answered items folded away.
function itemElement(item: Item, previous?: string, queries?: Map<string, Query>, shown?: Set<string>) {
  const line = el('li', undefined, 'item');
  line.dataset.id = item.id;
  line.dataset.status = item.status;
  if (previous !== undefined && previous !== 'answered' && item.status === 'answered') line.dataset.new = 'true';
  const mark = el('span', undefined, 'mark');
  mark.setAttribute('aria-hidden', 'true');
  const body = el('div', undefined, 'item-body');
  body.append(el('span', strings.statusNames[item.status], 'visually-hidden'));
  // What the step is trying to do is read first, before the named columns.
  if (item.intent) body.append(el('p', item.intent, 'intent'));
  body.append(el('p', item.question, 'question'));
  if (item.status !== 'answered') {
    // How the data team's queries get between the same tables instead.
    if (item.route && item.status === 'open') body.append(el('p', item.route, 'route'));
    body.append(el('p', item.needed, 'needed'));
    if (item.group === 'other' && item.actor) body.append(el('p', item.actor, 'actor'));
    if (item.ask && item.stage === 'source') body.append(askElement(item));
    if (queries && shown) body.append(...queryElements(item, queries, shown));
  }
  body.append(el('p', `${item.inHand} ${item.blocking ? strings.blocking : strings.notBlocking}`.trim(), 'in-hand'));
  line.append(mark, body);
  return line;
}

function itemList(group: string, entries: HTMLElement[]) {
  const list = el('ul', undefined, 'items');
  list.dataset.group = group;
  list.append(...entries);
  return list;
}

function targetSection(target: Target, before?: { answered: number; statuses: Map<string, string> }) {
  const section = el('section', undefined, 'target');
  section.dataset.target = target.name;
  const heading = el('h3');
  heading.append(el('span', target.name, 'target-name'));
  section.append(heading);
  // The first stage leads: what the answer from the source database rests on.
  const first = target.stages?.source ?? target.counts;
  const sourceReady = first.open === 0 && target.steps;
  section.append(el('p', (sinceAnalysis ? strings.tallySinceAnalysis : strings.tally)(first.answered, first.total, before?.answered), 'tally'));
  section.append(el('p', target.stageVerdicts?.[0] ?? target.verdict, sourceReady ? 'status good stage-verdict' : 'status stage-verdict'));
  // The questions that a colleague can answer from knowledge come first, as one list to send.
  if (target.questions) {
    const questions = textBlock('questions', strings.questionsHeading, strings.questionsWhat, target.questions, strings.questionsCopy);
    const who = el('label', strings.whoLabel);
    const input = el('input', undefined, 'fact-who') as HTMLInputElement;
    input.type = 'text';
    input.maxLength = 80;
    who.append(input);
    questions.append(who);
    section.append(questions);
  }
  const readiness = el('details', undefined, 'readiness');
  readiness.append(el('summary', strings.readinessInFull), el('pre', target.readiness, 'code'));
  section.append(readiness);

  // The table sizes query comes first, because every other query waits for the sizes it gives.
  if (target.sizes) {
    const sizes = el('div', undefined, 'sizes');
    sizes.append(el('h4', strings.sizesHeading), el('p', target.sizes.reason, 'sizes-reason'));
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
  const fresh = target.rows.filter((item) => item.status === 'answered' && before && previous(item) !== undefined && previous(item) !== 'answered');
  if (fresh.length) {
    section.append(el('p', strings.newlyAnswered(fresh.length), 'status good'), el('h4', strings.groupNew));
    section.append(itemList('new', fresh.map((item) => itemElement(item, previous(item)))));
  }

  // Within a group, the open items come before those partly answered, and blocking items first, so
  // that what stops the simulation is read first. The order is otherwise the checklist's own.
  const rank = (item: Item) => (item.status === 'open' ? 0 : 2) + (item.blocking ? 0 : 1);
  const ordered = (list: Item[]) => list.map((item, i) => ({ item, i })).sort((a, b) => rank(a.item) - rank(b.item) || a.i - b.i).map(({ item }) => item);
  const firstStage = (item: Item) => (item.stage ?? 'source') === 'source';
  const later = target.rows.filter((item) => item.stage === 'release' && !fresh.includes(item));
  const sql = ordered(target.rows.filter((item) => item.group === 'sql' && firstStage(item)));
  section.append(el('h4', strings.groupSql));
  if (sql.length) {
    section.append(el('p', strings.groupSqlNote, 'note'), itemList('sql', sql.map(offered)));
  } else section.append(el('p', strings.groupSqlNone, 'note'));

  const other = ordered(target.rows.filter((item) => item.group === 'other' && firstStage(item)));
  if (other.length) {
    section.append(el('h4', strings.groupOther), el('p', strings.groupOtherNote, 'note'));
    section.append(itemList('other', other.map(offered)));
  }

  const answered = target.rows.filter((item) => item.group === 'answered' && !fresh.includes(item) && firstStage(item));
  if (answered.length) {
    const folded = el('details', undefined, 'answered');
    folded.append(el('summary', strings.groupAnswered(answered.length)), itemList('answered', answered.map((item) => itemElement(item))));
    section.append(folded);
  }
  // The items that the answer to this question does not depend on, which do not count against the first phase.
  const unneeded = target.rows.filter((item) => item.stage === 'unneeded' && !fresh.includes(item));
  if (unneeded.length) {
    const folded = el('details', undefined, 'unneeded');
    folded.append(el('summary', strings.groupUnneeded(unneeded.length)), el('p', strings.unneededWhat, 'note'),
      itemList('unneeded', unneeded.map((item) => itemElement(item))));
    section.append(folded);
  }
  // The first phase ends with the specification, the check of a hand-written query, and the generated query.
  if (target.specification) {
    section.append(textBlock('specification', strings.specHeading, strings.specWhat, target.specification, strings.specCopy,
      strings.specSave, `${target.name}_specification.txt`));
    const check = el('div', undefined, 'sizes check');
    check.append(el('h4', strings.checkHeading), el('p', strings.checkWhat, 'sizes-reason'));
    section.append(check);
  }
  if (target.draft) section.append(auditSection(target));

  // Everything that only the later OMOP release needs, folded away under one heading.
  if (later.length || target.profile?.length) {
    const release = el('details', undefined, 'release');
    release.append(el('summary', strings.releaseHeading), el('p', strings.releaseWhat, 'note'));
    if (target.stageVerdicts?.[1]) release.append(el('p', target.stageVerdicts[1], 'status'));
    if (profileSection) release.append(profileSection);
    const open = ordered(later.filter((item) => item.status !== 'answered'));
    if (open.length) release.append(itemList('release', open.map(offered)));
    const done = later.filter((item) => item.status === 'answered');
    if (done.length) release.append(itemList('release-answered', done.map((item) => itemElement(item))));
    section.append(release);
  }
  return section;
}

// The checklist as the last analysis of the requests left it, before any result was pasted or any answer given,
// which the tally after a paste or an answer is compared with.
let analysed: Snapshot | null = null;

function renderChecklist(boundary: Boundary, afterPaste = false) {
  const previous = afterPaste && analysed ? analysed : snapshot;
  sinceAnalysis = afterPaste;
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

  // What changed since the previous analysis, across the target queries that both have.
  let newly = 0;
  for (const target of targets) {
    const before = previous?.get(target.name);
    if (!before) continue;
    newly += target.rows.filter((item) => item.status === 'answered' && before.statuses.has(item.id) && before.statuses.get(item.id) !== 'answered').length;
  }
  const compared = targets.filter((target) => previous?.has(target.name)).length;
  $('t-changes').hidden = !previous || compared === 0;
  text('t-changes', afterPaste ? strings.changesAfterPaste(newly) : strings.changes(newly, compared));

  snapshot = new Map(
    targets.map((target) => [
      target.name,
      { answered: (target.stages?.source ?? target.counts).answered, statuses: new Map(target.rows.map((item) => [item.id, item.status])) },
    ]),
  );
  if (!afterPaste) analysed = snapshot;
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
        renderChecklist(message.boundary as Boundary, true);
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
    if (!message.ok) {
      result.className = 'status problem';
      result.textContent = strings.firstUnreadable;
    } else {
      firstCatalogue = new File([message.catalogue], 'catalogue.csv', { type: 'text/csv' });
      firstChecks = new File([message.checks], 'checks.csv', { type: 'text/csv' });
      result.className = 'status good';
      result.textContent = strings.firstReadDone(message.facts.tables, message.facts.columns, message.facts.sized);
      $<HTMLTextAreaElement>('first-paste').value = '';
    }
  } else if (message.type === 'state-zip') {
    save([message.zip], 'application/zip', 'schemalyser-state.zip');
  } else if (message.type === 'fact-added') {
    const result = $('t-paste-result');
    result.hidden = false;
    result.className = message.ok ? 'status good' : 'status problem';
    result.textContent = message.ok ? strings.factRecorded : strings.factUnreadable;
    if (message.ok && message.boundary) {
      renderChecklist(message.boundary as Boundary, true);
      zip = message.zip;
      checkScript = message.checkScript;
    }
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

function lock() {
  // The network is back while the page holds data: end the worker, which holds the requests
  // and the sandbox, and let go of the file names.
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

for (const input of inputs) input.addEventListener('change', show);

function startAnalysis() {
  const chosen = chosenFiles();
  if (!worker || navigator.onLine || !canAnalyse(chosen) || fetchWindow !== null) return;
  const { requests } = chosen;
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
  if (!worker || navigator.onLine || !chosen.requests.length) return;
  workerHasFiles = true;
  const steps = [...chosen.state].filter(([path]) => path.startsWith('conversion/') && path.endsWith('.sql')).map(([, file]) => file);
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
  query: catalogueQuery,
  copy: strings.copyQuery,
  't-query-safe': strings.querySafe,
  't-skip-catalogue': strings.skipCatalogue,
  't-loading': strings.loading,
  't-load-failed': strings.loadFailed,
  't-loaded': strings.loaded,
  't-no-files': strings.noFilesWhileConnected,
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
  't-build-what': sandboxStrings.buildWhat,
  'l-rows': sandboxStrings.rowsLabel,
  build: sandboxStrings.build,
  't-building': sandboxStrings.building,
  't-invented': sandboxStrings.invented,
  't-no-values-yet': sandboxStrings.noValuesYet,
  't-uses-checks': strings.usesChecks,
  'l-sql': sandboxStrings.queryWhat,
  run: sandboxStrings.run,
  's-translated': sandboxStrings.showTranslated,
  't-runs-chosen': strings.runsChosenRequests,
  'run-requests': sandboxStrings.runRequests,
  'h-safeguards': strings.safeguardsHeading,
  't-check-yourself': strings.checkYourself,
  'a-sandbox': strings.openSandbox,
  't-keeps-nothing': strings.keepsNothing,
};
for (const [id, value] of Object.entries(fixed)) text(id, value);
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
$('safeguards').replaceChildren(...strings.safeguards.map((sentence) => el('li', sentence)));

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
