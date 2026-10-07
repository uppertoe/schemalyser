import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test, type Page } from '@playwright/test';
import { setOnline } from './network';
import { describeStrings as d } from '../src/describe-strings';

// The page Describe the record, walked through with the invented dictionary and the invented world: loaded, taken
// offline, the map proposed, the tables and columns query answered from the invented catalogue, some bindings
// confirmed, a list of what is charted and a count pasted, the folder saved as a zip, and the folder chosen again.
const fixtures = fileURLToPath(new URL('../../fixtures/', import.meta.url));
const pass = process.env.DESCRIBE_PASS;

test.skip(({ browserName }) => browserName === 'webkit', 'The page refuses to start in WebKit.');

// The result of the tables and columns query for the invented world, in the ten columns that SQL Server returns, as
// SQL Server Management Studio copies it with its headers. The sizes are invented, and the readings are large.
function tablesResult() {
  const [head, ...lines] = readFileSync(fixtures + 'invented-catalogue.csv', 'utf8').trim().split('\n');
  const rows = lines.map((line) => {
    const cells = line.split(',').map((cell) => cell || 'NULL');
    return [...cells, cells[1] === 'OBS_READING' ? '25000000' : '1200'].join('\t');
  });
  return [[...head.split(','), 'TABLE_ROWS'].join('\t'), ...rows].join('\n');
}

const charted = [
  'code\tcharted\tanaesthetics\tname',
  '52\t4210\t380\tMean arterial pressure, arterial line',
  '51\t2950\t610\tMean arterial pressure, cuff',
  '10\t8800\t640\tHeart rate',
  '77\tNULL\tNULL\tLocal observation',
].join('\n');

const coverage = [
  'start_year\tanaesthetics\twith_patient\twith_birth_date\twith_death_date\ttest_patients\twith_stop\tstop_before_start',
  '2023\t400\t400\t400\t20\t0\t390\t0',
  '2024\t420\t420\t410\t10\t0\t410\t0',
].join('\n');

// With DESCRIBE_PASS set, each stage saves the page's text and a screenshot of the step in hand, at the size that
// DESCRIBE_VIEWPORT gives (such as 390x844), for reading the screen as a person would meet it.
async function stage(page: Page, name: string, step?: string) {
  if (!pass) return;
  const { writeFileSync, mkdirSync } = await import('node:fs');
  mkdirSync(pass, { recursive: true });
  writeFileSync(join(pass, `${name}.txt`), await page.locator('body').innerText());
  const target = page.locator(step ?? 'main [aria-current="step"]').first();
  if (await target.count()) await target.evaluate((node) => node.scrollIntoView({ block: 'start', behavior: 'instant' }));
  else await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
  await page.screenshot({ path: join(pass, `${name}.png`) });
  // Nothing on the page scrolls sideways, at a phone's width as well.
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(page.viewportSize()!.width);
}

// A step that is not the current one is folded; the rail opens it.
async function openStep(page: Page, n: number | string) {
  await page.locator(`#rail a[href="#step-${n}"]`).click();
  await expect(page.locator(`#step-${n} .body`)).toBeVisible();
}

async function loadAndGoOffline(page: Page, context: import('@playwright/test').BrowserContext, browserName: string) {
  const size = process.env.DESCRIBE_VIEWPORT?.match(/^(\d+)x(\d+)$/);
  if (size) await page.setViewportSize({ width: Number(size[1]), height: Number(size[2]) });
  await page.goto('./describe.html');
  await expect(page.getByText(d.loaded)).toBeVisible({ timeout: 90_000 });
  await expect(page.locator('#dictionary')).toBeDisabled();
  await setOnline(page, context, browserName, false);
  // Step 1 folds to its receipt once the tab is offline.
  await expect(page.locator('#receipt-1')).toHaveText(d.offlineDone);
  await expect(page.locator('#receipt-1')).toBeVisible();
}

async function loadDictionary(page: Page) {
  await page.locator('#dictionary').setInputFiles(fixtures + 'dictionary/invented-dictionary.csv');
  await page.locator('#dictionary-tables').setInputFiles(fixtures + 'dictionary/invented-tables.csv');
  await page.locator('#dictionary-load').click();
  await expect(page.locator('#t-dictionary-status')).toHaveText(
    d.dictionaryReceipt({ tables: 25, columns: 98, described: 98, keyed: 25, skipped: 0 }),
  );
}

