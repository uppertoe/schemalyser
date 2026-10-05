import { execFileSync } from 'node:child_process';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test, type BrowserContext, type Page } from '@playwright/test';
import { mockGitHub, sha, type MockRepository, type Seen } from './github-mock';
import { counts, expected, filesUnder, parseCsv } from './boundary';
import { setOnline } from './network';
import { githubStrings as g, strings } from '../src/strings';

// The GitHub path: the files are fetched while the browser is online, by a separate window whose
// policy allows GitHub, and analysed only once the browser is offline, by a page and a worker whose
// policy never allows it. GitHub is mocked throughout.

const fixtures = fileURLToPath(new URL('../../fixtures/', import.meta.url));
const shots = process.env.SCREENSHOTS;

test.skip(({ browserName }) => browserName === 'webkit', 'The page refuses to start in WebKit.');

const SUMMARY = 'Schemalyser has read 15 files. It was not able to read 2 of them in full, because each holds a part that Schemalyser could not parse, SQL that is built as text when it runs, a call to a stored procedure, a statement of a kind that Schemalyser does not analyse, or a query whose columns Schemalyser could not match to their tables.';
const TOKEN = 'github_pat_TESTONLY_' + 'x7Q2'.repeat(10);
const REQUESTS = 'example/analytics-requests';
const STATE = 'example/schemalyser-state';
const REQUESTS_COMMIT = sha('requests commit');
const STATE_COMMIT = sha('state commit');

const walk = (dir: string): string[] =>
  readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? walk(path) : [path];
  });

function requestsRepository(): MockRepository {
  const files: MockRepository['files'] = { 'README.md': { content: Buffer.from('Not a request.') } };
  for (const path of walk(fixtures + 'requests')) {
    files[relative(fixtures + 'requests', path).split('\\').join('/')] = { content: readFileSync(path) };
  }
  return { defaultBranch: 'main', refs: { main: REQUESTS_COMMIT }, files };
}

function stateRepository(): MockRepository {
  return {
    defaultBranch: 'main',
    refs: { main: sha('state main'), 'v1.0': STATE_COMMIT },
    files: {
      'catalogue.csv': { content: readFileSync(fixtures + 'invented-catalogue.csv') },
      'site-rules.json': { content: readFileSync(fixtures + 'invented-site-rules.json') },
      'checks.csv': { content: readFileSync(fixtures + 'invented-checks.csv') },
      // Only the three files at the root are taken.
      'old/catalogue.csv': { content: Buffer.from('not this one') },
      'notes.txt': { content: Buffer.from('nor this') },
    },
  };
}

async function openGitHubPath(page: Page) {
  await page.goto('./?source=github');
  await expect(page.getByText(strings.loaded)).toBeVisible({ timeout: 120_000 });
  await expect(page.getByRole('heading', { name: g.heading })).toBeVisible();
}

async function fetchFiles(
  page: Page,
  fields: { requests: string; requestsRef?: string; state?: string; stateRef?: string; token?: string },
) {
  await page.locator('#github-requests').fill(fields.requests);
  await page.locator('#github-requests-ref').fill(fields.requestsRef ?? '');
  await page.locator('#github-state').fill(fields.state ?? '');
  await page.locator('#github-state-ref').fill(fields.stateRef ?? '');
  await page.locator('#github-token').fill(fields.token ?? TOKEN);
  const [popup] = await Promise.all([page.waitForEvent('popup'), page.locator('#github-fetch').click()]);
  // The window closes itself once it has handed over the files or the reason it could not.
  await popup.waitForEvent('close', { timeout: 90_000 });
  await expect(page.locator('#t-github-progress')).toBeHidden();
}

// Every request that any page or worker in the context makes, with its method.
function recordRequests(context: BrowserContext) {
  const requests: { method: string; url: string; authorization?: string }[] = [];
  context.on('request', async (request) => {
    const entry = { method: request.method(), url: request.url(), authorization: undefined as string | undefined };
    requests.push(entry);
    entry.authorization = (await request.allHeaders().catch(() => ({}) as Record<string, string>))['authorization'];
  });
  return requests;
}

