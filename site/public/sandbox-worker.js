// The sandbox worker. It loads Pyodide, DuckDB, sqlglot and the schemalyser core from this
// site, builds the synthetic database and runs SQL against it.
//
// The page starts this file from a blob, so that the worker inherits the page's content
// security policy. For that reason the file imports nothing statically.

let pyodide = null;
let browser = null;

async function load(base) {
  const { loadPyodide } = await import(base + 'pyodide/pyodide.mjs');
  pyodide = await loadPyodide({ indexURL: base + 'pyodide/' });
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
async function policyHolds() {
  let refused = false;
  const note = () => {
    refused = true;
  };
  self.addEventListener('securitypolicyviolation', note);
  try {
    await fetch('https://policy-check.invalid/', { mode: 'no-cors' });
  } catch {
    // Refused by the policy, or unreachable. Only the policy raises the event.
  }
  await new Promise((resolve) => setTimeout(resolve, 100));
  self.removeEventListener('securitypolicyviolation', note);
  return refused;
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

async function build({ catalogue, inventory, rows }) {
  const started = browser.sandbox_start(await bytes(catalogue), await bytes(inventory));
  if (started === 'catalogue' || started === 'inventory') {
    self.postMessage({ type: `${started}-error` });
    return;
  }
  const built = JSON.parse(browser.sandbox_build(rows));
  self.postMessage({ type: 'built', ...built, noHeaders: started === 'ok-no-headers' });
}

async function requests({ requests }) {
  browser.sandbox_requests_begin();
  let done = 0;
  for (const { path, file } of requests) {
    browser.sandbox_request(path, await bytes(file));
    done += 1;
    self.postMessage({ type: 'progress', done, total: requests.length });
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
    } else if (message.type === 'build') {
      await build(message);
    } else if (message.type === 'run') {
      self.postMessage({ type: 'ran', result: JSON.parse(browser.sandbox_run(message.sql)) });
    } else if (message.type === 'requests') {
      await requests(message);
    }
  } catch (error) {
    // The error itself is never passed on: its text can quote a request.
    self.postMessage({ type: message.type === 'load' ? 'load-failed' : `${message.type}-failed` });
  }
};