test('the record is described, written as a hospital folder and restored from it', async ({ page, context, browserName }) => {
  test.setTimeout(240_000);
  const requestsWhileOffline: string[] = [];
  let offline = false;
  page.on('request', (request) => {
    if (offline && !request.url().startsWith('blob:') && !request.url().startsWith('data:')) requestsWhileOffline.push(request.url());
  });

  // The existing page leads here as its new first step.
  await page.goto('./');
  await expect(page.locator('#a-describe')).toHaveAttribute('href', './describe.html');

  await loadAndGoOffline(page, context, browserName);
  offline = true;
  await stage(page, '1-offline', 'body');

  // The dictionary, with its receipt.
  await loadDictionary(page);
  await stage(page, '2-dictionary');

  // The proposal, role by role, with the dictionary's definition beside each attribute.
  await page.locator('#propose').click();
  await expect(page.locator('#t-propose-status')).toContainText('The page has proposed', { timeout: 60_000 });
  const birth = page.locator('#proposal [data-about="role_patient.birth_date"]');
  await expect(birth).toContainText('PERSON_MASTER.BIRTH_TS');
  await expect(birth).toContainText('The date and time on which the patient was born.');
  await expect(page.locator('#proposal [data-role="role_unit_stay"]')).toContainText(d.roleUndrafted);
  await stage(page, '4-proposed', '#step-4');

  // The tables and columns query, and its result.
  await page.locator('#database-options input[value="training"]').check();
  await page.locator('#tables-write').click();
  await expect(page.locator('#tables-query')).toContainText('INFORMATION_SCHEMA.COLUMNS');
  await expect(page.locator('#tables-query')).toContainText("N'OBS_READING'");
  await page.locator('#tables-paste').fill(tablesResult());
  await page.locator('#tables-read').click();
  await expect(page.locator('#t-tables-status')).toContainText('The page has read the result');
  const value = page.locator('#confirm [data-about="role_reading.value"]');
  await expect(value).toContainText(d.presence.large('OBS_READING', 25_000_000));
  await stage(page, '5-tables', '#step-5');

  // Some bindings confirmed, one corrected by hand, one not sure.
  const total = Number((await page.locator('#t-tally').textContent())!.match(/^Of (\d+) columns/)![1]);
  expect(total).toBeGreaterThan(40);
  await page.locator('#confirm [data-about="role_patient.birth_date"]').getByRole('button', { name: d.yes }).click();
  await expect(page.locator('#confirm [data-about="role_patient.birth_date"]')).toContainText('Confirmed on');
  await page.locator('#confirm [data-about="role_reading.value"]').getByRole('button', { name: d.notSure }).click();
  const patient = page.locator('#confirm [data-about="role_anaesthetic.patient_key"]');
  await patient.getByRole('button', { name: d.another }).click();
  await patient.locator('input[type=text]').fill('THEATRE_CASE.NO_SUCH');
  await patient.getByRole('button', { name: d.anotherUse }).click();
  await expect(patient.locator('.problem-note')).toHaveText('The dictionary holds no column THEATRE_CASE.NO_SUCH.');
  await patient.locator('input[type=text]').fill('THEATRE_CASE.PERSON_KEY');
  await patient.getByRole('button', { name: d.anotherUse }).click();
  await expect(page.locator('#confirm [data-about="role_anaesthetic.patient_key"]')).toContainText('Corrected to THEATRE_CASE.PERSON_KEY');
  await expect(page.locator('#t-tally')).toHaveText(d.tally({ confirmed: 1, corrected: 1, not_sure: 1, remaining: total - 3, total }));
  await expect(page.locator('#questions')).toContainText('role_reading.value: Please confirm whether');
  await stage(page, '6-confirmed', '#confirm [data-about="role_anaesthetic.patient_key"]');

  // The codes of the readings, from a pasted list of what is charted, in step 7, which the rail opens.
  await openStep(page, 7);
  await page.locator('#year').fill('2024');
  await page.locator('#year').dispatchEvent('change');
  const readings = page.locator('[data-key="role_reading.kind"]');
  await readings.getByRole('button', { name: d.chartedWrite }).click();
  await expect(readings.locator('pre')).toContainText('INTO   #cohort');
  await readings.locator('textarea').fill(charted);
  await readings.getByRole('button', { name: d.chartedRead }).click();
  await expect(readings).toContainText(d.chartedReceipt(4, 2024));
  await readings.locator('select[data-code="52"]').selectOption('map_arterial');
  await readings.locator('select[data-code="51"]').selectOption('map_cuff');
  await readings.getByRole('button', { name: d.codesSave }).click();
  await expect(page.locator('[data-key="role_reading.kind"]')).toContainText('The page saved 2 codes for this list on');
  await stage(page, '7-codes', '[data-key="role_reading.kind"]');

  // A count, pasted and judged.
  await openStep(page, 8);
  await page.locator('#counts-write').click();
  const block = page.locator('[data-count="coverage_by_year"]');
  await expect(block).toContainText(d.countSafe);
  await expect(page.locator('[data-count="readings_by_kind"] pre')).toContainText("IN ('52') THEN 'map_arterial'");
  await block.locator('textarea').fill(coverage);
  await block.getByRole('button', { name: d.countRead }).click();
  await expect(page.locator('[data-count="coverage_by_year"]')).toContainText(d.countReceipt(2));
  await page.locator('[data-count="coverage_by_year"] input[value="yes"]').check();
  await page.locator('[data-count="coverage_by_year"]').getByRole('button', { name: d.lookRightSave }).click();
  await expect(page.locator('[data-count="coverage_by_year"]')).toContainText('this count looks right');
  await stage(page, '8-counts', '[data-count="coverage_by_year"]');

  // The folder, as a zip, without the dictionary.
  await openStep(page, 9);
  await expect(page.locator('#keep-dictionary')).not.toBeChecked();
  const download = page.waitForEvent('download');
  await page.locator('#write-zip').click();
  const saved = await download;
  expect(saved.suggestedFilename()).toBe('hospital-folder.zip');
  await expect(page.locator('#t-write-status')).toContainText('The page has saved the hospital folder as a zip of');
  await expect(page.locator('#step-9')).toHaveAttribute('data-state', 'done');
  await stage(page, '9-written', '#step-9');
  const folder = join(mkdtempSync(join(tmpdir(), 'hospital-')), 'hospital-folder');
  const zipPath = folder + '.zip';
  await saved.saveAs(zipPath);
  execFileSync('python3', ['-c', 'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])', zipPath, folder]);
  for (const name of ['README.md', 'journal.json', 'confirmations.csv', 'settings.json', 'map/map.json', 'map/role_reading.sql',
    'queries/01-tables-and-columns.sql', 'results/01-tables-and-columns.tsv', 'codes/role_reading.kind.json']) {
    expect(existsSync(join(folder, name)), name).toBe(true);
  }
  expect(existsSync(join(folder, 'dictionary'))).toBe(false);
  expect(readFileSync(join(folder, 'map/role_reading.sql'), 'utf8')).toContain("IN ('52') THEN 'map_arterial'");
  // No description of the dictionary is in any query.
  expect(readFileSync(join(folder, 'queries/01-tables-and-columns.sql'), 'utf8')).not.toContain('on which the patient was born');
  expect(requestsWhileOffline).toEqual([]);

  // The page returned to: the folder restores what was settled.
  await setOnline(page, context, browserName, true);
  offline = false;
  await expect(page.locator('#t-locked')).toBeVisible();
  await loadAndGoOffline(page, context, browserName);
  offline = true;
  await openStep(page, 3);
  await page.locator('#hospital-folder').setInputFiles(folder);
  await expect(page.locator('#t-folder-status')).toContainText('The page has restored the map from the hospital folder.');
  await expect(page.locator('#t-tally')).toHaveText(d.tally({ confirmed: 1, corrected: 1, not_sure: 1, remaining: total - 3, total }));
  await expect(page.locator('#confirm [data-about="role_reading.value"]')).toContainText(d.presence.large('OBS_READING', 25_000_000));
  await expect(page.locator('[data-key="role_reading.kind"] select[data-code="52"]')).toHaveValue('map_arterial');
  await stage(page, '10-restored', '#rail-nav');

  // The check: without the dictionary it lists the queries only, and with it the rebuilt map is the same.
  await page.locator('#check').click();
  await expect(page.locator('#check-result')).toContainText(d.checkNoDictionary);
  await expect(page.locator('#check-result [data-query="tables-and-columns"]')).toBeVisible();
  await loadDictionary(page);
  await page.locator('#check').click();
  await expect(page.locator('#check-result')).toContainText(d.checkSame, { timeout: 60_000 });
  const again = page.locator('#check-result [data-query="tables-and-columns"]');
  await again.locator('textarea').fill(tablesResult().split('\n').filter((line) => !line.includes('\tVISIT_DIAGNOSIS\t')).join('\n'));
  await again.getByRole('button', { name: d.checkCompare }).click();
  await expect(again).toContainText('The table VISIT_DIAGNOSIS was in the earlier result and is not in the new one.');
  await stage(page, '11-checked', '#step-check');
  expect(requestsWhileOffline).toEqual([]);
});