// Asks for GitHub from the page itself and from a new worker started from a blob, and reports
// whether the policy refused each one.
async function githubRefusedFromPage(page: Page) {
  return page.evaluate(async () => {
    const pageRefused = await new Promise<boolean>((resolve) => {
      let seen = false;
      document.addEventListener('securitypolicyviolation', (event) => {
        if (event.blockedURI.startsWith('https://api.github.com')) seen = true;
      });
      fetch('https://api.github.com/repos/example/analytics-requests', { mode: 'no-cors' })
        .catch(() => {})
        .finally(() => setTimeout(() => resolve(seen), 150));
    });
    const source = `let seen = false;
      self.addEventListener('securitypolicyviolation', (e) => { if (e.blockedURI.startsWith('https://api.github.com')) seen = true; });
      fetch('https://api.github.com/repos/example/analytics-requests', { mode: 'no-cors' }).catch(() => {})
        .finally(() => setTimeout(() => postMessage(seen), 150));`;
    const worker = new Worker(URL.createObjectURL(new Blob([source], { type: 'text/javascript' })));
    const workerRefused = await new Promise<boolean>((resolve) => (worker.onmessage = (event) => resolve(event.data)));
    worker.terminate();
    return { pageRefused, workerRefused };
  });
}

test('the page offers the GitHub section, and its own policy still never allows GitHub', async ({ page }) => {
  await page.goto('./');
  await expect(page.getByText(strings.loaded)).toBeVisible({ timeout: 120_000 });
  await expect(page.locator('#b-github')).toBeVisible();
  await expect(page.getByText(g.heading)).toBeVisible();
  const policies = await page.locator('meta[http-equiv="Content-Security-Policy"]').evaluateAll((metas) =>
    metas.map((meta) => meta.getAttribute('content')),
  );
  expect(policies).toEqual([
    "default-src 'none'; img-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; connect-src 'self'; worker-src blob:; base-uri 'none'; form-action 'none'",
  ]);
});

test('files fetched from two repositories, then analysed offline, give the same results as the same files chosen from this computer', async ({ page, context, browserName }) => {
  const seen = await mockGitHub(context, { [REQUESTS]: requestsRepository(), [STATE]: stateRepository() }, TOKEN);
  const requests = recordRequests(context);
  await openGitHubPath(page);
  const policyBefore = await page.locator('meta[http-equiv="Content-Security-Policy"]').getAttribute('content');

  await fetchFiles(page, { requests: REQUESTS, state: STATE, stateRef: 'v1.0' });
  await expect(page.locator('#github-fetched')).toContainText(g.fetched(15, REQUESTS, REQUESTS_COMMIT));
  await expect(page.locator('#github-fetched')).toContainText(g.fetched(3, STATE, STATE_COMMIT));
  await expect(page.getByText(g.closed)).toBeVisible();
  await expect(page.locator('#t-github-error')).toBeHidden();
  if (shots) await page.screenshot({ path: `${shots}/github-1-fetched.png`, fullPage: true });

  // The token is gone from the form, the page, storage and every address.
  await expect(page.locator('#github-token')).toHaveValue('');
  expect(await page.content()).not.toContain(TOKEN);
  const stored = await page.evaluate(async () => ({
    local: localStorage.length,
    session: sessionStorage.length,
    cookies: document.cookie,
    databases: (await indexedDB.databases()).length,
  }));
  expect(stored).toEqual({ local: 0, session: 0, cookies: '', databases: 0 });
  for (const request of requests) expect(request.url).not.toContain(TOKEN);

  // Only this site and GitHub were contacted; GitHub was sent GET requests only, and the token went
  // to GitHub alone, in the Authorization header.
  const origins = new Set(requests.filter((r) => /^https?:/.test(r.url)).map((r) => new URL(r.url).origin));
  expect(requests.filter((r) => !/^(https?|blob|data):/.test(r.url))).toEqual([]);
  expect([...origins].sort()).toEqual(['http://localhost:4173', 'https://api.github.com']);
  expect(seen.length).toBeGreaterThan(0);
  for (const request of seen) expect(request.method).toBe('GET');
  for (const request of requests.filter((r) => r.url.startsWith('https://api.github.com'))) expect(request.method).toBe('GET');
  for (const request of requests.filter((r) => !r.url.startsWith('https://api.github.com'))) {
    expect(request.authorization).toBeUndefined();
  }
  // Nor did either self-check reach GitHub: both were refused by a policy.
  expect(seen.map((r) => new URL(r.url).pathname)).not.toContain('/');

  // The page's own policy never changed, and GitHub is refused from the page and from a worker.
  expect(await page.locator('meta[http-equiv="Content-Security-Policy"]').getAttribute('content')).toBe(policyBefore);
  const before = seen.length;
  expect(await githubRefusedFromPage(page)).toEqual({ pageRefused: true, workerRefused: true });
  expect(seen.length).toBe(before);

  // Offline, the fetched files are analysed in the same way as chosen ones.
  await setOnline(page, context, browserName, false);
  await expect(page.getByText(strings.offline)).toBeVisible();
  await expect(page.locator('#github-fetched')).toContainText(g.fetched(15, REQUESTS, REQUESTS_COMMIT));
  await page.locator('#analyse').click();
  await expect(page.locator('#t-files-sentence')).toHaveText(SUMMARY, { timeout: 60_000 });
  await expect(page.locator('#github-provenance p')).toHaveText([
    g.provenance(REQUESTS, REQUESTS_COMMIT),
    g.provenance(STATE, STATE_COMMIT),
  ]);
  if (shots) await page.screenshot({ path: `${shots}/github-2-results.png`, fullPage: true });
  const capture = async () => ({
    found: await page.locator('#t-found-sentence').innerText(),
    checks: await page.locator('#t-checks-used').innerText(),
    pack: await page.locator('#pack').innerText(),
    index: await page.locator('#index').innerText(),
  });
  const fromGitHub = await capture();
  expect(fromGitHub.checks).toContain('Schemalyser has used your check results');

  // The same files, chosen from this computer.
  await page.locator('#clear').click();
  await expect(page.locator('#github-fetched')).toBeEmpty();
  await expect(page.locator('#analyse')).toBeDisabled();
  await page.locator('#catalogue').setInputFiles(fixtures + 'invented-catalogue.csv');
  await page.locator('#rules').setInputFiles(fixtures + 'invented-site-rules.json');
  await page.locator('#folder').setInputFiles(fixtures + 'requests');
  await page.locator('#checks').setInputFiles(fixtures + 'invented-checks.csv');
  await page.locator('#analyse').click();
  // The summary line keeps its text after clearing, so the index is what shows the new results.
  await expect(page.locator('#index li')).toHaveCount(15, { timeout: 60_000 });
  await expect(page.locator('#t-files-sentence')).toHaveText(SUMMARY);
  await expect(page.locator('#github-provenance')).toBeHidden();
  expect(await capture()).toEqual(fromGitHub);

  // While offline nothing went to GitHub, and the network's return still locks the page.
  expect(seen.length).toBe(before);
  await setOnline(page, context, browserName, true);
  await expect(page.getByText(strings.reconnected)).toBeVisible();
  await expect.poll(() => page.workers().length).toBe(0);
});

