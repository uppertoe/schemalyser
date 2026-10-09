// The worker of the page Describe the record. It loads Pyodide, DuckDB, sqlglot and the schemalyser core from this
// site, and it is the only place where the dictionary and the hospital schema exist while the page is open.
//
// The page starts this file from a blob, so that the worker inherits the page's content
// security policy. For that reason the file imports nothing statically.

let pyodide = null;
let browser = null;

async function load(base) {
  const { loadPyodide } = await import(base + 'pyodide/pyodide.mjs');
  pyodide = await loadPyodide({ indexURL: base + 'pyodide/' });
  // DuckDB is loaded now, while the network is still on, so that the test on made-up rows works offline.
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
    } else if (message.type === 'describe') {
      // Screen 1 (index.html, the front page): one call of a describe_ function of the bridge. Files are read here into bytes, and
      // the dictionary and the hospital schema stay in this worker's memory.
      if (!/^describe_[a-z_]+$/.test(message.call) || typeof browser[message.call] !== 'function') throw new Error('call');
      let reply;
      if (message.call === 'describe_propose') {
        reply = browser.describe_propose((done, total) => self.postMessage({ type: 'describe-progress', id: message.id, done, total }));
      } else if (message.call === 'describe_schema_zip') {
        const zip = browser.describe_schema_zip().toJs();
        self.postMessage({ type: 'describe-reply', id: message.id, zip }, [zip.buffer]);
        return;
      } else {
        const args = [];
        for (const arg of message.args ?? []) args.push(arg instanceof Blob ? await bytes(arg) : arg);
        reply = browser[message.call](...args);
      }
      self.postMessage({ type: 'describe-reply', id: message.id, reply });
    }
  } catch (error) {
    // The error itself is never passed on: its text can quote a request.
    const failed = message.type === 'load' ? 'load-failed' : `${message.type}-failed`;
    self.postMessage({ type: failed, id: message.id });
  }
};
