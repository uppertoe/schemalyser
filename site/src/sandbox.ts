import { sandboxStrings as s } from './sandbox-strings';
import { strings } from './strings';

declare const __VERSION__: string;

type State = 'loading' | 'load-failed' | 'ready' | 'building' | 'built';

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
const text = (id: string, value: string) => ($(id).textContent = value);

const catalogue = $<HTMLInputElement>('catalogue');
const inventory = $<HTMLInputElement>('inventory');
const folder = $<HTMLInputElement>('folder');
const rowsInput = $<HTMLInputElement>('rows');

let state: State = 'loading';
let worker: Worker | null = null;
let runningRequests = false;
let requestsRan = false;

function show() {
  const online = navigator.onLine;
  const built = state === 'built';
  const steps = built ? ['done', 'current', 'current', 'current'] : ['current', 'current', 'upcoming', 'upcoming'];
  steps.forEach((stepState, i) => ($(`step-${i + 1}`).dataset.state = stepState));
  if (state === 'load-failed') $('step-2').dataset.state = 'problem';

  $('t-loading').hidden = state !== 'loading';
  $('t-load-failed').hidden = state !== 'load-failed';
  $('b-build-form').hidden = !(state === 'loading' || state === 'ready');
  $('t-building').hidden = state !== 'building';
  $('b-built').hidden = !built;
  $<HTMLButtonElement>('build').disabled = !(state === 'ready' && catalogue.files?.length && inventory.files?.length);

  const connection = $('t-connection');
  connection.textContent = online ? strings.connected : strings.isOffline;
  connection.dataset.online = String(online);

  folder.disabled = online || runningRequests;
  const count = sqlFiles().length;
  $('t-folder-count').hidden = !folder.files?.length;
  text('t-folder-count', strings.folderCount(count));
  $<HTMLButtonElement>('run-requests').disabled = online || runningRequests || !count;
  $('b-progress').hidden = !runningRequests;
}

function sqlFiles() {
  return Array.from(folder.files ?? [])
    .filter((file) => /\.sql$/i.test(file.name))
    .map((file) => ({ path: file.webkitRelativePath.split('/').slice(1).join('/') || file.name, file }))
    .sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0));
}

function grid(header: string[] | null, rows: (string | null)[][]) {
  const result = el('table');
  if (header) {
    const head = result.createTHead().insertRow();
    for (const name of header) head.append(el('th', name));
  }
  const body = result.createTBody();
  for (const row of rows) {
    const line = body.insertRow();
    for (const value of row) {
      const cell = line.insertCell();
      cell.textContent = value ?? 'NULL';
      if (value === null) cell.className = 'null';
      else if (/^-?\d+(\.\d+)?$/.test(value)) cell.className = 'number';
    }
  }
  const frame = el('div', undefined, 'table-frame');
  frame.append(result);
  return frame;
}

function showRun(result: RunResult) {
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
  if (event.target !== worker) return;
  const message = event.data;
  if (message.type === 'ready') {
    state = 'ready';
  } else if (message.type === 'load-failed' || message.type === 'policy-failed') {
    state = 'load-failed';
    if (message.type === 'policy-failed') text('t-load-failed', strings.policyFailed);
  } else if (message.type === 'catalogue-error' || message.type === 'inventory-error') {
    state = 'ready';
    $(`t-${message.type}`).hidden = false;
  } else if (message.type === 'built') {
    state = 'built';
    text('t-built', message.sentence);
    $('t-no-headers').hidden = !message.noHeaders;
  } else if (message.type === 'build-failed') {
    state = 'ready';
    $('t-inventory-error').hidden = false;
  } else if (message.type === 'ran') {
    showRun(message.result as RunResult);
    $<HTMLButtonElement>('run').disabled = false;
  } else if (message.type === 'run-failed') {
    showRun({ status: 'database-error', message: '' });
    $<HTMLButtonElement>('run').disabled = false;
  } else if (message.type === 'progress') {
    text('t-progress', strings.progress(message.done, message.total));
    $<HTMLProgressElement>('progress-bar').value = message.total ? message.done / message.total : 0;
  } else if (message.type === 'requests-result') {
    runningRequests = false;
    text('t-requests-sentence', message.sentence);
    const names = new Map<number, string>(message.index);
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
  }
  show();
}

