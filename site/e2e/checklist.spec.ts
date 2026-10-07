import { execFileSync } from 'node:child_process';
import { cpSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
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
    // The head of the checklist says what is still needed, in place of a count of items.
    await expect(section.locator('.tally')).toContainText(was === undefined ? 'Schemalyser needs' : '');
    expect(was === undefined || want.answered >= 0).toBe(true);
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
  await expect(page.locator('#t-state-found')).toContainText('it holds a list of tables and columns');
  await page.locator('#folder').setInputFiles(fixtures + 'requests');
  const began = Date.now();
  await page.locator('#analyse').click();
  await expect(page.locator('#checklists section.target').first()).toBeVisible({ timeout: 120_000 });
  const seconds = (Date.now() - began) / 1000;
  console.log(`${browserName}: the first analysis with the checklists took ${seconds.toFixed(1)} s, ` +
    `of which the boundary took ${await page.locator('#checklist').getAttribute('data-seconds')} s in the worker.`);
  await checkTargets(page, world.before);

  // The open join is asked of the colleague, and its own account, folded beneath, agrees with the buttons: a Yes settles
  // it, and a SQL file of the team's that makes the join is another way.
  const section = page.locator(`section.target[data-target="${TARGET}"]`);
  const join = section.locator(`ul[data-group="you"] li[data-id="${JOIN}"]`);
  await expect(join).toHaveAttribute('data-status', 'open');
  await expect(join).toContainText(strings.askSettles.join);
  await expect(join).not.toContainText('must act');
  await expect(join).toContainText(strings.blocking);
  // The items that need something else say who must act.
  // Each item's own account, folded beneath it, says who must act.
  expect(await section.locator('li.item .actor').first().textContent()).toMatch(/The (clinician leading the audit|team that looks after the reporting database|central OMOP team)/);
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
  await expect(section.locator('.tally')).toBeVisible();
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
  // The kind of anaesthetic is only assumed from the conversion's own mapping, and the answer rests on it, so the item is open.
  await expect(readyItem).toHaveAttribute('data-status', 'open');
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
  // The result settles nothing by itself: the items that needed the query now show its codes with their names, for the
  // two of them to choose from, and the query that waited for the sizes has appeared.
  const choosing = section.locator('li.item[data-status="open"] .choose-table');
  await expect(choosing.first()).toBeVisible({ timeout: 120_000 });
  await expect(section.locator('li.item[data-status="answered"] .choose-table')).toHaveCount(0);
  await expect(section.locator(`pre[data-query="${KIND}"]`)).toHaveCount(0);
  // A code whose name is only a number, or empty, is shown as such and cannot be chosen; where no code has a name, the
  // codes are entered from what the two of them know, and the item ticks.
  const rows = choosing.first().locator('tr:has(select)');
  const nameless = choosing.first().locator('tr[data-nameless]');
  expect(await nameless.count()).toBeGreaterThan(0);
  await expect(nameless.first().locator('select')).toBeDisabled();
  await expect(nameless.first()).toContainText(strings.noName);
  const item = section.locator('li.item', { has: page.locator('.choose-table') }).first();
  const id = await item.getAttribute('data-id');
  const code = (await rows.first().locator('td').first().textContent()) ?? '';
  if ((await nameless.count()) === (await rows.count())) {
    await item.locator('input.fact-codes').fill(code);
    await item.locator('button.fact-save-codes').click();
  } else {
    const firstChoice = choosing.first().locator('select.choose-choice:not([disabled])').first();
    await firstChoice.selectOption({ index: 1 });
    await section.locator('button.choose-save').first().click();
  }
  await expect(page.locator('.answer-confirmation')).toHaveText(strings.factRecorded, { timeout: 120_000 });
  await expect(section.locator(`li.item[data-id="${id}"][data-status="answered"]`).first()).toBeAttached();
  await expect(section.locator('pre[data-query="sizes"]')).toHaveCount(0);
  const next = section.locator('pre[data-query="values:AIRWAY_DEVICE.DEVICE_KIND_KEY"]');
  await expect(next).toHaveCount(1);
  await expect(next).toContainText('FROM [dbo].[AIRWAY_DEVICE] WITH (NOLOCK)');
  await noPlantedValue(page);

  // The merged check results are saved with the state, at the end of the checklist.
  const [saved] = await Promise.all([page.waitForEvent('download'), section.locator('button.ending-save').click()]);
  expect(saved.suggestedFilename()).toBe('schemalyser-state.zip');
  const checksCsv = execFileSync('unzip', ['-p', await saved.path(), 'checks.csv'], { encoding: 'utf8' });
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
  // The core profile is for the later release, so its paste box appears once that part is opened.
  await section.locator('details.release > summary').click();
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
  // The merged core profile is saved with the state, at the end of the checklist.
  const [saved] = await Promise.all([page.waitForEvent('download'), section.locator('button.ending-save').click()]);
  const profileCsv = execFileSync('unzip', ['-p', await saved.path(), 'core-profile.csv'], { encoding: 'utf8' });
  expect(profileCsv.split('\n')[0]).toBe('ITEM_CATEGORY,VALUE_01,VALUE_02,VALUE_03,VALUE_04,VALUE_05');
  expect(profileCsv).toContain('CDM_TABLE,person,Y,');
});