test('the fetch window can reach only this site and GitHub, and does nothing unless this page opened it', async ({ page, context }) => {
  const seen = await mockGitHub(context, { [REQUESTS]: requestsRepository() }, TOKEN);
  await page.goto('./github.html');
  const reached = await page.evaluate(async () => {
    const blocked: string[] = [];
    document.addEventListener('securitypolicyviolation', (event) => blocked.push(new URL(event.blockedURI).origin));
    const github = await fetch('https://api.github.com/repos/example/analytics-requests').then((r) => r.status, () => 'refused');
    await fetch('https://policy-check.invalid/', { mode: 'no-cors' }).catch(() => {});
    await new Promise((resolve) => setTimeout(resolve, 150));
    return { github, blocked };
  });
  // GitHub answered (the mock refuses a request without the token), and the other site was refused.
  expect(reached).toEqual({ github: 401, blocked: ['https://policy-check.invalid'] });
  expect(seen).toHaveLength(1);
  // Opened on its own, the window has no page to serve and asks GitHub for nothing more.
  await page.waitForTimeout(500);
  expect(seen).toHaveLength(1);
  await expect(page.locator('#t-github-progress')).toBeEmpty();
});

test('a wrong token, a missing repository, a missing branch and a lost network each leave a message and no files', async ({ page, context }) => {
  const seen = await mockGitHub(context, { [REQUESTS]: requestsRepository(), [STATE]: stateRepository() }, TOKEN);
  await openGitHubPath(page);

  await fetchFiles(page, { requests: REQUESTS, token: 'github_pat_WRONG' });
  await expect(page.locator('#t-github-error')).toHaveText(g.unauthorised(REQUESTS));
  await expect(page.locator('#github-token')).toHaveValue('');
  await expect(page.locator('#github-fetched')).toBeEmpty();
  await expect(page.getByText(g.closed)).toBeHidden();
  if (shots) await page.screenshot({ path: `${shots}/github-3-error.png`, fullPage: true });

  await fetchFiles(page, { requests: REQUESTS, state: 'example/missing' });
  await expect(page.locator('#t-github-error')).toHaveText(g.notFound('example/missing', g.defaultBranch));
  await expect(page.locator('#github-fetched')).toBeEmpty();

  await fetchFiles(page, { requests: REQUESTS, requestsRef: 'no-such-branch' });
  await expect(page.locator('#t-github-error')).toHaveText(g.notFound(REQUESTS, 'no-such-branch'));

  // With the error gone, the next fetch succeeds.
  await fetchFiles(page, { requests: REQUESTS });
  await expect(page.locator('#t-github-error')).toBeHidden();
  await expect(page.locator('#github-fetched')).toContainText(g.fetched(15, REQUESTS, REQUESTS_COMMIT));
  for (const request of seen) expect(request.method).toBe('GET');
  expect(await page.content()).not.toContain('github_pat_');

  // GitHub cannot be reached.
  await context.unroute('https://api.github.com/**');
  await mockGitHub(context, {}, TOKEN, { abort: true });
  await fetchFiles(page, { requests: REQUESTS });
  await expect(page.locator('#t-github-error')).toHaveText(g.offline);
  await expect(page.locator('#github-fetched')).toBeEmpty();
});