// Step 6's corrections: one of each kind made in its plain form, its sentence and SQL shown, checked on invented rows
// in the worker and kept or discarded; a link that repeats readings reported, and kept only with a reason; a window
// with no key refused; and the probes of kept corrections written and read back.
test('each kind of correction is checked on invented rows before it is kept', async ({ page, context, browserName }) => {
  test.setTimeout(900_000);
  const c = d.corrections;
  const requestsWhileOffline: string[] = [];
  let offline = false;
  page.on('request', (request) => {
    if (offline && !request.url().startsWith('blob:') && !request.url().startsWith('data:')) requestsWhileOffline.push(request.url());
  });
  await loadAndGoOffline(page, context, browserName);
  offline = true;
  await loadDictionary(page);
  await page.locator('#propose').click();
  await expect(page.locator('#t-propose-status')).toContainText('The page has proposed', { timeout: 60_000 });
  await page.locator('#tables-write').click();
  await page.locator('#tables-paste').fill(tablesResult());
  await page.locator('#tables-read').click();
  await expect(page.locator('#t-tables-status')).toContainText('The page has read the result');

  // The map as it stands, checked with no change.
  await page.locator('#model-check').click();
  await expect(page.locator('#model-check-result')).toContainText('The map as it stands has', { timeout: 120_000 });
  await stage(page, 'c0-model-check', '#step-6');

  const entry = (about: string) => page.locator(`#confirm [data-about="${about}"]`);
  const open = async (about: string, form: string) => {
    await entry(about).getByRole('button', { name: d.another }).click();
    await entry(about).locator('select.correction-form').selectOption(form);
  };
  const table = async (about: string, label: string, name: string) => {
    const box = entry(about).getByLabel(label, { exact: true });
    await box.fill(name);
    await box.press('Tab');
  };
  const column = (about: string, label: string, name: string) => entry(about).getByLabel(label, { exact: true }).selectOption(name);
  const check = async (about: string, passed: boolean) => {
    await expect(entry(about).locator('.correction .correction-sentence')).toBeVisible();
    await entry(about).getByRole('button', { name: c.checkButton }).click();
    const report = entry(about).locator('.check-report');
    await expect(report).toBeVisible({ timeout: 120_000 });
    await expect(report).toHaveClass(passed ? /passed/ : /failed/);
    return report;
  };
  const keep = async (about: string) => {
    await entry(about).getByRole('button', { name: c.keep, exact: true }).click();
    await expect(entry(about).locator('.kept-correction')).toBeVisible();
  };

  // A flag worked out from a column, its values chosen from a pasted query of values, then kept and probed.
  let about = 'role_anaesthetic_detail.is_emergency';
  await open(about, 'flag');
  await table(about, c.tableLabel, 'THEATRE_CASE');
  await column(about, c.columnLabel, 'EMERGENCY_FLAG');
  await entry(about).getByRole('button', { name: c.valuesWrite }).click();
  await expect(entry(about).locator('pre').first()).toContainText('TOP (50)');
  await entry(about).getByLabel(c.valuesPasteLabel).fill('value\trows\nY\t120\nN\t900\n');
  await entry(about).getByRole('button', { name: c.valuesRead }).click();
  await entry(about).locator('fieldset.values input[value="Y"]').check();
  await expect(entry(about).locator('.correction .correction-sentence')).toHaveText(
    'is_emergency is 1 where THEATRE_CASE.EMERGENCY_FLAG, reached through ANAES_RECORD.CASE_KEY = THEATRE_CASE.CASE_KEY, holds Y, 0 where it holds anything else, and empty where it is empty.');
  await expect(entry(about).locator('.correction-sql')).toContainText("IN ('Y') THEN 1 ELSE 0 END AS is_emergency");
  await check(about, true);
  await stage(page, 'c1-flag-checked', '#confirm [data-about="role_anaesthetic_detail.is_emergency"]');
  await keep(about);
  await entry(about).getByRole('button', { name: c.probeWrite }).click();
  await expect(entry(about).locator('.probe pre')).toContainText('AS ones');
  await entry(about).getByLabel(c.probePasteLabel).fill('ones\tzeros\tempty\n120\t900\tNULL\n');
  await entry(about).getByRole('button', { name: c.probeRead }).click();
  await expect(entry(about).locator('.probe')).toContainText('The flag is 1 in about 120 rows, 0 in about 900 and empty in fewer than ten.');
  await stage(page, 'c2-flag-probed', '#confirm [data-about="role_anaesthetic_detail.is_emergency"]');

  // A number in another unit, checked and discarded.
  about = 'role_patient_detail.birth_weight_grams';
  await open(about, 'scale');
  await table(about, c.tableLabel, 'PERSON_MASTER_2');
  await column(about, c.columnLabel, 'BIRTH_WEIGHT_G');
  const factor = entry(about).getByLabel(c.factorLabel);
  await factor.fill('1000');
  await factor.press('Tab');
  await expect(entry(about).locator('.correction .correction-sentence')).toHaveText('birth_weight_grams is PERSON_MASTER_2.BIRTH_WEIGHT_G multiplied by 1000.');
  await check(about, true);
  await entry(about).getByRole('button', { name: c.discard }).click();
  await expect(entry(about).locator('.correction')).toHaveCount(0);

  // The date of a date and time, and text with its spaces removed, which also mends a fault of the proposal.
  about = 'role_patient.birth_date';
  await open(about, 'date');
  await table(about, c.tableLabel, 'PERSON_MASTER');
  await column(about, c.columnLabel, 'BIRTH_TS');
  await check(about, true);
  await keep(about);
  about = 'role_drug.unit';
  await open(about, 'trim');
  await table(about, c.tableLabel, 'DRUG_GIVEN');
  await column(about, c.columnLabel, 'DOSE_UNIT_CAT');
  await expect(await check(about, true)).toContainText('This change also mends 1 problem that was there before it.');
  await keep(about);

  // Only some of the rows, kept and probed.
  about = 'role_anaesthetic rows';
  await open(about, 'filter');
  await table(about, c.filterTable, 'THEATRE_CASE');
  await column(about, c.filterColumn, 'CASE_STATUS_CAT');
  const values = entry(about).getByLabel(c.valuesLabel);
  await values.fill('2');
  await values.press('Tab');
  await expect(entry(about).locator('.correction-sql')).toContainText("WHERE  CAST(t2.CASE_STATUS_CAT AS varchar(254)) IN ('2')");
  await check(about, true);
  await keep(about);
  await entry(about).getByRole('button', { name: c.probeWrite }).click();
  await entry(about).getByLabel(c.probePasteLabel).fill('rows_read\tpassing\n1200\t1100\n');
  await entry(about).getByRole('button', { name: c.probeRead }).click();
  await expect(entry(about).locator('.probe')).toContainText('Of about 1,200 rows read, about 1,100 pass the filter.');
  await stage(page, 'c3-filter', '#confirm [data-about="role_anaesthetic rows"]');

  // A link through two other tables, then a link that joins on two columns, which is checked and discarded.
  about = 'role_anaesthetic.patient_key';
  await open(about, 'path');
  const step = (n: number) => entry(about).locator(`[data-step="${n}"]`);
  await step(1).getByLabel(c.stepFrom('ANAES_RECORD')).selectOption('CASE_KEY');
  await step(1).getByLabel(c.stepTo).fill('THEATRE_CASE');
  await step(1).getByLabel(c.stepTo).press('Tab');
  await step(1).getByLabel(c.stepToColumn).selectOption('CASE_KEY');
  await entry(about).getByRole('button', { name: c.addStep }).click();
  await step(2).getByLabel(c.stepFrom('THEATRE_CASE')).selectOption('VISIT_KEY');
  await step(2).getByLabel(c.stepTo).fill('VISIT');
  await step(2).getByLabel(c.stepTo).press('Tab');
  await step(2).getByLabel(c.stepToColumn).selectOption('VISIT_KEY');
  await column(about, c.finalColumn('VISIT'), 'PERSON_KEY');
  await expect(entry(about).locator('.correction .correction-sentence')).toHaveText(
    'patient_key is VISIT.PERSON_KEY, which ANAES_RECORD reaches through THEATRE_CASE (ANAES_RECORD.CASE_KEY = THEATRE_CASE.CASE_KEY), then VISIT (THEATRE_CASE.VISIT_KEY = VISIT.VISIT_KEY).');
  await check(about, true);
  await keep(about);
  await open(about, 'pair');
  await step(1).getByLabel(c.stepFrom('ANAES_RECORD')).selectOption('CASE_KEY');
  await step(1).getByLabel(c.stepTo).fill('THEATRE_CASE');
  await step(1).getByLabel(c.stepTo).press('Tab');
  await step(1).getByLabel(c.stepToColumn).selectOption('CASE_KEY');
  await step(1).getByLabel(c.stepSecondFrom('ANAES_RECORD')).selectOption('VISIT_KEY');
  await step(1).getByLabel(c.stepSecondTo).selectOption('VISIT_KEY');
  await column(about, c.finalColumn('THEATRE_CASE'), 'PERSON_KEY');
  await expect(entry(about).locator('.correction-sql')).toContainText('ON t1.CASE_KEY = t0.CASE_KEY AND t1.VISIT_KEY = t0.VISIT_KEY');
  await check(about, true);
  await entry(about).getByRole('button', { name: c.discard }).click();

  // A window with no key is refused, with rule 3 beside it.
  about = 'role_event.anaesthetic_key';
  await open(about, 'window');
  await expect(entry(about).locator('.window-rule')).toHaveText(c.windowRule);
  await expect(entry(about).locator('.problem-note')).toContainText('Rule 3 of the record says');
  await entry(about).getByRole('button', { name: d.another }).click();

  // A link that also needs a time window, kept, and its probe written as a script of two parts.
  about = 'role_reading.anaesthetic_key';
  await open(about, 'window');
  await table(about, c.sharedTable, 'OBS_SHEET');
  await column(about, c.sharedColumn, 'VISIT_KEY');
  await column(about, c.anaestheticKey('ANAES_RECORD'), 'VISIT_KEY');
  await expect(entry(about).locator('.correction .correction-sentence')).toHaveText(
    "A reading belongs to the anaesthetic whose VISIT_KEY it shares (OBS_SHEET.VISIT_KEY = ANAES_RECORD.VISIT_KEY), if its reading_time lies between the anaesthetic's start and stop, allowing 15 minutes either side.");
  const windowed = await check(about, true);
  await expect(windowed).toContainText("outside the anaesthetic's window, as the window intends");
  await stage(page, 'c4-window-checked', '#confirm [data-about="role_reading.anaesthetic_key"]');
  await keep(about);
  await entry(about).getByRole('button', { name: c.probeWrite }).click();
  await expect(entry(about).locator('.probe pre')).toContainText('INTO   #cohort');
  await expect(entry(about).locator('.probe pre')).toContainText('DATEADD(minute, -15, w.ANAES_START_TS)');

  // Several rows joined into one text.
  about = 'role_drug.route';
  await open(about, 'joined');
  await column(about, c.onColumn, 'ROUTE_CAT');
  await table(about, c.rowsTable, 'LK_ROUTE');
  await column(about, c.linkColumn, 'ROUTE_CAT');
  await column(about, c.textColumn, 'LABEL');
  await column(about, c.orderColumn, 'LABEL');
  await expect(entry(about).locator('.correction-sql')).toContainText('STRING_AGG(');
  await check(about, true);
  await keep(about);

  // The local codes, from the binding.
  about = 'role_reading.kind';
  await open(about, 'codes');
  const code = entry(about).getByLabel(c.codeLabel);
  await code.fill('52');
  await code.press('Tab');
  await entry(about).getByLabel(c.kindLabel).selectOption('map_arterial');
  await expect(entry(about).locator('.correction .correction-sentence')).toHaveText(
    'The local code of OBS_READING.OBS_TYPE_KEY is translated to map_arterial (52), and every other code is other.');
  await check(about, true);
  await keep(about);
  await stage(page, 'c5-kept', '#confirm [data-about="role_reading.kind"]');

  // A link that repeats readings: reported, refused without a reason, and kept with one.
  about = 'role_reading.anaesthetic_key';
  await open(about, 'path');
  await step(1).getByLabel(c.stepFrom('OBS_READING')).selectOption('SHEET_KEY');
  await step(1).getByLabel(c.stepTo).fill('OBS_SHEET');
  await step(1).getByLabel(c.stepTo).press('Tab');
  await step(1).getByLabel(c.stepToColumn).selectOption('SHEET_KEY');
  await entry(about).getByRole('button', { name: c.addStep }).click();
  await step(2).getByLabel(c.stepFrom('OBS_SHEET')).selectOption('VISIT_KEY');
  await step(2).getByLabel(c.stepTo).fill('ANAES_RECORD');
  await step(2).getByLabel(c.stepTo).press('Tab');
  await step(2).getByLabel(c.stepToColumn).selectOption('VISIT_KEY');
  await column(about, c.finalColumn('ANAES_RECORD'), 'ANAES_KEY');
  const broken = await check(about, false);
  await expect(broken).toContainText('This change breaks the map in 1 place:');
  await expect(broken).toContainText('readings twice, each linked to a second anaesthetic, so a reading no longer links to exactly one anaesthetic.');
  await stage(page, 'c6-broken', '#confirm [data-about="role_reading.anaesthetic_key"]');
  await entry(about).getByRole('button', { name: c.keep, exact: true }).click();
  await expect(entry(about).locator('.problem-note')).toContainText('tick Keep it although the check fails and give the reason');
  await entry(about).getByRole('button', { name: c.checkButton }).click();
  await expect(entry(about).locator('.check-report')).toBeVisible({ timeout: 120_000 });
  await entry(about).locator('input.although').check();
  await entry(about).getByLabel(c.reasonLabel).fill('The database team says that each visit holds one anaesthetic at this hospital.');
  await entry(about).getByRole('button', { name: c.keep, exact: true }).click();
  await expect(entry(about).locator('.kept-correction')).toContainText('although the check failed');
  await expect(entry(about).locator('.kept-correction')).toContainText('The reason given: The database team says');
  await stage(page, 'c7-kept-failing', '#confirm [data-about="role_reading.anaesthetic_key"]');

  // The folder records each correction with the outcome of its check.
  await openStep(page, 9);
  const download = page.waitForEvent('download');
  await page.locator('#write-zip').click();
  const saved = await download;
  const folder = join(mkdtempSync(join(tmpdir(), 'hospital-')), 'hospital-folder');
  await saved.saveAs(folder + '.zip');
  execFileSync('python3', ['-c', 'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])', folder + '.zip', folder]);
  const confirmations = readFileSync(join(folder, 'confirmations.csv'), 'utf8');
  expect(confirmations.split('\n')[0]).toBe('attribute,answer,replacement,date,note,version,correction,check,reason');
  expect(confirmations).toContain('failed: role_reading gives');
  expect(confirmations).toContain('The database team says that each visit holds one anaesthetic at this hospital.');
  const journal = JSON.parse(readFileSync(join(folder, 'journal.json'), 'utf8')).entries as { name: string; passed?: boolean }[];
  expect(journal.filter((e) => e.name === 'correction').map((e) => e.passed)).toEqual([true, true, true, true, true, true, true, true, false]);
  expect(readFileSync(join(folder, 'map/role_reading.sql'), 'utf8')).toContain('LEFT JOIN ANAES_RECORD');
  expect(requestsWhileOffline).toEqual([]);
});
