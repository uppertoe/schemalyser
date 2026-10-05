// The analysis worker. It loads Pyodide, DuckDB, sqlglot and the schemalyser core from this
// site, and it is the only place where the contents of a request ever exist.
//
// The page starts this file from a blob, so that the worker inherits the page's content
// security policy. For that reason the file imports nothing statically.

let pyodide = null;
let browser = null;

async function load(base) {
  const { loadPyodide } = await import(base + 'pyodide/pyodide.mjs');
  pyodide = await loadPyodide({ indexURL: base + 'pyodide/' });
  // DuckDB is loaded now, while the network is still on, so that the sandbox step works offline.
  await pyodide.loadPackage('duckdb', { messageCallback: () => {}, errorCallback: () => {} });
  const sitePackages = pyodide.runPython('import site; site.getsitepackages()[0]');
  for (const [file, format] of [['py/sqlglot.whl', 'wheel'], ['py/schemalyser.zip', 'zip']]) {
    const response = await fetch(base + file);
    if (!response.ok) throw new Error('asset');
    pyodide.unpackArchive(await response.arrayBuffer(), format, { extractDir: sitePackages });
  }
  browser = pyodide.pyimport('schemalyser.browser');
}

// The worker checks for itself that it is under the page's content security policy, by asking for
// an address on another site and seeing the policy refuse it. If the policy does not refuse it,
// the worker does not start: a browser that does not pass the policy on to a worker started from
// a blob is one in which the tool must not be used.
//
// It then asks for GitHub's API in the same way, since the page can fetch from GitHub in a
// separate window, and this worker must never be able to reach it. GitHub is asked only once the
// first address has been refused, so a worker without any policy never contacts it.
async function refused(address) {
  const origin = new URL(address).origin;
  let seen = false;
  const note = (event) => {
    if (event.blockedURI.startsWith(origin)) seen = true;
  };
  self.addEventListener('securitypolicyviolation', note);
  try {
    await fetch(address, { mode: 'no-cors', credentials: 'omit' });
  } catch {
    // Refused by the policy, or unreachable. Only the policy raises the event.
  }
  await new Promise((resolve) => setTimeout(resolve, 100));
  self.removeEventListener('securitypolicyviolation', note);
  return seen;
}

async function policyHolds() {
  return (await refused('https://policy-check.invalid/')) && (await refused('https://api.github.com/'));
}

// Once everything is loaded, the worker has no further use for the network. Its means of
// reaching one are removed, as a second defence behind the content security policy.
function sealNetwork() {
  for (const name of ['fetch', 'XMLHttpRequest', 'WebSocket', 'EventSource', 'importScripts', 'WebTransport']) {
    try {
      Object.defineProperty(self, name, { value: undefined, writable: false, configurable: false });
    } catch {
      // A browser that will not let the name be redefined still has the policy.
    }
  }
}

async function bytes(file) {
  return new Uint8Array(await file.arrayBuffer());
}

// The boundary: the state and the requests are written into Pyodide's own file system, in the layout
// of the state folder, and boundary.produce runs over them, as the boundary command does. The files
// stay inside this worker.
async function runBoundary(state, requests, commits) {
  browser.boundary_begin();
  try {
    // A path that cannot be written is refused and counted by boundary_put, and never stops the run.
    for (const { path, file } of state) browser.boundary_put('state', path, await bytes(file));
    for (const { path, file } of requests) browser.boundary_put('requests', path, await bytes(file));
    return JSON.parse(browser.boundary_run(commits?.state ?? null, commits?.requests ?? null));
  } catch {
    // As elsewhere, the error itself is never passed on. The inventory is still shown.
    browser.boundary_begin();
    return { ok: false, problem: 'other' };
  }
}

async function analyse({ catalogue, rules, checks, requests, state, commits }) {
  const started = browser.start(
    await bytes(catalogue),
    rules ? await bytes(rules) : undefined,
    checks ? await bytes(checks) : undefined,
  );
  if (started === 'catalogue' || started === 'checks') {
    self.postMessage({ type: `${started}-error` });
    return;
  }
  let done = 0;
  for (const { path, file } of requests) {
    browser.add(path, await bytes(file));
    done += 1;
    self.postMessage({ type: 'progress', done, total: requests.length });
  }
  const result = JSON.parse(browser.finish());
  self.postMessage({ type: 'boundary-progress' });
  const began = performance.now();
  const boundary = await runBoundary(state ?? [], requests, commits);
  const seconds = (performance.now() - began) / 1000;
  const zip = browser.pack_zip().toJs();
  const checkScript = browser.check_script();
  self.postMessage(
    { type: 'result', ...result, boundary, seconds, checkScript, noHeaders: started === 'ok-no-headers', zip },
    [zip.buffer],
  );
}