test('large files are left out, the fetch stops at 5,000 files, and a partial list is reported', async ({ page, context }) => {
  test.setTimeout(240_000);
  const files: MockRepository['files'] = { 'huge.sql': { size: 3 * 1024 * 1024 } };
  for (let i = 0; i < 5_001; i++) files[`requests/${String(i).padStart(5, '0')}.sql`] = { content: Buffer.from('SELECT 1') };
  const seen = await mockGitHub(
    context,
    { [REQUESTS]: { defaultBranch: 'main', refs: { main: REQUESTS_COMMIT }, files, truncated: true } },
    TOKEN,
  );
  await openGitHubPath(page);
  await fetchFiles(page, { requests: REQUESTS });
  await expect(page.locator('#github-fetched')).toContainText(g.fetched(5_000, REQUESTS, REQUESTS_COMMIT));
  await expect(page.getByText(g.skipped(1))).toBeVisible();
  await expect(page.getByText(g.tooMany)).toBeVisible();
  await expect(page.getByText(g.truncated)).toBeVisible();
  // The large file was never asked for: it was left out on the size that GitHub listed.
  const blobs = seen.filter((request) => request.url.includes('/git/blobs/'));
  expect(blobs).toHaveLength(5_000);
  expect(blobs.some((request) => request.url.includes(sha(`${REQUESTS}:huge.sql`)))).toBe(false);
});

test('a fetch window served without its policy fetches nothing', async ({ page, context }) => {
  const seen = await mockGitHub(context, { [REQUESTS]: requestsRepository() }, TOKEN);
  await context.route('http://localhost:4173/github.html', async (route) => {
    const response = await route.fetch();
    const body = (await response.text()).replace(/<meta\s+http-equiv="Content-Security-Policy"[\s\S]*?\/>/, '');
    await route.fulfill({ response, body });
  });
  await openGitHubPath(page);
  await fetchFiles(page, { requests: REQUESTS });
  await expect(page.locator('#t-github-error')).toHaveText(g.policyBefore);
  await expect(page.locator('#github-fetched')).toBeEmpty();
  expect(seen).toEqual([]);
});

test('if the fetch window cannot close its connection to GitHub, it hands over no files', async ({ page, context }) => {
  const seen = await mockGitHub(context, { [REQUESTS]: requestsRepository() }, TOKEN);
  // In the fetch window only, the second, stricter policy is quietly dropped, by a script that the
  // router adds to the window's page ahead of its own.
  await context.route('http://localhost:4173/drop-policy.js', (route) =>
    route.fulfill({
      contentType: 'text/javascript',
      body: `const append = Element.prototype.append;
        Element.prototype.append = function (...nodes) {
          return append.apply(this, nodes.filter((node) => !(node instanceof HTMLMetaElement)));
        };`,
    }),
  );
  await context.route('http://localhost:4173/github.html', async (route) => {
    const response = await route.fetch();
    const body = (await response.text()).replace('<title>', '<script src="./drop-policy.js"></script><title>');
    await route.fulfill({ response, body });
  });
  await openGitHubPath(page);
  await fetchFiles(page, { requests: REQUESTS });
  await expect(page.locator('#t-github-error')).toHaveText(g.policyAfter);
  await expect(page.locator('#github-fetched')).toBeEmpty();
  await expect(page.getByText(g.closed)).toBeHidden();
  // The window's own check is what reached GitHub here, and without the token.
  const check = seen.find((request) => new URL(request.url).pathname === '/');
  expect(check?.authorization).toBeUndefined();
  for (const request of seen) expect(request.method).toBe('GET');
});