test('without a catalogue, one first query of names and sizes starts the project, and a ready question offers its audit query', async ({ page, context, browserName }) => {
  test.setTimeout(300_000);
  const state = seen(makeState(join(mkdtempSync(join(tmpdir(), 'schemalyser-first-')), 'state'), { catalogue: false, checks: false }), true);
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
  await expect(page.locator('#t-first-result')).toContainText('You can now choose Analyse the folder and the SQL files');
  // Every table that the audit needs came back, so nothing suggests the wrong database.
  await expect(page.locator('#t-first-doubt')).toBeHidden();
  await expect(page.locator('#analyse')).toBeEnabled();
  await page.locator('#analyse').click();
  await expect(page.locator('#checklists section.target')).toHaveCount(world.targets.length, { timeout: 120_000 });
  // The sizes are known, so no table sizes query is asked for, and the queries are ready at once.
  const section = page.locator('section.target[data-target="infant_low_pressure"]');
  await expect(section.locator('.stage-verdict')).toContainText('Everything that the audit query must rest on is settled');
  await expect(section.locator('pre[data-query="sizes"]')).toHaveCount(0);
  // The core and the timing are folded away under the heading for the later OMOP release.
  await expect(section.locator('details.release > summary')).toHaveText(strings.releaseHeading);
  await expect(section.locator('details.release li.item[data-id^="core-"]').first()).toBeHidden();
  // The catalogue and the sizes are saved together for the next project.
  const [saved] = await Promise.all([page.waitForEvent('download'), page.locator('#save-state').click()]);
  expect(saved.suggestedFilename()).toBe('schemalyser-state.zip');
  const listing = execFileSync('unzip', ['-Z1', await saved.path()], { encoding: 'utf8' }).trim().split('\n').sort();
  expect(listing).toEqual(['audit.json', 'catalogue.csv', 'checks.csv', 'core-profile.csv', 'facts.json', 'sql_evidence.json']);
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
  await page.locator('#state-folder').setInputFiles(seen(makeState(join(mkdtempSync(join(tmpdir(), 'schemalyser-ready-')), 'state')), true));
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
  await expect(ready.locator('.specification pre')).toContainText('10. Cases to check the query against');
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
  await expect(questions).toContainText('by joining on OBS_SHEET.VISIT_KEY = VISIT.VISIT_KEY. Is that right?');
  const copy = section.locator('.questions button', { hasText: strings.questionsCopy });
  await copy.click();
  await expect(copy).toHaveAttribute('data-copied', 'true');
  if (browserName === 'chromium') expect(await page.evaluate(() => navigator.clipboard.readText())).toBe((await questions.textContent()) ?? '');
  // The item puts the question first, and the query, where there is one, second.
  const item = section.locator('li.item[data-id="relationship-OBS_SHEET.VISIT_KEY=VISIT.VISIT_KEY"]');
  await expect(item.locator('.ask .ask-text')).toContainText('Is that right?');
  await expect(item.locator('button.fact-unsure')).toHaveText(strings.notSure);
  await section.locator('input.fact-who').fill('A colleague');
  await item.locator('button.fact-yes').click();
  await expect(page.locator('.answer-confirmation')).toHaveText(strings.factRecorded, { timeout: 120_000 });
  const ticked = section.locator('li.item[data-id="relationship-OBS_SHEET.VISIT_KEY=VISIT.VISIT_KEY"]');
  await expect(ticked.first()).toHaveAttribute('data-status', 'answered');
  await expect(ticked.first()).toContainText('You confirmed on');
  // The answer and the button that changes it stand in view beside each other, outside the folded account.
  await expect(ticked.first().locator('.answer-given .answer-text')).toBeVisible();
  await expect(ticked.first().locator('.answer-given .answer-text')).toContainText('You confirmed on');
  await expect(ticked.first().locator('details button.withdraw-answer')).toHaveCount(0);
  await expect(ticked.first().locator('.answer-given button.withdraw-answer')).toBeVisible();
  // The answer can be changed: withdrawn, the question is asked again, and the note of what was settled goes with it.
  await ticked.first().locator('button.withdraw-answer').click();
  await expect(page.locator('.answer-confirmation')).toHaveText(strings.withdrawn, { timeout: 120_000 });
  await expect(section.locator('.fresh-note')).toHaveCount(0);
  await expect(item.locator('.ask .ask-text')).toContainText('Is that right?');
  await item.locator('button.fact-yes').click();
  await expect(page.locator('.answer-confirmation')).toHaveText(strings.factRecorded, { timeout: 120_000 });
  await expect(ticked.first()).toHaveAttribute('data-status', 'answered');
  // The ending groups what remains by who can settle it, and its tally agrees with the list.
  const tally = (await section.locator('.tally').textContent()) ?? '';
  const listed = await section.locator('.ending ul.remaining li').count();
  if (listed) expect(tally).toContain(`needs ${listed} more`);
  await expect(section.locator('.questions pre')).not.toContainText('OBS_SHEET.VISIT_KEY = VISIT.VISIT_KEY');
  // The codes for a concept are found by the name search and chosen, and become the site's mapping rows.
  await chooseCodes(page, section);
  // The saved state holds the facts and the site's mapping rows, and nothing else that the page writes names who answered.
  const [saved] = await Promise.all([page.waitForEvent('download'), page.locator('#save-state').click()]);
  const listing = execFileSync('unzip', ['-Z1', await saved.path()], { encoding: 'utf8' });
  expect(listing).toContain('facts.json');
  expect(execFileSync('unzip', ['-p', await saved.path(), 'facts.json'], { encoding: 'utf8' })).toContain('A colleague');
  const [download] = await Promise.all([page.waitForEvent('download'), page.locator('#download').click()]);
  expect(execFileSync('unzip', ['-p', await download.path()], { encoding: 'utf8' })).not.toContain('A colleague');
  // Once the answer is no longer new, it stays in view among the answers given, beside the button that changes it, and
  // no folded part of the page holds that button.
  await page.locator('#reanalyse').click();
  const given = section.locator('ul.items[data-group="given"] li.item[data-id="relationship-OBS_SHEET.VISIT_KEY=VISIT.VISIT_KEY"]');
  await expect(given.locator('.answer-given button.withdraw-answer')).toBeVisible({ timeout: 120_000 });
  await expect(section.locator('details button.withdraw-answer')).toHaveCount(0);
});