async function startWorker() {
  try {
    const response = await fetch(new URL('sandbox-worker.js', document.baseURI));
    if (!response.ok) throw new Error('worker');
    // Started from a blob so that the worker inherits this page's content security policy.
    const source = new Blob([await response.text()], { type: 'text/javascript' });
    worker = new Worker(URL.createObjectURL(source), { type: 'module' });
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

window.addEventListener('online', () => {
  // The folder of requests is accepted only offline. When the network returns, the page lets go
  // of the folder and of the file names, and it ends the worker if requests were still running.
  folder.value = '';
  $('requests-table').replaceChildren();
  $('requests-result').hidden = true;
  // Once requests have been run, the worker is ended when the network returns, whatever it holds.
  if (runningRequests || requestsRan) {
    worker?.terminate();
    runningRequests = false;
    requestsRan = false;
    state = 'loading';
    $('query-result').hidden = true;
    void startWorker();
  }
  show();
});
window.addEventListener('offline', show);

for (const input of [catalogue, inventory, folder]) input.addEventListener('change', show);

$('build').addEventListener('click', () => {
  if (!worker || !catalogue.files?.length || !inventory.files?.length) return;
  $('t-catalogue-error').hidden = true;
  $('t-inventory-error').hidden = true;
  state = 'building';
  show();
  const rows = Math.min(5000, Math.max(10, Number(rowsInput.value) || 500));
  worker.postMessage({ type: 'build', catalogue: catalogue.files[0], inventory: inventory.files[0], rows });
});

$('run').addEventListener('click', () => {
  const sql = $<HTMLTextAreaElement>('sql').value;
  if (!worker || state !== 'built' || !sql.trim()) return;
  $<HTMLButtonElement>('run').disabled = true;
  worker.postMessage({ type: 'run', sql });
});

$('run-requests').addEventListener('click', () => {
  if (!worker || state !== 'built' || navigator.onLine) return;
  const requests = sqlFiles();
  runningRequests = true;
  requestsRan = true;
  $('requests-result').hidden = true;
  text('t-progress', strings.progress(0, requests.length));
  $<HTMLProgressElement>('progress-bar').value = 0;
  show();
  worker.postMessage({ type: 'requests', requests });
});

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
  intro: s.intro,
  'l-catalogue': strings.chooseCatalogue,
  't-catalogue-note': strings.catalogueNote,
  'l-inventory': s.chooseInventory,
  't-inventory-note': s.inventoryNote,
  't-catalogue-error': strings.catalogueError,
  't-inventory-error': s.inventoryError,
  't-loading': strings.loading,
  't-load-failed': strings.loadFailed,
  't-build-what': s.buildWhat,
  'l-rows': s.rowsLabel,
  build: s.build,
  't-building': s.building,
  't-no-headers': strings.noHeaders,
  't-invented': s.invented,
  't-no-values-yet': s.noValuesYet,
  'l-sql': s.queryWhat,
  run: s.run,
  's-translated': s.showTranslated,
  't-offline-only': s.offlineOnly,
  'l-folder': strings.chooseFolder,
  't-folder-note': strings.folderNote,
  'run-requests': s.runRequests,
  't-index-note': strings.indexNote,
  'h-safeguards': strings.safeguardsHeading,
  't-check-yourself': strings.checkYourself,
  'a-first': strings.openFirstPage,
  't-keeps-nothing': strings.keepsNothing,
};
for (const [id, value] of Object.entries(fixed)) text(id, value);
s.steps.forEach((heading, i) => text(`h-step-${i + 1}`, heading));
$('safeguards').replaceChildren(...[strings.safeguards[0], s.onlyInThisTab].map((sentence) => el('li', sentence)));

window.addEventListener('pagehide', () => ($<HTMLTextAreaElement>('sql').value = ''));

show();
void showVersion();
void startWorker();