test('a page whose policy allows GitHub refuses to start', async ({ page, context }) => {
  const seen = await mockGitHub(context, {}, TOKEN);
  await page.route('http://localhost:4173/', async (route) => {
    const response = await route.fetch();
    const body = (await response.text()).replace("connect-src 'self';", "connect-src 'self' https://api.github.com;");
    await route.fulfill({ response, body });
  });
  await page.goto('./');
  await expect(page.getByText(strings.policyFailed)).toBeVisible({ timeout: 120_000 });
  await expect(page.locator('#step-3 .body')).toBeHidden();
  // The worker's own check reached the mock, which shows that it asked GitHub and was not refused.
  expect(seen.map((request) => request.url)).toEqual(['https://api.github.com/']);
});

test('the state can come from GitHub, with its conversion and target queries, and gives the same checklist', async ({ page, context, browserName }) => {
  test.setTimeout(300_000);
  const world = expected();
  // Schemalyser's repository in the layout of the state folder, with files that are not part of the state.
  const files: MockRepository['files'] = {
    'README.md': { content: Buffer.from('Not part of the state.') },
    'targets/drafts/not_this.sql': { content: Buffer.from('SELECT 1;') },
    'old/catalogue.csv': { content: Buffer.from('not this one') },
  };
  const state = filesUnder(world.state);
  for (const [path, content] of Object.entries(state)) files[path] = { content };
  const stateRepository: MockRepository = { defaultBranch: 'main', refs: { main: STATE_COMMIT }, files };
  await mockGitHub(context, { [REQUESTS]: requestsRepository(), [STATE]: stateRepository }, TOKEN);
  await openGitHubPath(page);
  await fetchFiles(page, { requests: REQUESTS, state: STATE });
  await expect(page.locator('#github-fetched')).toContainText(g.fetched(Object.keys(state).length, STATE, STATE_COMMIT));

  await setOnline(page, context, browserName, false);
  await page.locator('#analyse').click();
  await expect(page.locator('#checklists section.target')).toHaveCount(world.targets.length, { timeout: 120_000 });
  for (const name of world.targets) {
    const want = counts(world.before, name);
    await expect(page.locator(`section.target[data-target="${name}"] .tally`)).toHaveText(strings.tally(want.answered, want.total));
  }
  const fromGitHub = await page.locator('#checklists').innerText();

  // The boundary's provenance names both commits, because every file came from them.
  const [download] = await Promise.all([page.waitForEvent('download'), page.locator('#download').click()]);
  const provenance = JSON.parse(execFileSync('unzip', ['-p', await download.path(), 'boundary/provenance.json'], { encoding: 'utf8' }));
  expect(provenance.stateCommit).toBe(STATE_COMMIT);
  expect(provenance.requestsCommit).toBe(REQUESTS_COMMIT);
  const checklist = execFileSync('unzip', ['-p', await download.path(), `boundary/targets/${world.targets[0]}/checklist.csv`], { encoding: 'utf8' });
  expect(parseCsv(checklist)).toEqual(parseCsv(world.before[`targets/${world.targets[0]}/checklist.csv`]));

  // The same state, chosen from this computer as a folder, gives the same checklist.
  await page.locator('#clear').click();
  await page.locator('#state-folder').setInputFiles(world.state);
  await page.locator('#folder').setInputFiles(fixtures + 'requests');
  await page.locator('#analyse').click();
  await expect(page.locator('#index li')).toHaveCount(15, { timeout: 120_000 });
  await expect(page.locator('#checklists section.target')).toHaveCount(world.targets.length);
  expect(await page.locator('#checklists').innerText()).toBe(fromGitHub);

  await setOnline(page, context, browserName, true);
  await expect(page.getByText(strings.reconnected)).toBeVisible();
  await expect.poll(() => page.workers().length).toBe(0);
});
