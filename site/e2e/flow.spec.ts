import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';
import { setOnline } from './network';
import { strings } from '../src/strings';

const fixtures = fileURLToPath(new URL('../../fixtures/', import.meta.url));
const shots = process.env.SCREENSHOTS;

// WebKit does not report the content security policy to a worker started from a blob, so the page
// cannot confirm that the policy holds there and refuses to start. See webkit.spec.ts.
test.skip(({ browserName }) => browserName === 'webkit', 'The page refuses to start in WebKit.');
const SUMMARY = 'Schemalyser has read 15 files. It was not able to read 2 of them in full, because each holds a part that Schemalyser could not parse, SQL that is built as text when it runs, a call to a stored procedure, a statement of a kind that Schemalyser does not analyse, or a query whose columns Schemalyser could not match to their tables.';
const planted = readFileSync(fixtures + 'planted-values.txt', 'utf8').split('\n').filter(Boolean);

test('the page loads, waits for the network to be off, analyses, and locks when the network returns', async ({ page, context, browserName }) => {
  const requestsWhileOffline: string[] = [];
  const violations: string[] = [];
  let offline = false;
  page.on('request', (request) => {
    if (offline && !request.url().startsWith('blob:')) requestsWhileOffline.push(request.url());
  });
  page.on('console', (message) => {
    if (message.text().includes('Content Security Policy')) violations.push(message.text());
  });

  // Loading, then loaded but still connected: no files are accepted.
  await page.goto('./');
  await expect(page.getByText(strings.loaded)).toBeVisible({ timeout: 90_000 });
  await expect(page.getByText(strings.noFilesWhileConnected)).toBeVisible();
  await expect(page.locator('#step-3 .body')).toBeHidden();
  await expect(page.locator('#t-connection')).toHaveText(strings.connected);
  await expect(page.getByText(strings.catalogueWhy)).toBeVisible();
  await expect(page.locator('#query')).toContainText('INFORMATION_SCHEMA.COLUMNS');
  if (shots) await page.screenshot({ path: `${shots}/1-connected.png`, fullPage: true });
  expect(violations).toEqual([]);

  // The worker started only because it saw the policy refuse an outside address (see the test of a
  // page without the policy below). Its own means of reaching a network have since been removed.
  const [worker] = page.workers();
  const sealed = await worker.evaluate(() =>
    ['fetch', 'XMLHttpRequest', 'WebSocket', 'EventSource'].every(
      (name) => (self as unknown as Record<string, unknown>)[name] === undefined,
    ),
  );
  expect(sealed).toBe(true);
  violations.length = 0;

  // Nothing is kept in the browser.
  const stored = await page.evaluate(async () => ({
    local: localStorage.length,
    session: sessionStorage.length,
    cookies: document.cookie,
    databases: (await indexedDB.databases()).length,
  }));
  expect(stored).toEqual({ local: 0, session: 0, cookies: '', databases: 0 });

  // Offline: the files can be chosen.
  await setOnline(page, context, browserName, false);
  offline = true;
  await expect(page.getByText(strings.offline)).toBeVisible();
  await expect(page.locator('#analyse')).toBeDisabled();
  await expect(page.locator('#t-connection')).toHaveText(strings.isOffline);
  if (shots) await page.screenshot({ path: `${shots}/2-offline.png`, fullPage: true });

  // A file that is not a catalogue is refused.
  await page.locator('#catalogue').setInputFiles(fixtures + 'planted-values.txt');
  await page.locator('#folder').setInputFiles(fixtures + 'requests');
  await page.locator('#analyse').click();
  await expect(page.getByText(strings.catalogueError)).toBeVisible();

  // The real catalogue is accepted and the requests are analysed.
  await page.locator('#catalogue').setInputFiles(fixtures + 'invented-catalogue.csv');
  await page.locator('#analyse').click();
  await expect(page.locator('#t-files-sentence')).toHaveText(SUMMARY, { timeout: 60_000 });
  await expect(page.getByText(strings.readBeforeDownload)).toBeVisible();
  await expect(page.locator('#t-found-sentence')).toHaveText('Schemalyser found 20 tables and 64 columns in use, with 25 joins, 24 filters and 17 computed expressions.');
  await expect(page.locator('#unread dt')).toHaveCount(2);
  await expect(page.getByRole('heading', { name: 'Columns in use' })).toBeVisible();
  await expect(page.locator('.file table')).toHaveCount(6);
  await expect(page.getByText(strings.noHeaders)).toBeHidden();
  await expect(page.getByText(strings.folderCount(15))).toBeHidden();
  if (shots) await page.screenshot({ path: `${shots}/3-review.png`, fullPage: true });

  // The inventory shown contains the expected findings and none of the planted values.
  const pack = (await page.locator('#pack').innerText()).toLowerCase();
  expect(pack).toContain('datediff(minute, anaes_record.anaes_start_ts, anaes_record.anaes_stop_ts)');
  for (const value of planted) expect(pack).not.toContain(value.toLowerCase());
  await expect(page.locator('#index li')).toHaveCount(15);

  // The download holds the same seven files.
  const [download] = await Promise.all([page.waitForEvent('download'), page.locator('#download').click()]);
  expect(download.suggestedFilename()).toBe('schemalyser-inventory.zip');
  const listing = execFileSync('unzip', ['-Z1', await download.path()], { encoding: 'utf8' }).trim().split('\n');
  expect(listing.filter((name) => !name.startsWith('boundary/')).sort()).toEqual(
    ['comparisons.csv', 'coverage.txt', 'derivations.csv', 'elements.csv', 'filters.csv', 'joins.csv', 'requests.csv'].sort(),
  );
  // Beside the inventory, the boundary's own outputs. Without a conversion there is no checklist, and
  // the page says what to supply to see one.
  expect(listing).toEqual(expect.arrayContaining(['boundary/summary.md', 'boundary/provenance.json', 'boundary/check_script.sql']));
  expect(listing.some((name) => name.startsWith('boundary/targets/'))).toBe(false);
  await expect(page.getByText(strings.noChecklist)).toBeVisible();
  const zipped = execFileSync('unzip', ['-p', await download.path()], { encoding: 'utf8' }).toLowerCase();
  for (const value of planted) expect(zipped).not.toContain(value.toLowerCase());

  // While offline the page asked for nothing.
  expect(requestsWhileOffline).toEqual([]);

  // The network returns: the worker ends, the file list goes, and the inventory can still be downloaded.
  offline = false;
  await setOnline(page, context, browserName, true);
  await expect(page.getByText(strings.reconnected)).toBeVisible();
  await expect.poll(() => page.workers().length).toBe(0);
  await expect(page.locator('#index li')).toHaveCount(0);
  await expect(page.locator('#index-block')).toBeHidden();
  await expect(page.locator('#download')).toBeVisible();
  if (shots) await page.screenshot({ path: `${shots}/4-locked.png` });
  expect(violations).toEqual([]);
});

