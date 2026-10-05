import { execFileSync } from 'node:child_process';
import { cpSync, mkdirSync, mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { answer, counts, expected, EXTRA_REQUEST, fixtures, makeState, type Expected } from './boundary';
import { setOnline } from './network';
import { strings } from '../src/strings';

// The checklist: for each target query, what the shadow database still needs, computed in the worker
// by the same code as the boundary command, and the loop of adding requests and analysing again.

test.skip(({ browserName }) => browserName === 'webkit', 'The page refuses to start in WebKit.');

const shots = process.env.SCREENSHOTS;
const planted = readFileSync(fixtures + 'planted-values.txt', 'utf8').split('\n').filter(Boolean);
const JOIN = 'relationship-AIRWAY_DEVICE.ANAES_KEY=ANAES_RECORD.ANAES_KEY';
const TARGET = 'airway_by_anaesthesia_type';

let world: Expected;
test.beforeAll(() => {
  world = expected();
});

async function checkTargets(page: Page, outputs: Record<string, string>, before?: Record<string, string>) {
  await expect(page.locator('#checklists section.target')).toHaveCount(world.targets.length);
  for (const name of world.targets) {
    const want = counts(outputs, name);
    const was = before ? counts(before, name).answered : undefined;
    const section = page.locator(`section.target[data-target="${name}"]`);
    await expect(section.locator('.tally')).toHaveText(strings.tally(want.answered, want.total, was));
    await expect(section.locator('li.item')).toHaveCount(want.rows.length);
    for (const status of ['answered', 'partly', 'open'] as const) {
      await expect(section.locator(`li.item[data-status="${status}"]`)).toHaveCount(want.statuses[status]);
    }
    // The readiness statement is the command's own.
    expect(await section.locator('.readiness pre').textContent()).toBe(outputs[`targets/${name}/readiness.txt`]);
  }
}

// Everything on the page, shown or hidden, apart from the list that maps each request's number to its
// file name, which the page shows by design and never writes into the results.
async function noPlantedValue(page: Page) {
  const html = (
    await page.evaluate(() => {
      const copy = document.documentElement.cloneNode(true) as HTMLElement;
      copy.querySelector('#index')?.remove();
      return copy.outerHTML;
    })
  ).toLowerCase();
  expect(html).toContain('airway_device.anaes_key');
  for (const value of planted) expect(html).not.toContain(value.toLowerCase());
  expect(await page.locator('#index li').count()).toBeGreaterThan(0);
}

test('the checklist matches the boundary command, and an added request ticks off the join it makes', async ({ page, context, browserName }) => {
  test.setTimeout(240_000);
  await page.goto('./');
  await expect(page.getByText(strings.loaded)).toBeVisible({ timeout: 120_000 });
  // While the computer is connected, nothing can be chosen or analysed.
  await expect(page.locator('#step-3 .body')).toBeHidden();
  await expect(page.locator('#checklist')).toBeHidden();
  await setOnline(page, context, browserName, false);

  await page.locator('#state-folder').setInputFiles(world.state);
  await expect(page.locator('#t-state-found')).toContainText('a catalogue, a conversion of');
  await page.locator('#folder').setInputFiles(fixtures + 'requests');
  const began = Date.now();
  await page.locator('#analyse').click();
  await expect(page.locator('#checklists section.target').first()).toBeVisible({ timeout: 120_000 });
  const seconds = (Date.now() - began) / 1000;
  console.log(`${browserName}: the first analysis with the checklists took ${seconds.toFixed(1)} s, ` +
    `of which the boundary took ${await page.locator('#checklist').getAttribute('data-seconds')} s in the worker.`);
  await checkTargets(page, world.before);

  // The open join is listed first among the items that existing SQL can settle, and says what to look for.
  const section = page.locator(`section.target[data-target="${TARGET}"]`);
  const join = section.locator(`ul[data-group="sql"] li[data-id="${JOIN}"]`);
  await expect(join).toHaveAttribute('data-status', 'open');
  await expect(join).toContainText('sample queries from the data team that join AIRWAY_DEVICE.ANAES_KEY to ANAES_RECORD.ANAES_KEY');
  await expect(join).toContainText(strings.blocking);
  // The items that need something else say who must act.
  await expect(section.locator('ul[data-group="other"] li').first()).toContainText(/The (clinical lead|analytics team|central OMOP team)/);
  // The answered items are folded away until asked for.
  await expect(section.locator('details.answered ul')).toBeHidden();
  await expect(page.locator('#t-changes')).toBeHidden();
  await noPlantedValue(page);
  if (shots) {
    await page.setViewportSize({ width: 1440, height: 1100 });
    await page.locator('#step-4').evaluate((node) => node.scrollIntoView({ block: 'start' }));
    await page.screenshot({ path: `${shots}/checklist-${browserName}.png` });
  }

  // One more request is added, and the requests are analysed again without starting over.
  await page.locator('#add-files').setInputFiles({ name: EXTRA_REQUEST.name, mimeType: 'text/plain', buffer: Buffer.from(EXTRA_REQUEST.text) });
  await expect(page.locator('#t-held')).toHaveText(strings.held(16, 1));
  await page.locator('#reanalyse').click();
  const newly = world.targets.reduce((sum, name) => {
    const before = new Map(counts(world.before, name).rows.map((row) => [row.question_id, row.status]));
    return sum + counts(world.after, name).rows.filter((row) => row.status === 'answered' && before.get(row.question_id) !== 'answered' && before.has(row.question_id)).length;
  }, 0);
  expect(newly).toBeGreaterThan(0);
  await expect(page.locator('#t-changes')).toHaveText(strings.changes(newly, world.targets.length), { timeout: 120_000 });
  await checkTargets(page, world.after, world.before);
  const ticked = section.locator(`ul[data-group="new"] li[data-id="${JOIN}"]`);
  await expect(ticked).toHaveAttribute('data-status', 'answered');
  await expect(ticked).toHaveAttribute('data-new', 'true');
  await expect(section.locator(`ul[data-group="sql"] li[data-id="${JOIN}"]`)).toHaveCount(0);
  const before = counts(world.before, TARGET).answered;
  const after = counts(world.after, TARGET).answered;
  expect(after).toBeGreaterThan(before);
  await expect(section.locator('.tally')).toHaveText(strings.tally(after, counts(world.after, TARGET).total, before));
  await expect(page.locator('#t-held')).toHaveText(strings.held(16, 0));
  await noPlantedValue(page);
  if (shots) {
    await section.evaluate((node) => node.scrollIntoView({ block: 'start' }));
    await page.screenshot({ path: `${shots}/checklist-after-${browserName}.png` });
  }

  // The download holds the inventory and, under boundary/, the command's own outputs.
  const [download] = await Promise.all([page.waitForEvent('download'), page.locator('#download').click()]);
  const path = await download.path();
  const listing = execFileSync('unzip', ['-Z1', path], { encoding: 'utf8' }).trim().split('\n');
  expect(listing).toContain('elements.csv');
  const inBoundary = listing.filter((name) => name.startsWith('boundary/')).map((name) => name.slice('boundary/'.length));
  expect(inBoundary.sort()).toEqual(Object.keys(world.after).sort());
  for (const name of ['questions.csv', 'questions-summary.txt', 'check_script.sql',
    ...world.targets.flatMap((t) => [`targets/${t}/checklist.csv`, `targets/${t}/readiness.txt`])]) {
    expect(execFileSync('unzip', ['-p', path, `boundary/${name}`], { encoding: 'utf8' }), name).toBe(world.after[name]);
  }
  const summary = execFileSync('unzip', ['-p', path, 'boundary/summary.md'], { encoding: 'utf8' });
  const withoutFingerprint = (text: string) => text.replace(/^The Schemalyser code that wrote them has the fingerprint .*$/m, '');
  expect(withoutFingerprint(summary)).toBe(withoutFingerprint(world.after['summary.md']));
  const zipped = execFileSync('unzip', ['-p', path], { encoding: 'utf8' }).toLowerCase();
  for (const value of planted) expect(zipped).not.toContain(value.toLowerCase());

  // The check script now includes the spans and fanout checks, as the command's does.
  const [script] = await Promise.all([page.waitForEvent('download'), page.locator('#download-check-script').click()]);
  const text = readFileSync(await script.path(), 'utf8');
  expect(text).toBe(world.after['check_script.sql']);
  expect(text).toContain("''spans''");
  expect(text).toContain("''fanout''");

  // The network returns: the worker ends, the requests go, and the checklist stays readable.
  await setOnline(page, context, browserName, true);
  await expect(page.getByText(strings.reconnected)).toBeVisible();
  await expect.poll(() => page.workers().length).toBe(0);
  await expect(page.locator('#b-add')).toBeHidden();
  await expect(page.locator('#restart')).toBeVisible();
  await expect(page.locator('#checklists section.target')).toHaveCount(world.targets.length);
});

test('a new analysis after the network returns shows what the new files answer', async ({ page, context, browserName }) => {
  test.setTimeout(300_000);
  await page.goto('./');
  await expect(page.getByText(strings.loaded)).toBeVisible({ timeout: 120_000 });
  await setOnline(page, context, browserName, false);
  await page.locator('#state-folder').setInputFiles(world.state);
  await page.locator('#folder').setInputFiles(fixtures + 'requests');
  await page.locator('#analyse').click();
  await expect(page.locator('#checklists section.target')).toHaveCount(world.targets.length, { timeout: 120_000 });

  await setOnline(page, context, browserName, true);
  await page.locator('#restart').click();
  await expect(page.getByText(strings.loaded)).toBeVisible({ timeout: 120_000 });
  await expect(page.locator('#checklist')).toBeHidden();
  await setOnline(page, context, browserName, false);
  await expect(page.getByText(strings.keptForComparison)).toBeVisible();
  // The files were let go of at the lock, so they are chosen again, with the extra request.
  await page.locator('#state-folder').setInputFiles(world.state);
  await page.locator('#folder').setInputFiles(fixtures + 'requests');
  await page.locator('#analyse').click();
  await expect(page.locator('#checklists section.target')).toHaveCount(world.targets.length, { timeout: 120_000 });
  await expect(page.locator('#t-changes')).toHaveText(strings.changes(0, world.targets.length));
  await page.locator('#add-files').setInputFiles({ name: EXTRA_REQUEST.name, mimeType: 'text/plain', buffer: Buffer.from(EXTRA_REQUEST.text) });
  await page.locator('#reanalyse').click();
  await expect(page.locator(`section.target[data-target="${TARGET}"] ul[data-group="new"] li[data-id="${JOIN}"]`)).toBeVisible({ timeout: 120_000 });
});

test('each open item carries its query, and a pasted result ticks it and brings on the next query', async ({ page, context, browserName }) => {
  test.setTimeout(300_000);
  if (browserName === 'chromium') await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.goto('./');
  await expect(page.getByText(strings.loaded)).toBeVisible({ timeout: 120_000 });
  await setOnline(page, context, browserName, false);
  await page.locator('#state-folder').setInputFiles(world.state);
  await page.locator('#folder').setInputFiles(fixtures + 'requests');
  await page.locator('#analyse').click();
  await expect(page.locator('#checklists section.target')).toHaveCount(world.targets.length, { timeout: 120_000 });
  await expect(page.locator('#b-paste')).toBeVisible();

  // The table sizes query comes first, in its own section, for the one table whose size the state does not give.
  const section = page.locator(`section.target[data-target="${TARGET}"]`);
  const sizes = section.locator('.sizes pre[data-query="sizes"]');
  await expect(section.locator('.sizes h4', { hasText: strings.sizesHeading })).toHaveCount(1);
  await expect(sizes).toContainText("(N'dbo', N'AIRWAY_DEVICE')");
  await expect(sizes).toContainText('sys.partitions');
  // The query that can run now is shown once, under the first item that needs it, with its reason.
  const KIND = 'values:ANAES_RECORD.ANAES_KIND_CAT';
  const ready = section.locator(`pre[data-query="${KIND}"]`);
  await expect(ready).toHaveCount(1);
  await expect(ready).toContainText('WITH (NOLOCK)');
  await expect(ready).toContainText('SELECT TOP (201)');
  const readyItem = section.locator('li.item', { has: page.locator(`pre[data-query="${KIND}"]`) });
  await expect(readyItem).toHaveAttribute('data-status', 'partly');
  await expect(readyItem.locator('.query-state')).toHaveText(strings.queryStates.ready);
  await expect(readyItem.locator('.query-reason')).toContainText('The query lists each code that ANAES_RECORD.ANAES_KIND_CAT holds');
  const others = section.locator('li.item', { has: page.locator('.query-earlier') });
  await expect(others.first()).toContainText(strings.queryShownEarlier);
  // The items on AIRWAY_DEVICE wait for the table sizes, and show no query yet.
  const waiting = section.locator('li.item', { has: page.locator('.query-state[data-state="waiting"]') });
  await expect(waiting.first()).toContainText('AIRWAY_DEVICE.DEVICE_KIND_KEY');
  await expect(section.locator('pre[data-query="values:AIRWAY_DEVICE.DEVICE_KIND_KEY"]')).toHaveCount(0);

  // The copy button copies the query exactly as shown.
  const copy = section.locator(`button[data-query="${KIND}"]`);
  await copy.click();
  await expect(copy).toHaveAttribute('data-copied', 'true');
  const queryText = (await ready.textContent()) ?? '';
  if (browserName === 'chromium') expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(queryText);
  const sizesText = (await sizes.textContent()) ?? '';
  // Every item with a query ready to run needs this one query.
  const needing = await section.locator('li.item', { has: page.locator('.query-state[data-state="ready"]') }).count();
  expect(needing).toBeGreaterThan(1);

  // Text that is no result is refused, and the checklist stays as it was.
  await page.locator('#paste').fill('These are not the results of any query.');
  await page.locator('#read-paste').click();
  await expect(page.locator('#t-paste-result')).toHaveText(strings.pasteUnreadable);

  // The results of both queries, as the results grid copies them, are pasted together.
  const pasted = answer([sizesText, queryText]);
  const lines = pasted.trim().split('\n').length - 1;
  await page.locator('#paste').fill(pasted);
  await page.locator('#read-paste').click();
  await expect(page.locator('#t-paste-result')).toHaveText(strings.pasted(lines, lines), { timeout: 120_000 });
  await expect(page.locator('#t-changes')).toContainText('Schemalyser has worked out the checklist again with the pasted results');
  await expect(page.locator('#paste')).toHaveValue('');
  // The items that the query answers have ticked, and the query that waited for the sizes has appeared.
  const ticked = section.locator('ul[data-group="new"] li.item[data-status="answered"]');
  await expect(ticked.first()).toBeVisible();
  expect(await ticked.count()).toBe(needing);
  await expect(section.locator(`pre[data-query="${KIND}"]`)).toHaveCount(0);
  await expect(section.locator('pre[data-query="sizes"]')).toHaveCount(0);
  const next = section.locator('pre[data-query="values:AIRWAY_DEVICE.DEVICE_KIND_KEY"]');
  await expect(next).toHaveCount(1);
  await expect(next).toContainText('FROM [dbo].[AIRWAY_DEVICE] WITH (NOLOCK)');
  await noPlantedValue(page);

  // The merged check results are saved as checks.csv for the state folder.
  const [saved] = await Promise.all([page.waitForEvent('download'), page.locator('#save-checks').click()]);
  expect(saved.suggestedFilename()).toBe('checks.csv');
  const checksCsv = readFileSync(await saved.path(), 'utf8');
  expect(checksCsv.split('\n')[0]).toBe('check_kind,table_name,column_name,value,label,row_count,distinct_count,null_count,is_unique');
  expect(checksCsv).toContain('values,ANAES_RECORD,ANAES_KIND_CAT,');
  expect(checksCsv).toMatch(/\nrows,AIRWAY_DEVICE,,,,\d+,,,\n/);
  // The check results that the state already held are kept.
  expect(checksCsv).toContain('values,OBS_READING,OBS_TYPE_KEY,');
  // The download of the boundary's files follows the merged results.
  const [download] = await Promise.all([page.waitForEvent('download'), page.locator('#download').click()]);
  const queriesFile = execFileSync('unzip', ['-p', await download.path(), `boundary/targets/${TARGET}/queries.sql`], { encoding: 'utf8' });
  expect(queriesFile).toContain('FROM [dbo].[AIRWAY_DEVICE] WITH (NOLOCK)');
  if (shots) {
    await section.evaluate((node) => node.scrollIntoView({ block: 'start' }));
    await page.screenshot({ path: `${shots}/checklist-pasted-${browserName}.png` });
  }
});

test('the core profile queries are shown for the central OMOP team, and a pasted result ticks the core items', async ({ page, context, browserName }) => {
  test.setTimeout(300_000);
  const state = makeState(join(mkdtempSync(join(tmpdir(), 'schemalyser-profile-')), 'state'), { profile: false, sourcePrefix: 'clarity_shadow.dbo.' });
  await page.goto('./');
  await expect(page.getByText(strings.loaded)).toBeVisible({ timeout: 120_000 });
  await setOnline(page, context, browserName, false);
  await page.locator('#state-folder').setInputFiles(state);
  await page.locator('#folder').setInputFiles(fixtures + 'requests');
  await page.locator('#analyse').click();
  await expect(page.locator('#checklists section.target')).toHaveCount(world.targets.length, { timeout: 120_000 });

  // The query of the core's own records comes first, in a section of its own for the central OMOP team.
  const section = page.locator('section.target[data-target="neonatal_low_mean_pressure"]');
  const profile = section.locator('.profile-queries');
  await expect(profile.locator('h4')).toHaveText(strings.profileHeading);
  const tierOne = profile.locator('pre[data-query="profile:tier-one"]');
  await expect(tierOne).toContainText('sys.objects');
  await expect(profile.locator('pre')).toHaveCount(1);
  const core = section.locator('li.item[data-id^="core-"]');
  await expect(core.first()).toContainText(strings.queryInProfile);
  await expect(page.locator('#b-profile-paste')).toBeVisible();

  // The central team's result, as the results grid copies it: the rows of the whole script's result on the
  // same database that the query asks for.
  const sql = (await tierOne.textContent()) ?? '';
  const tables = new Set([...sql.matchAll(/\('([a-z_]+)', '[a-z_]*'\)/g)].map((m) => m[1]));
  const whole = readFileSync(fixtures + 'profile/invented-core-profile.csv', 'utf8').trim().split('\n').map((line) => line.split(','));
  const rows = whole.filter((r) => (r[0] === 'PROFILE' && r[1] === '1') || (r[0] === 'CDM_TABLE' && tables.has(r[1])));
  const pasted = ['ITEM_CATEGORY\tVALUE_01\tVALUE_02\tVALUE_03\tVALUE_04\tVALUE_05', ...rows.map((r) => r.slice(0, 6).join('\t'))].join('\n');
  await page.locator('#profile-paste').fill(pasted);
  await page.locator('#read-profile-paste').click();
  await expect(page.locator('#t-profile-paste-result')).toHaveText(strings.pasted(rows.length, rows.length), { timeout: 120_000 });
  await expect(section.locator('ul[data-group="new"] li.item[data-id^="core-"][data-status="answered"]').first()).toBeVisible();
  // The queries that waited for the core's sizes appear in their place.
  await expect(profile.locator('pre[data-query="profile:tier-one"]')).toHaveCount(0);
  await expect(profile.locator('pre[data-query^="profile:match:"]').first()).toContainText('[clarity_shadow].[dbo].');
  await noPlantedValue(page);

  // The merged core profile is saved as core-profile.csv for the state folder.
  const [saved] = await Promise.all([page.waitForEvent('download'), page.locator('#save-profile').click()]);
  expect(saved.suggestedFilename()).toBe('core-profile.csv');
  const profileCsv = readFileSync(await saved.path(), 'utf8');
  expect(profileCsv.split('\n')[0]).toBe('ITEM_CATEGORY,VALUE_01,VALUE_02,VALUE_03,VALUE_04,VALUE_05');
  expect(profileCsv).toContain('CDM_TABLE,person,Y,');
});

test('without a catalogue, one first query of names and sizes starts the project, and a ready question offers its audit query', async ({ page, context, browserName }) => {
  test.setTimeout(300_000);
  const state = makeState(join(mkdtempSync(join(tmpdir(), 'schemalyser-first-')), 'state'), { catalogue: false, checks: false });
  await page.goto('./');
  await expect(page.getByText(strings.loaded)).toBeVisible({ timeout: 120_000 });
  await setOnline(page, context, browserName, false);
  await page.locator('#state-folder').setInputFiles(state);
  await page.locator('#folder').setInputFiles(fixtures + 'requests');
  await expect(page.locator('#analyse')).toBeDisabled();
  await expect(page.locator('#b-first')).toBeVisible();
  await page.locator('#first-write').click();
  const query = page.locator('#first-query');
  await expect(query).toContainText('INFORMATION_SCHEMA.COLUMNS');
  const sql = (await query.textContent()) ?? '';
  // The names come from the requests and the conversion, and nothing else of them reaches the query.
  const names = [...sql.matchAll(/N'([A-Z_0-9]+)'/g)].map((m) => m[1]);
  expect(names).toContain('THEATRE_CASE');
  expect(names).toContain('OBS_READING');
  for (const value of planted) expect(sql.toLowerCase()).not.toContain(value.toLowerCase());

  // The analyst's result, as the grid copies it: the invented catalogue's rows for those names, with sizes.
  const catalogueRows = readFileSync(fixtures + 'invented-catalogue.csv', 'utf8').trim().split('\n').slice(1).map((line) => line.split(','));
  const lines = ['TABLE_SCHEMA\tTABLE_NAME\tCOLUMN_NAME\tORDINAL_POSITION\tDATA_TYPE\tCHARACTER_MAXIMUM_LENGTH\tNUMERIC_PRECISION\tNUMERIC_SCALE\tIS_NULLABLE\tTABLE_ROWS'];
  for (const row of catalogueRows.filter((r) => names.includes(r[1]))) lines.push([...row.map((c) => c || 'NULL'), '600'].join('\t'));
  await page.locator('#first-paste').fill(lines.join('\n'));
  await page.locator('#first-read').click();
  await expect(page.locator('#t-first-result')).toContainText('You can now analyse the requests.');
  await expect(page.locator('#analyse')).toBeEnabled();
  await page.locator('#analyse').click();
  await expect(page.locator('#checklists section.target')).toHaveCount(world.targets.length, { timeout: 120_000 });
  // The sizes are known, so no table sizes query is asked for, and the queries are ready at once.
  const section = page.locator('section.target[data-target="infant_low_pressure"]');
  await expect(section.locator('.stage-verdict')).toContainText('from the source database');
  await expect(section.locator('pre[data-query="sizes"]')).toHaveCount(0);
  // The core and the timing are folded away under the heading for the later OMOP release.
  await expect(section.locator('details.release summary')).toHaveText(strings.releaseHeading);
  await expect(section.locator('details.release li.item[data-id^="core-"]').first()).toBeHidden();
  // The catalogue and the sizes are saved together for the next project.
  const [saved] = await Promise.all([page.waitForEvent('download'), page.locator('#save-state').click()]);
  expect(saved.suggestedFilename()).toBe('schemalyser-state.zip');
  const listing = execFileSync('unzip', ['-Z1', await saved.path()], { encoding: 'utf8' }).trim().split('\n').sort();
  expect(listing).toEqual(['catalogue.csv', 'checks.csv', 'core-profile.csv', 'sql_evidence.json']);
  const savedCatalogue = execFileSync('unzip', ['-p', await saved.path(), 'catalogue.csv'], { encoding: 'utf8' });
  expect(savedCatalogue).toContain('THEATRE_CASE');
  // Nothing that the page writes holds the first query.
  const [download] = await Promise.all([page.waitForEvent('download'), page.locator('#download').click()]);
  const zipped = execFileSync('unzip', ['-p', await download.path()], { encoding: 'utf8' });
  expect(zipped).not.toContain('INFORMATION_SCHEMA.COLUMNS AS c');
});

test('a question that is ready to be answered from the source database offers the audit query', async ({ page, context, browserName }) => {
  test.setTimeout(300_000);
  if (browserName === 'chromium') await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.goto('./');
  await expect(page.getByText(strings.loaded)).toBeVisible({ timeout: 120_000 });
  await setOnline(page, context, browserName, false);
  await page.locator('#state-folder').setInputFiles(world.state);
  await page.locator('#folder').setInputFiles(fixtures + 'requests');
  await page.locator('#analyse').click();
  await expect(page.locator('#checklists section.target')).toHaveCount(world.targets.length, { timeout: 120_000 });
  const ready = page.locator('section.target[data-target="infant_low_pressure"]');
  const audit = ready.locator('.audit pre[data-query="audit"]');
  // The query is restructured to start from the cohort, so it is offered as one that may be run.
  await expect(ready.locator('.audit h4')).toHaveText(strings.auditHeading);
  await expect(ready.locator('.audit')).toHaveAttribute('data-restructured', 'true');
  await expect(ready.locator('.audit')).not.toContainText('not suitable to run on a large database');
  await expect(audit).toContainText('This query starts from the cohort of the question');
  await expect(audit).not.toContainText('DENSE_RANK');
  // The specification comes before it, with the check of a hand-written query.
  await expect(ready.locator('.specification pre')).toContainText('6. Acceptance cases');
  await expect(ready.locator('.check')).toContainText('--check-query');
  await expect(audit).toContainText('WITH (NOLOCK)');
  await expect(ready.locator('.audit')).toContainText(strings.auditCounts);
  await expect(ready.locator('.audit')).toContainText('OBS_READING, which holds about');
  const [saved] = await Promise.all([page.waitForEvent('download'), ready.locator('.audit button', { hasText: strings.auditSave }).click()]);
  expect(readFileSync(await saved.path(), 'utf8')).toBe((await audit.textContent()) ?? '');
});

test('a colleague answers a question from knowledge, and the item ticks without a query', async ({ page, context, browserName }) => {
  test.setTimeout(300_000);
  if (browserName === 'chromium') await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.goto('./');
  await expect(page.getByText(strings.loaded)).toBeVisible({ timeout: 120_000 });
  await setOnline(page, context, browserName, false);
  await page.locator('#state-folder').setInputFiles(world.state);
  await page.locator('#folder').setInputFiles(fixtures + 'requests');
  await page.locator('#analyse').click();
  await expect(page.locator('#checklists section.target')).toHaveCount(world.targets.length, { timeout: 120_000 });
  const section = page.locator('section.target[data-target="neonatal_low_mean_pressure"]');
  // The questions come first, as one list to send.
  const questions = section.locator('.questions pre');
  await expect(questions).toContainText('Please confirm whether OBS_SHEET.VISIT_KEY joins to VISIT.VISIT_KEY');
  const copy = section.locator('.questions button', { hasText: strings.questionsCopy });
  await copy.click();
  await expect(copy).toHaveAttribute('data-copied', 'true');
  if (browserName === 'chromium') expect(await page.evaluate(() => navigator.clipboard.readText())).toBe((await questions.textContent()) ?? '');
  // The item puts the question first, and the query, where there is one, second.
  const item = section.locator('li.item[data-id="relationship-OBS_SHEET.VISIT_KEY=VISIT.VISIT_KEY"]');
  await expect(item.locator('.ask .ask-text')).toContainText('Please confirm whether');
  await section.locator('input.fact-who').fill('A colleague');
  await item.locator('button.fact-yes').click();
  await expect(page.locator('#t-paste-result')).toHaveText(strings.factRecorded, { timeout: 120_000 });
  const ticked = section.locator('li.item[data-id="relationship-OBS_SHEET.VISIT_KEY=VISIT.VISIT_KEY"]');
  await expect(ticked.first()).toHaveAttribute('data-status', 'answered');
  await expect(ticked.first()).toContainText('A person confirmed on');
  await expect(section.locator('.questions pre')).not.toContainText('OBS_SHEET.VISIT_KEY joins to VISIT.VISIT_KEY');
  // The codes for a concept are entered as a person, and become the site's mapping rows.
  const codes = section.locator('li.item[data-id="codes-measurement.measurement_concept_id-21490852"]');
  if (await codes.locator('.ask').count()) {
    await codes.locator('input.fact-codes').fill('52');
    await codes.locator('button.fact-save-codes').click();
    await expect(section.locator('li.item[data-id="codes-measurement.measurement_concept_id-21490852"]').first()).toHaveAttribute('data-status', 'answered', { timeout: 120_000 });
  }
  // The saved state holds the facts and the site's mapping rows, and nothing else that the page writes names who answered.
  const [saved] = await Promise.all([page.waitForEvent('download'), page.locator('#save-state').click()]);
  const listing = execFileSync('unzip', ['-Z1', await saved.path()], { encoding: 'utf8' });
  expect(listing).toContain('facts.json');
  expect(execFileSync('unzip', ['-p', await saved.path(), 'facts.json'], { encoding: 'utf8' })).toContain('A colleague');
  const [download] = await Promise.all([page.waitForEvent('download'), page.locator('#download').click()]);
  expect(execFileSync('unzip', ['-p', await download.path()], { encoding: 'utf8' })).not.toContain('A colleague');
});

// Each target's items, as identifier and status, in the order of their identifiers.
async function itemsByTarget(page: Page) {
  return page.evaluate(() =>
    Object.fromEntries(
      [...document.querySelectorAll<HTMLElement>('#checklists section.target')].map((section) => [
        section.dataset.target,
        [...new Set([...section.querySelectorAll<HTMLElement>('li.item')].map((li) => `${li.dataset.id} ${li.dataset.status}`))].sort(),
      ]),
    ),
  );
}

test('a state saved part of the way loads on a fresh page without the request files, and each checklist is the same item for item', async ({ page, context, browserName }) => {
  test.setTimeout(420_000);
  const base = mkdtempSync(join(tmpdir(), 'schemalyser-saved-'));
  const state = makeState(join(base, 'state'), { catalogue: false, checks: false });
  await page.goto('./');
  await expect(page.getByText(strings.loaded)).toBeVisible({ timeout: 120_000 });
  await setOnline(page, context, browserName, false);
  await page.locator('#state-folder').setInputFiles(state);
  await page.locator('#folder').setInputFiles(fixtures + 'requests');

  // The first query's result stands in for the catalogue and gives the sizes.
  await page.locator('#first-write').click();
  await expect(page.locator('#first-query')).toContainText('INFORMATION_SCHEMA.COLUMNS');
  const sql = (await page.locator('#first-query').textContent()) ?? '';
  const names = [...sql.matchAll(/N'([A-Z_0-9]+)'/g)].map((m) => m[1]);
  const catalogueRows = readFileSync(fixtures + 'invented-catalogue.csv', 'utf8').trim().split('\n').slice(1).map((line) => line.split(','));
  const lines = ['TABLE_SCHEMA\tTABLE_NAME\tCOLUMN_NAME\tORDINAL_POSITION\tDATA_TYPE\tCHARACTER_MAXIMUM_LENGTH\tNUMERIC_PRECISION\tNUMERIC_SCALE\tIS_NULLABLE\tTABLE_ROWS'];
  for (const row of catalogueRows.filter((r) => names.includes(r[1]))) lines.push([...row.map((c) => c || 'NULL'), '600'].join('\t'));
  await page.locator('#first-paste').fill(lines.join('\n'));
  await page.locator('#first-read').click();
  await expect(page.locator('#t-first-result')).toContainText('You can now analyse the requests.');
  await page.locator('#analyse').click();
  await expect(page.locator('#checklists section.target')).toHaveCount(world.targets.length, { timeout: 120_000 });
  const section = page.locator('section.target[data-target="neonatal_low_mean_pressure"]');

  // A pasted check result.
  const query = section.locator('pre[data-query^="values:"]').first();
  const pasted = answer([(await query.textContent()) ?? '']);
  await page.locator('#paste').fill(pasted);
  await page.locator('#read-paste').click();
  await expect(page.locator('#t-paste-result')).toContainText('Schemalyser has added the', { timeout: 120_000 });

  // A fact answered, and a code entered.
  const JOINED = 'relationship-OBS_SHEET.VISIT_KEY=VISIT.VISIT_KEY';
  await section.locator('input.fact-who').fill('A colleague');
  await section.locator(`li.item[data-id="${JOINED}"] button.fact-yes`).click();
  await expect(page.locator('#t-paste-result')).toHaveText(strings.factRecorded, { timeout: 120_000 });
  await expect(section.locator(`li.item[data-id="${JOINED}"]`).first()).toHaveAttribute('data-status', 'answered');
  // The first item, in any target, that asks for the local codes of a concept.
  const asking = page.locator('#checklists li.item', { has: page.locator('input.fact-codes') }).first();
  const CODES = (await asking.getAttribute('data-id')) ?? '';
  expect(CODES).toMatch(/^codes-/);
  const where = page.locator('#checklists section.target', { has: page.locator(`li.item[data-id="${CODES}"] input.fact-codes`) }).first();
  await where.locator(`li.item[data-id="${CODES}"] input.fact-codes`).first().fill('52');
  await where.locator(`li.item[data-id="${CODES}"] button.fact-save-codes`).first().click();
  await expect(page.locator(`#checklists li.item[data-id="${CODES}"]`).first()).toContainText('A person gave on', { timeout: 120_000 });
  const before = await itemsByTarget(page);

  // The state is saved with the page's button.
  const [saved] = await Promise.all([page.waitForEvent('download'), page.locator('#save-state').click()]);
  const listing = execFileSync('unzip', ['-Z1', await saved.path()], { encoding: 'utf8' }).trim().split('\n').sort();
  expect(listing).toEqual(['catalogue.csv', 'checks.csv', 'conversion/site_mappings.csv', 'core-profile.csv', 'facts.json', 'sql_evidence.json']);

  // A state folder made from the zip, the conversion, the targets and the site rules, with no request files.
  const loaded = join(base, 'loaded');
  mkdirSync(loaded);
  cpSync(fixtures + 'conversion', join(loaded, 'conversion'), { recursive: true });
  cpSync(fixtures + 'targets', join(loaded, 'targets'), { recursive: true });
  cpSync(fixtures + 'invented-site-rules.json', join(loaded, 'site-rules.json'));
  execFileSync('unzip', ['-o', '-q', await saved.path(), '-d', loaded]);

  await setOnline(page, context, browserName, true);
  const fresh = await context.newPage();
  await fresh.goto('./');
  await expect(fresh.getByText(strings.loaded)).toBeVisible({ timeout: 120_000 });
  await setOnline(fresh, context, browserName, false);
  await fresh.locator('#state-folder').setInputFiles(loaded);
  await expect(fresh.locator('#analyse')).toBeEnabled();
  await fresh.locator('#analyse').click();
  await expect(fresh.locator('#checklists section.target')).toHaveCount(world.targets.length, { timeout: 120_000 });
  expect(await itemsByTarget(fresh)).toEqual(before);
  // The items that the team's SQL settled say when it was seen.
  await expect(fresh.locator('section.target[data-target="neonatal_low_mean_pressure"]')).toContainText("of the team's queries on");
});

test('the invented example loads before any file is chosen, is marked as invented, and gives way cleanly to real files', async ({ page, context, browserName }) => {
  test.setTimeout(300_000);
  await page.goto('./');
  await expect(page.getByText(strings.loaded)).toBeVisible({ timeout: 120_000 });
  // The page says how to take only this tab offline, and why two outside addresses were refused.
  await expect(page.locator('#t-offline-how li')).toHaveCount(strings.offlineHow.length);
  await expect(page.locator('#t-policy-held')).toHaveText(strings.policyHeld);
  await page.locator('#example-load').click();
  await expect(page.locator('#t-example-status')).toContainText('Schemalyser has loaded the invented example');
  await setOnline(page, context, browserName, false);
  await expect(page.locator('#t-example-chosen')).toHaveText(strings.exampleChosen);
  await expect(page.locator('#analyse')).toBeEnabled();
  await page.locator('#analyse').click();
  await expect(page.locator('#checklists section.target')).toHaveCount(world.targets.length, { timeout: 120_000 });
  await expect(page.locator('#t-example-banner')).toHaveText(strings.exampleBanner);
  // The example's checklist is the command's own for the invented world.
  await checkTargets(page, world.before);
  // A file of one's own discards the example and everything worked out from it.
  await page.locator('#catalogue').setInputFiles(fixtures + 'invented-catalogue.csv');
  await expect(page.locator('#t-example-banner')).toBeHidden();
  await expect(page.locator('#checklists section.target')).toHaveCount(0);
  await expect(page.locator('#t-example-chosen')).toBeHidden();
});

test('without a conversion or target queries the page says what to supply to see a checklist', async ({ page, context, browserName }) => {
  await page.goto('./');
  await expect(page.getByText(strings.loaded)).toBeVisible({ timeout: 120_000 });
  await setOnline(page, context, browserName, false);
  await page.locator('#catalogue').setInputFiles(fixtures + 'invented-catalogue.csv');
  await page.locator('#folder').setInputFiles(fixtures + 'requests');
  await page.locator('#analyse').click();
  await expect(page.getByText(strings.noChecklist)).toBeVisible({ timeout: 120_000 });
  await expect(page.locator('#checklists section')).toHaveCount(0);
  await expect(page.locator('#t-checklist-intro')).toBeHidden();
  await expect(page.locator('#t-files-sentence')).toHaveText('Schemalyser has read 15 files. It was not able to read 2 of them in full, because each holds a part that Schemalyser could not parse, SQL that is built as text when it runs, a call to a stored procedure, a statement of a kind that Schemalyser does not analyse, or a query whose columns Schemalyser could not match to their tables.');
});