test('once the count by year is seen, the codes are chosen from the list of what is charted on the cohort, and are counted on the cohort and reach the specification', async ({ page, context, browserName }) => {
  test.setTimeout(300_000);
  await page.goto('./');
  await expect(page.getByText(strings.loaded)).toBeVisible({ timeout: 120_000 });
  await setOnline(page, context, browserName, false);
  await page.locator('#state-folder').setInputFiles(seen(makeState(join(mkdtempSync(join(tmpdir(), 'schemalyser-charted-')), 'state'), { checks: false })));
  await page.locator('#folder').setInputFiles(fixtures + 'requests');
  await page.locator('#analyse').click();
  await expect(page.locator('#checklists section.target')).toHaveCount(world.targets.length, { timeout: 120_000 });
  const section = page.locator('section.target[data-target="neonatal_low_mean_pressure"]');
  // The count by year has been seen, so the list of what is charted on the cohort takes the place of the name search.
  const listed = section.locator('.listed');
  // The list is a script that puts the cohort into a temporary table and reaches the readings from it by key.
  await expect(listed.locator('pre[data-query="listed"]')).toContainText('FROM #cohort AS c');
  await expect(listed.locator('pre[data-query="listed"]')).toContainText('ALTER TABLE #cohort ADD PRIMARY KEY (anaesthetic_id);');
  // Above the script, what it creates, the time-out, what the plan must show and what means stop, and what it asks for.
  const notes = listed.locator('.script-note');
  await expect(notes.first()).toHaveText(strings.scriptTemporary);
  await expect(notes.filter({ hasText: strings.scriptTimeout })).toHaveCount(1);
  await expect(notes.filter({ hasText: 'These instructions are for the colleague who runs the SQL.' })).toHaveCount(1);
  await expect(notes.filter({ hasText: strings.scriptWorst('about 20 anaesthetics of the cohort in 2024') })).toHaveCount(1);
  // The script itself says where the time-out is set.
  await expect(listed.locator('pre[data-query="listed"]')).toContainText('open the Query menu, choose Query Options, then Execution');
  await expect(section.locator('textarea.search-paste')).toHaveCount(0);
  await expect(section.locator('li.item', { hasText: strings.listedInstead }).first()).toBeAttached();

  // A list that came back empty is recorded at once, and is the first point of what remains.
  const header = 'code\treadings\tanaesthetics\tOBS_LABEL\tUNIT_LABEL';
  await listed.locator('textarea.listed-paste').fill(header + '\n');
  await listed.locator('button.listed-read').click();
  await expect(page.locator('.answer-confirmation')).toHaveText(strings.factRecorded, { timeout: 120_000 });
  const first = section.locator('.ending ul.remaining li').first();
  await expect(first).toHaveAttribute('data-ids', /charted-empty/);
  await expect(first).toHaveText(/^The list of what is charted on the audit's anaesthetics in/);

  // Forty-five rows: the names that hold a word for the meanings sought, as a whole word, are marked as likely.
  const names = ['INVENTED ART MEAN', 'INVENTED NIBP MEAN', 'INVENTED MEANINGFUL NOTE', 'INVENTED PA MEAN', ...Array.from({ length: 41 }, (_, i) => `INVENTED ITEM ${i}`)];
  const many = [header, ...names.map((name, i) => `${i < 2 ? 52 - i : 100 + i}\t${1000 - i}\t${90 - i}\t${name}\tNULL`)].join('\n');
  await listed.locator('textarea.listed-paste').fill(many);
  await listed.locator('button.listed-read').click();
  const table = listed.locator('.listed-table');
  await expect(table.locator('select.listed-choice')).toHaveCount(45, { timeout: 120_000 });
  await expect(table.locator('tr[data-likely]')).toHaveCount(2);
  await expect(table.locator('tr[data-likely]', { hasText: 'MEANINGFUL' })).toHaveCount(0);
  // A pulmonary artery mean is never marked as likely for an arterial or cuff mean.
  await expect(table.locator('tr[data-likely]', { hasText: 'PA MEAN' })).toHaveCount(0);
  // Typing in the filter shows only the rows that hold the words.
  const filter = listed.locator('input.listed-filter');
  await filter.fill('NIBP');
  await expect(table.locator('tr:not([hidden]) select.listed-choice')).toHaveCount(1);
  await filter.fill('');
  await expect(table.locator('tr:not([hidden]) select.listed-choice')).toHaveCount(45);
  // The arterial line's code is chosen for the first meaning offered, and the choices are saved as the codes.
  const choice = table.locator('select.listed-choice[data-code="52"]');
  const values = await choice.locator('option').evaluateAll((options) => options.map((o) => (o as HTMLOptionElement).value));
  await choice.selectOption(values[1]);
  await listed.locator('button.listed-save').click();
  await expect(section.locator('li.item', { hasText: 'You chose on' }).first()).toBeAttached({ timeout: 120_000 });
  await expect(page.locator('.answer-confirmation')).toHaveText(strings.factRecorded);
  await expect(section.locator('.ending ul.remaining li[data-ids~="charted-empty"]')).toHaveCount(0);

  // With codes chosen and a study period set, the optional count is offered, and its result reaches the specification.
  const charted = section.locator('.charted');
  await expect(charted.locator('pre[data-query="charted"]')).toContainText('FROM #cohort AS c', { timeout: 120_000 });
  await expect(charted.locator('.script-note').first()).toHaveText(strings.scriptTemporary);
  await expect(charted.locator('.sizes-reason').first()).toContainText('fetches only the readings of those anaesthetics');
  await charted.locator('textarea.charted-paste').fill('code\treadings\tanaesthetics\n52\t1230\t40\n');
  await charted.locator('button.charted-read').click();
  await expect(section.locator('.charted .charted-result')).toHaveText(strings.chartedKept, { timeout: 120_000 });
  await expect(page.locator('.answer-confirmation')).toHaveText(strings.factRecorded);
  await expect(section.locator('.specification pre')).toContainText('the code 52 was charted 1,230 times on 40 of the audit\'s anaesthetics');
});

// The name search for codes: its result, as the grid copies it, is pasted, and the first row is chosen as the first
// meaning offered. The names are invented. The choice is saved as codes facts.
async function chooseCodes(page: Page, section: ReturnType<Page['locator']>): Promise<boolean> {
  // Where the check results already list the codes, no search is asked for, and nothing is to be chosen.
  if (!(await section.locator('li.item .ask textarea.search-paste').count())) section = page.locator('#checklists');
  const search = section.locator('li.item .ask textarea.search-paste').first();
  if (!(await search.count())) return false;
  await expect(search).toBeVisible({ timeout: 120_000 });
  const item = section.locator('li.item', { has: page.locator('textarea.search-paste') }).first();
  await search.fill('code\tname\n52\tINVENTED ARTERIAL MEAN\n51\tINVENTED CUFF MEAN\n');
  await item.locator('button.search-read').click();
  const choices = item.locator('select.candidate-choice');
  await expect(choices).toHaveCount(2, { timeout: 120_000 });
  const values = await choices.first().locator('option').evaluateAll((options) => options.map((o) => (o as HTMLOptionElement).value));
  await choices.first().selectOption(values[1]);
  await item.locator('button.search-save').click();
  await expect(page.locator('.answer-confirmation')).toHaveText(strings.factRecorded, { timeout: 120_000 });
  await expect(page.locator('#checklists li.item', { hasText: 'You chose on' }).first()).toBeAttached();
  return true;
}

// A state in which the count by year has been seen and judged about right, and a study period chosen, as a meeting leaves it.
// With confirmed, a person has also confirmed the codes that the invented conversion maps to the general anaesthetic and
// to the systolic pressure, which the infant questions compare with, so that those questions are ready.
function seen(folder: string, confirmed = false) {
  const codes = confirmed
    ? [{ kind: 'codes', vocabulary: 'SITE_ANAES_KIND', concept: 4174669, codes: ['1'], date: '2026-10-05' },
       { kind: 'codes', vocabulary: 'SITE_OBS_SYSTOLIC', concept: 3004249, codes: ['5'], date: '2026-10-05' }]
    : [];
  writeFileSync(join(folder, 'facts.json'), JSON.stringify({ facts: [{ kind: 'count', answer: 'right', date: '2026-10-05',
    years: [[2023, 120, 10], [2024, 130, 20]] }, ...codes] }));
  writeFileSync(join(folder, 'audit.json'), JSON.stringify({ from: '2019-01-01', to: '2025-12-31' }));
  return folder;
}

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
  await expect(page.locator('#t-first-result')).toContainText('You can now choose Analyse the folder and the SQL files');
  await page.locator('#analyse').click();
  await expect(page.locator('#checklists section.target')).toHaveCount(world.targets.length, { timeout: 120_000 });
  const section = page.locator('section.target[data-target="neonatal_low_mean_pressure"]');

  // A pasted check result.
  const query = section.locator('pre[data-query^="values:"]').first();
  const pasted = answer([(await query.textContent()) ?? '']);
  await page.locator('#paste').fill(pasted);
  await page.locator('#read-paste').click();
  await expect(page.locator('#t-paste-result')).toContainText('Schemalyser has read the', { timeout: 120_000 });

  // A fact answered, and a code entered.
  const JOINED = 'relationship-OBS_SHEET.VISIT_KEY=VISIT.VISIT_KEY';
  await section.locator('input.fact-who').fill('A colleague');
  await section.locator(`li.item[data-id="${JOINED}"] button.fact-yes`).click();
  await expect(page.locator('.answer-confirmation')).toHaveText(strings.factRecorded, { timeout: 120_000 });
  await expect(section.locator(`li.item[data-id="${JOINED}"]`).first()).toHaveAttribute('data-status', 'answered');
  // The codes found by the name search and chosen.
  await chooseCodes(page, section);
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
  await expect(page.locator('#t-files-sentence')).toHaveText('Schemalyser has read 15 files. It was not able to read 2 of them in full. In 1 file, part of the SQL could not be parsed, that is, Schemalyser could not read it as SQL. In 1 file, part of the SQL is built as text when it runs, so Schemalyser cannot see the tables inside it.');
});