test('clearing while offline empties the page and leaves it ready for another run', async ({ page, context, browserName }) => {
  await page.goto('./');
  await expect(page.getByText(strings.loaded)).toBeVisible({ timeout: 90_000 });
  await setOnline(page, context, browserName, false);

  const run = async () => {
    await page.locator('#catalogue').setInputFiles(fixtures + 'invented-catalogue.csv');
    await page.locator('#folder').setInputFiles(fixtures + 'requests');
    await page.locator('#analyse').click();
    await expect(page.locator('#t-files-sentence')).toHaveText(SUMMARY, { timeout: 60_000 });
  };

  await run();
  await page.locator('#clear').click();
  await expect(page.getByText(strings.offline)).toBeVisible();
  await expect(page.locator('#results')).toBeHidden();
  await expect(page.locator('#pack')).toBeEmpty();
  await expect(page.locator('#index li')).toHaveCount(0);
  await expect(page.locator('#analyse')).toBeDisabled();
  expect(await page.locator('#catalogue').inputValue()).toBe('');

  await run();
});

test('the network returning before the inventory is finished leaves nothing to download', async ({ page, context, browserName }) => {
  await page.goto('./');
  await expect(page.getByText(strings.loaded)).toBeVisible({ timeout: 90_000 });
  await setOnline(page, context, browserName, false);
  await page.locator('#catalogue').setInputFiles(fixtures + 'invented-catalogue.csv');
  await page.locator('#folder').setInputFiles(fixtures + 'requests');
  // Hold the worker's messages back, so that the page is still analysing when the network returns.
  await page.evaluate(() => {
    window.addEventListener('message', () => {});
    const original = Worker.prototype.postMessage;
    Worker.prototype.postMessage = function (this: Worker, ...args: unknown[]) {
      this.onmessage = null;
      return (original as (...a: unknown[]) => void).apply(this, args);
    } as typeof Worker.prototype.postMessage;
  });
  await page.locator('#analyse').click();
  await setOnline(page, context, browserName, true);
  await expect(page.getByText(strings.reconnectedNoInventory)).toBeVisible();
  await expect(page.locator('#download')).toBeHidden();
  await expect.poll(() => page.workers().length).toBe(0);
});