async function runRequests({ requests }) {
  browser.sandbox_requests_begin();
  let done = 0;
  for (const { path, file } of requests) {
    browser.sandbox_request(path, await bytes(file));
    done += 1;
    self.postMessage({ type: 'requests-progress', done, total: requests.length });
  }
  self.postMessage({ type: 'requests-result', ...JSON.parse(browser.sandbox_requests_finish()) });
}

self.onmessage = async (event) => {
  const message = event.data;
  try {
    if (message.type === 'load') {
      await load(message.base);
      if (!(await policyHolds())) {
        self.postMessage({ type: 'policy-failed' });
        return;
      }
      sealNetwork();
      self.postMessage({ type: 'ready' });
    } else if (message.type === 'analyse') {
      await analyse(message);
    } else if (message.type === 'clear') {
      browser.clear();
      self.postMessage({ type: 'cleared' });
    } else if (message.type === 'build') {
      browser.sandbox_from_analysis();
      self.postMessage({ type: 'built', ...JSON.parse(browser.sandbox_build(message.rows)) });
    } else if (message.type === 'run') {
      self.postMessage({ type: 'ran', result: JSON.parse(browser.sandbox_run(message.sql)) });
    } else if (message.type === 'requests') {
      await runRequests(message);
    } else if (message.type === 'first-ask') {
      // The names of the tables that the requests and the conversion read, for the first query. The text of
      // the files is read here and let go of; only the names stay, in the query.
      browser.first_ask_begin();
      for (const file of [...message.requests.map((entry) => entry.file), ...message.steps]) browser.first_ask_add(await bytes(file));
      self.postMessage({ type: 'first-query', ...JSON.parse(browser.first_ask_query()) });
    } else if (message.type === 'first-read') {
      const reply = browser.first_ask_read(
        message.text,
        message.rules ? await bytes(message.rules) : undefined,
        message.checks ? await bytes(message.checks) : undefined,
      );
      self.postMessage({ type: 'first-read', ...JSON.parse(reply) });
    } else if (message.type === 'fact') {
      // A fact that a person confirmed: the core checks it against the catalogue and works the checklists out again.
      const reply = JSON.parse(browser.fact_add(message.fact));
      if (reply.ok) {
        const zip = browser.pack_zip().toJs();
        self.postMessage({ type: 'fact-added', ...reply, zip, checkScript: browser.check_script() }, [zip.buffer]);
      } else self.postMessage({ type: 'fact-added', ...reply });
    } else if (message.type === 'facts' || message.type === 'settings') {
      // Several answers at once, or the audit's settings: the core checks them and works the checklists out again.
      const reply = JSON.parse(message.type === 'facts' ? browser.facts_add(message.facts) : browser.settings_set(message.settings));
      if (reply.ok) {
        const zip = browser.pack_zip().toJs();
        self.postMessage({ type: 'fact-added', ...reply, zip, checkScript: browser.check_script() }, [zip.buffer]);
      } else self.postMessage({ type: 'fact-added', ...reply });
    } else if (message.type === 'search-sql') {
      self.postMessage({ type: 'search-sql', group: message.group, ...JSON.parse(browser.codes_search_sql(message.request)) });
    } else if (message.type === 'charted') {
      self.postMessage({ type: 'charted', ...JSON.parse(browser.charted_read(message.text)) });
    } else if (message.type === 'year-count') {
      self.postMessage({ type: 'year-counted', ...JSON.parse(browser.year_count_read(message.text)) });
    } else if (message.type === 'codes-search') {
      // The names that the search returns are shown on the page and written into no file.
      self.postMessage({ type: 'codes-found', group: message.group, ...JSON.parse(browser.codes_search_read(message.text)) });
    } else if (message.type === 'state-zip') {
      const zip = browser.state_zip().toJs();
      self.postMessage({ type: 'state-zip', zip }, [zip.buffer]);
    } else if (message.type === 'paste' || message.type === 'profile-paste') {
      // The pasted results are read, and the checklists worked out again, by the core, as the boundary does.
      const reply = JSON.parse(
        message.type === 'paste' ? browser.checks_paste(message.text) : browser.profile_paste(message.text),
      );
      const type = message.type === 'paste' ? 'pasted' : 'profile-pasted';
      if (reply.boundary) {
        // The download and the check script follow the merged check results.
        const zip = browser.pack_zip().toJs();
        self.postMessage({ type, ...reply, zip, checkScript: browser.check_script() }, [zip.buffer]);
      } else self.postMessage({ type, ...reply });
    }
  } catch (error) {
    // The error itself is never passed on: its text can quote a request.
    const failed = { load: 'load-failed', analyse: 'analysis-failed' }[message.type] ?? `${message.type}-failed`;
    self.postMessage({ type: failed });
  }
};