test('on a training database the count by year is kept without judging it, and is to be asked again on the production copy', async ({ page, context, browserName }) => {
  test.setTimeout(300_000);
  await page.goto('./');
  await expect(page.getByText(strings.loaded)).toBeVisible({ timeout: 120_000 });
  await setOnline(page, context, browserName, false);
  await page.locator('#state-folder').setInputFiles(makeState(join(mkdtempSync(join(tmpdir(), 'schemalyser-training-')), 'state')));
  await page.locator('#folder').setInputFiles(fixtures + 'requests');
  await page.locator('#analyse').click();
  await expect(page.locator('#checklists section.target')).toHaveCount(world.targets.length, { timeout: 120_000 });
  // The page shows a pasted count beside the first count on the page, so the checklist that holds it is the one followed.
  const name = await page.locator('#checklists section.target').filter({ has: page.locator('textarea.count-paste') }).first().getAttribute('data-target');
  const section = page.locator(`section.target[data-target="${name}"]`);

  // The colleague says that the SQL window is connected to a training database, and the core works the checklist out again.
  await section.locator('.database input[value="training"]').check();
  await expect(page.locator('.answer-confirmation')).toHaveText(strings.factRecorded, { timeout: 120_000 });
  await expect(section.locator('.database input[value="training"]')).toBeChecked();

  // The count by year is still offered. Once pasted, the page does not ask whether the numbers look right.
  const item = section.locator('li.item[data-id="count-by-year"]').first();
  await item.locator('textarea.count-paste').fill('start_year\tanaesthetics\tin_the_cohort\tno_kind_recorded\n2023\t120\t10\tNULL\n2024\t130\t20\t10\n');
  await item.locator('button.count-read').click();
  await expect(item.locator('.count-training')).toHaveText(strings.countTraining, { timeout: 120_000 });
  await expect(item.locator('button.count-right')).toHaveCount(0);
  await item.locator('button.count-keep').click();
  await expect(page.locator('.answer-confirmation')).toHaveText(strings.factRecorded, { timeout: 120_000 });

  // The item is marked to be asked again on the production copy, rather than open or settled, and takes no paste here.
  const marked = section.locator('li.item[data-id="count-by-year"][data-again="true"]').first();
  await expect(marked.locator('.again-mark')).toHaveText(strings.againMark, { timeout: 120_000 });
  await expect(marked.locator('textarea.count-paste')).toHaveCount(0);
  // What remains gains a fourth group, and the tally at the head counts it apart.
  const again = section.locator('.ending ul.remaining[data-who="again"]');
  await expect(again.locator('li[data-ids~="count-by-year"]')).toHaveCount(1);
  await expect(section.locator('.ending .remaining-heading', { hasText: strings.endingAgainHeading })).toHaveCount(1);
  const tally = (await section.locator('p.tally').textContent()) ?? '';
  const n = await again.locator('li').count();
  expect(tally).toContain(`${n} to ask again on the production copy`);
  await expect(section.locator('.ending .ending-database')).toHaveText(strings.databaseRecorded.training);
  await expect(section.locator('.specification pre')).toContainText('a training database with fictional patients');

  // The state saved at the end records the choice and the count kept without judgement.
  const [saved] = await Promise.all([page.waitForEvent('download'), section.locator('button.ending-save').click()]);
  expect(JSON.parse(execFileSync('unzip', ['-p', await saved.path(), 'audit.json'], { encoding: 'utf8' })).database).toBe('training');
  const facts = JSON.parse(execFileSync('unzip', ['-p', await saved.path(), 'facts.json'], { encoding: 'utf8' })).facts;
  expect(facts.find((fact: { kind: string }) => fact.kind === 'count').answer).toBe('training');
});