test('an analysis that fails returns to the file choosers with a message', async ({ page, context, browserName }) => {
  await page.goto('./');
  await expect(page.getByText(strings.loaded)).toBeVisible({ timeout: 90_000 });
  await setOnline(page, context, browserName, false);
  await page.locator('#catalogue').setInputFiles(fixtures + 'invented-catalogue.csv');
  await page.locator('#folder').setInputFiles(fixtures + 'requests');
  // Break the core inside the worker, so that the analysis cannot finish.
  await page.workers()[0].evaluate(() => {
    (self as unknown as { onmessage: (e: unknown) => void }).onmessage = () =>
      self.postMessage({ type: 'analysis-failed' });
  });
  await page.locator('#analyse').click();
  await expect(page.getByText(strings.analysisFailed)).toBeVisible();
  await expect(page.getByText(strings.offline)).toBeVisible();
});

test('the network returning after a failed analysis ends the worker, which was given the requests', async ({ page, context, browserName }) => {
  await page.goto('./');
  await expect(page.getByText(strings.loaded)).toBeVisible({ timeout: 90_000 });
  await setOnline(page, context, browserName, false);
  await page.locator('#catalogue').setInputFiles(fixtures + 'invented-catalogue.csv');
  await page.locator('#folder').setInputFiles(fixtures + 'requests');
  // The worker receives the files and keeps them, then reports that the analysis failed.
  await page.workers()[0].evaluate(() => {
    (self as unknown as { onmessage: (e: MessageEvent) => void }).onmessage = (event) => {
      (self as unknown as { held: unknown }).held = event.data;
      self.postMessage({ type: 'analysis-failed' });
    };
  });
  await page.locator('#analyse').click();
  await expect(page.getByText(strings.analysisFailed)).toBeVisible();
  expect(page.workers()).toHaveLength(1);
  await setOnline(page, context, browserName, true);
  await expect.poll(() => page.workers().length).toBe(0);
  await expect(page.getByText(strings.reconnectedNoInventory)).toBeVisible();
  await expect(page.locator('#download')).toBeHidden();
});

test('a catalogue saved without column headers is accepted, and the page says so', async ({ page, context, browserName }) => {
  const headless = readFileSync(fixtures + 'invented-catalogue.csv', 'utf8').split('\n').slice(1).join('\n');
  await page.goto('./');
  await expect(page.getByText(strings.loaded)).toBeVisible({ timeout: 90_000 });
  await setOnline(page, context, browserName, false);
  await page.locator('#catalogue').setInputFiles({ name: 'results.csv', mimeType: 'text/csv', buffer: Buffer.from(headless) });
  await page.locator('#folder').setInputFiles(fixtures + 'requests');
  await expect(page.getByText(strings.folderCount(15))).toBeVisible();
  await page.locator('#analyse').click();
  await expect(page.locator('#t-files-sentence')).toHaveText(SUMMARY, { timeout: 60_000 });
  await expect(page.getByText(strings.noHeaders)).toBeVisible();
});

test('with check results the page confirms values, offers the check script and carries on into the sandbox', async ({ page, context, browserName }) => {
  await page.goto('./');
  await expect(page.getByText(strings.loaded)).toBeVisible({ timeout: 120_000 });
  await setOnline(page, context, browserName, false);

  // A file that is not check results is refused.
  await page.locator('#catalogue').setInputFiles(fixtures + 'invented-catalogue.csv');
  await page.locator('#rules').setInputFiles(fixtures + 'invented-site-rules.json');
  await page.locator('#folder').setInputFiles(fixtures + 'requests');
  await page.locator('#checks').setInputFiles(fixtures + 'planted-values.txt');
  await page.locator('#analyse').click();
  await expect(page.getByText(strings.checksError)).toBeVisible();

  await page.locator('#checks').setInputFiles(fixtures + 'invented-checks.csv');
  await page.locator('#analyse').click();
  await expect(page.locator('#t-files-sentence')).toHaveText(SUMMARY, { timeout: 60_000 });
  await expect(page.locator('#t-checks-used')).toHaveText(
    'Schemalyser has used your check results, which confirmed 20 values in 9 columns.',
  );
  // The inventory shown includes the check results it carries, and still none of the planted values.
  // With site rules and check results loaded, the inventory also carries roles.csv and checks.csv.
  // The eight files that have a title, and checked.csv, which lists the definition tables that the checks read.
  await expect(page.locator('.file table')).toHaveCount(9);
  const pack = (await page.locator('#pack').innerText()).toLowerCase();
  expect(pack).toContain('emergency_flag');
  for (const value of planted) expect(pack).not.toContain(value.toLowerCase());

  // The check script is offered, and it carries nothing from the requests.
  const [download] = await Promise.all([page.waitForEvent('download'), page.locator('#download-check-script').click()]);
  expect(download.suggestedFilename()).toBe('schemalyser-checks.sql');
  const script = readFileSync(await download.path(), 'utf8');
  expect(script).toContain('EXEC sys.sp_executesql');
  expect(script).toContain('[dbo].[THEATRE_CASE]');
  for (const value of planted) expect(script.toLowerCase()).not.toContain(value.toLowerCase());

  // The sandbox is built from the inventory just made, with no file chosen again.
  await page.locator('#build').click();
  await expect(page.locator('#t-built')).toContainText('Schemalyser has built 20 tables', { timeout: 60_000 });
  await expect(page.getByText(strings.usesChecks)).toBeVisible();
  await page.locator('#sql').fill("SELECT EMERGENCY_FLAG, COUNT(*) AS n FROM THEATRE_CASE GROUP BY EMERGENCY_FLAG");
  await page.locator('#run').click();
  await expect(page.locator('#query-table tbody tr')).toHaveCount(2);
  await expect(page.locator('#query-table')).toContainText('Y');

  // The requests chosen in the third step are run without being chosen again.
  await page.locator('#run-requests').click();
  await expect(page.locator('#t-requests-sentence')).toHaveText(
    'Of 15 requests, 9 ran and returned rows, 3 ran and returned no rows, and 3 could not be run in the sandbox.',
    { timeout: 60_000 },
  );
  if (shots) await page.screenshot({ path: `${shots}/7-combined.png`, fullPage: true });

  // The network returns: the worker and the sandbox end, and the inventory can still be downloaded.
  await setOnline(page, context, browserName, true);
  await expect(page.getByText(strings.reconnected)).toBeVisible();
  await expect.poll(() => page.workers().length).toBe(0);
  await expect(page.locator('#step-7 .body')).toBeHidden();
  await expect(page.locator('#download')).toBeVisible();
});

test('a page served without its content security policy refuses to start', async ({ page }) => {
  // The worker checks the policy for itself. Here the policy is stripped from the page on its way
  // to the browser, as a host that had been tampered with might do.
  await page.route('http://localhost:4173/', async (route) => {
    const response = await route.fetch();
    const body = (await response.text()).replace(/<meta\s+http-equiv="Content-Security-Policy"[\s\S]*?\/>/, '');
    await route.fulfill({ response, body });
  });
  await page.goto('./');
  await expect(page.getByText(strings.policyFailed)).toBeVisible({ timeout: 120_000 });
  await expect(page.locator('#step-3 .body')).toBeHidden();
});
