import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test, type Page } from '@playwright/test';
import { setOnline } from './network';
import { describeStrings as d } from '../src/describe-strings';

// The page Describe the record, walked through with the invented dictionary and the invented world: loaded, taken
// offline, the hospital schema proposed, the query of tables and columns answered from the invented catalogue, some
// columns confirmed, a list of what is charted and a count pasted, the hospital schema saved as one file, and that file
// opened again.
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

test('the record is described, saved as a hospital schema and opened again', async ({ page, context, browserName }) => {
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

  // Step 6 says once what it asks, and names each part and column in plain words.
  await expect(page.locator('#t-confirm-intro')).toHaveText(d.confirmIntro);
  await expect(page.locator('#confirm [data-role="role_patient"] h3')).toContainText('Patients');
  await expect(page.locator('#confirm [data-role="role_reading"] h3')).toContainText('Readings charted during an anaesthetic');
  await expect(page.locator('#confirm [data-role="role_patient"] .who')).toHaveText(d.whoAnswers.colleague);
  await expect(page.locator('#confirm [data-about="role_patient.birth_date"] .attribute')).toHaveText('Date of birth');
  await expect(page.locator('#confirm [data-about="role_patient.patient_key"] .meaning')).toContainText("the patient's identifier in Anaesthetics");
  // A column for which the page found nothing offers no Yes, and says so.
  const nothing = page.locator('#confirm li.binding:has(.proposed .nothing)').first();
  await expect(nothing).toContainText(d.nothingProposed);
  await expect(nothing.locator('.answer-yes')).toHaveCount(0);
  await expect(nothing.getByRole('button', { name: d.chooseColumn })).toBeVisible();

  // Some columns confirmed, one corrected by hand after its test on made-up rows, one not sure.
  const tallyText = (await page.locator('#t-tally').textContent())!;
  const total = Number(tallyText.match(/^Of (\d+) columns/)![1]);
  const tables = Number(tallyText.match(/Of the (\d+) tables of the parts/)![1]);
  expect(total).toBeGreaterThan(40);
  // The figures count columns only; the tables of the parts are counted apart.
  const tally = (confirmed: number, corrected: number, notSure: number, untranslated: number) =>
    d.tally({ confirmed, corrected, not_sure: notSure, untranslated, remaining: total - confirmed - corrected - notSure - untranslated, total, tables, tables_remaining: tables });
  const birthRow = page.locator('#confirm [data-about="role_patient.birth_date"]');
  await birthRow.getByRole('button', { name: d.yes }).click();
  // An answered column shows its answer as a state, with Change the answer in place of the choices.
  await expect(birthRow.locator('.answered')).toContainText('Confirmed on');
  await expect(birthRow.getByRole('button', { name: d.yes })).toHaveCount(0);
  await birthRow.getByRole('button', { name: d.change }).click();
  await expect(birthRow.getByRole('button', { name: d.yes })).toBeVisible();
  await birthRow.getByRole('button', { name: d.yes }).click();
  await page.locator('#confirm [data-about="role_reading.value"]').getByRole('button', { name: d.notSure }).click();
  await expect(page.locator('#confirm [data-about="role_reading.value"] .answered')).toContainText('Not sure, listed as a question on');
  const patient = page.locator('#confirm [data-about="role_anaesthetic.patient_key"]');
  await patient.getByRole('button', { name: d.another }).click();
  await patient.locator('input[type=text]').fill('THEATRE_CASE.NO_SUCH');
  await patient.getByRole('button', { name: d.anotherUse }).click();
  await expect(patient.locator('.problem-note')).toContainText('The dictionary holds no column THEATRE_CASE.NO_SUCH.');
  await patient.locator('input[type=text]').fill('THEATRE_CASE.PERSON_KEY');
  // The written name clears the list's choice, and the row says which is in force.
  await expect(patient.locator('.in-force')).toHaveText(d.inForceWritten('THEATRE_CASE.PERSON_KEY'));
  await patient.getByRole('button', { name: d.anotherUse }).click();
  // The chosen column's sentence is shown; the person then checks it, as in every other form, before keeping it.
  await expect(patient.locator('.correction-sentence')).toContainText("The patient's identifier in Anaesthetics is THEATRE_CASE.PERSON_KEY");
  await expect(patient.locator('.check-report')).toHaveCount(0);
  await patient.getByRole('button', { name: d.corrections.checkButton }).click();
  await expect(patient.locator('.check-report')).toBeVisible({ timeout: 300_000 });
  await expect(patient.locator('.passed-means')).toHaveText(d.corrections.passedMeans);
  await patient.getByRole('button', { name: d.corrections.keep, exact: true }).click();
  await expect(patient).toContainText('Corrected to THEATRE_CASE.PERSON_KEY');
  await expect(patient.locator('.proposed')).toHaveCount(0);
  await expect(page.locator('#t-tally')).toHaveText(tally(1, 1, 1, 0));
  // Each question states the proposal and asks whether it is right.
  await expect(page.locator('#questions')).toContainText(
    'The value in Readings charted during an anaesthetic: The page proposes OBS_READING.READ_VALUE as the value in Readings charted during an anaesthetic. Is that right, and if not, which column holds it?');
  // After the first answer, the test of the whole hospital schema says that it tests the schema as it now stands.
  await expect(page.locator('#model-check')).toHaveText(d.corrections.modelCheckAgain);
  await expect(page.locator('#questions')).not.toContainText('roles.md');
  // No code name of a part or a column stands alone anywhere in the step.
  expect(await page.locator('#step-6').innerText()).not.toMatch(/\brole_[a-z]/);
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
  await expect(readings.locator('th').nth(1)).toHaveText('Times charted');
  await expect(readings.locator('select[data-code="77"] option[value="other"]')).toHaveCount(1);
  await expect(readings).toContainText(d.codesOther);
  // Each kind is offered in plain words, with its code after it.
  await expect(readings.locator('select[data-code="52"] option[value="map_arterial"]')).toHaveText('A mean arterial pressure from an arterial line, in mmHg (map_arterial)');
  await readings.locator('select[data-code="52"]').selectOption('map_arterial');
  await readings.locator('select[data-code="51"]').selectOption('map_cuff');
  await readings.getByRole('button', { name: d.codesSave }).click();
  await expect(page.locator('[data-key="role_reading.kind"]')).toContainText('The page saved 2 codes for this list on');
  // Step 7 is done only when every list is saved, and the rail says how many are.
  const lists = await page.locator('#vocabularies section.vocabulary:has(button)').count();
  await expect(page.locator('#rail a[href="#step-7"]')).toContainText(d.receipt.codes(1, lists).replace(/\.$/, ''));
  await expect(page.locator('#step-7')).not.toHaveAttribute('data-state', 'done');
  await stage(page, '7-codes', '[data-key="role_reading.kind"]');

  // A count, pasted and judged.
  await openStep(page, 8);
  await page.locator('#counts-write').click();
  const block = page.locator('[data-count="coverage_by_year"]');
  await expect(block).toContainText(d.countSafe);
  // The database is a training one, so every count carries the reminder to run it again on production.
  await expect(block).toContainText(d.countTraining);
  await expect(page.locator('[data-count="readings_by_kind"] pre')).toContainText("IN ('52') THEN 'map_arterial'");
  await block.locator('textarea').fill(coverage);
  await block.getByRole('button', { name: d.countRead }).click();
  await expect(page.locator('[data-count="coverage_by_year"]')).toContainText(d.countReceipt(2));
  await page.locator('[data-count="coverage_by_year"] input[value="yes"]').check();
  await page.locator('[data-count="coverage_by_year"]').getByRole('button', { name: d.lookRightSave }).click();
  await expect(page.locator('[data-count="coverage_by_year"]')).toContainText('this count looks right');
  await expect(page.locator('[data-count="coverage_by_year"]')).toContainText(d.lookRightTraining);
  await expect(page.locator('[data-count="coverage_by_year"]')).toContainText(d.lookRightCompare.coverage_by_year);
  // Step 8 is done only when every count offered has a judgement.
  await expect(page.locator('#rail a[href="#step-8"]')).toContainText(d.receipt.counts(1, 3).replace(/\.$/, ''));
  await expect(page.locator('#step-8')).not.toHaveAttribute('data-state', 'done');
  await stage(page, '8-counts', '[data-count="coverage_by_year"]');

  // The hospital schema, saved as one file that holds the dictionary as well.
  await openStep(page, 9);
  const download = page.waitForEvent('download');
  await page.locator('#write-save').click();
  const saved = await download;
  expect(saved.suggestedFilename()).toBe('hospital-schema.schemalyser.zip');
  // With columns still to answer, the hospital schema is saved as a draft and step 9 is not done.
  await expect(page.locator('#t-write-draft')).toContainText('The hospital schema is not yet complete:');
  await expect(page.locator('#t-write-status')).toContainText('The page has saved the hospital schema as a draft (');
  await expect(page.locator('#step-9')).not.toHaveAttribute('data-state', 'done');
  await expect(page.locator('#rail a[href="#step-9"]')).toContainText(d.savedDraft);
  await stage(page, '9-written', '#step-9');
  const unzipped = join(mkdtempSync(join(tmpdir(), 'hospital-')), 'hospital-schema');
  const zipPath = unzipped + '.schemalyser.zip';
  await saved.saveAs(zipPath);
  execFileSync('python3', ['-c', 'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])', zipPath, unzipped]);
  for (const name of ['README.md', 'journal.json', 'confirmations.csv', 'settings.json', 'map/map.json', 'map/role_reading.sql',
    'queries/01-tables-and-columns.sql', 'results/01-tables-and-columns.tsv', 'codes/role_reading.kind.json',
    'dictionary/invented-dictionary.csv', 'dictionary/invented-tables.csv']) {
    expect(existsSync(join(unzipped, name)), name).toBe(true);
  }
  expect(readFileSync(join(unzipped, 'map/role_reading.sql'), 'utf8')).toContain("IN ('52') THEN 'map_arterial'");
  // The view's head carries one true sentence about who has answered for it.
  expect(readFileSync(join(unzipped, 'map/role_anaesthetic.sql'), 'utf8')).not.toContain('No person has confirmed');
  const settings = JSON.parse(readFileSync(join(unzipped, 'settings.json'), 'utf8'));
  expect(settings.year).toBe(2024);
  expect(settings.complete).toBe(false);
  expect(settings.invented).toBeUndefined();
  expect(settings.draft).toMatch(/^draft: [\d,]+ columns and [\d,]+ tables unanswered/);
  const readme = readFileSync(join(unzipped, 'README.md'), 'utf8');
  expect(readme).toContain('## This hospital schema is a draft');
  expect(readme).toContain("It must stay on the hospital's own storage.");
  // The hospital schema says how many columns a person has answered for, and the README speaks of parts and the
  // hospital schema, not of roles, bindings, maps or folders.
  expect(JSON.parse(readFileSync(join(unzipped, 'map/map.json'), 'utf8')).description).toContain('A person has since answered for');
  expect(readme.replace(/`[^`]*`/g, '').replace(/\S*\/\S*/g, '')).not.toMatch(/\brole\b|\bbindings?\b|\bfolders?\b|\bmaps?\b/i);
  expect(readme).toContain('## Queries to run again on production');
  expect(readFileSync(join(unzipped, 'confirmations.csv'), 'utf8')).toMatch(/role_anaesthetic\.patient_key,no,"?THEATRE_CASE\.PERSON_KEY[^\n]*,passed,/);
  // A change after the hospital schema was saved makes step 9 to be done again.
  await page.locator('#confirm [data-about="role_patient.patient_key"] .answer-yes').click();
  await expect(page.locator('#step-9')).not.toHaveAttribute('data-state', 'done');
  await expect(page.locator('#t-write-status')).toHaveText(d.writtenStale);
  // No description of the dictionary is in any query.
  expect(readFileSync(join(unzipped, 'queries/01-tables-and-columns.sql'), 'utf8')).not.toContain('on which the patient was born');
  expect(requestsWhileOffline).toEqual([]);

  // The page returned to: a saved schema without the dictionary is refused until one is loaded, and the saved file
  // opens everything, the dictionary included, with nothing else.
  const stripped = unzipped + '-without-dictionary.schemalyser.zip';
  execFileSync('python3', ['-c', [
    'import sys, zipfile',
    'source = zipfile.ZipFile(sys.argv[1])',
    'with zipfile.ZipFile(sys.argv[2], "w") as out:',
    '    [out.writestr(i, source.read(i)) for i in source.infolist() if not i.filename.startswith("dictionary/")]',
  ].join('\n'), zipPath, stripped]);
  await setOnline(page, context, browserName, true);
  offline = false;
  await expect(page.locator('#t-locked')).toBeVisible();
  await loadAndGoOffline(page, context, browserName);
  offline = true;
  await openStep(page, 3);
  await expect(page.locator('#b-schema-file')).toHaveText(d.folderChoose);
  await page.locator('#schema-file').setInputFiles(stripped);
  await expect(page.locator('#t-folder-status')).toHaveText(d.folderNeedsDictionary);
  await expect(page.locator('#step-4')).toHaveAttribute('data-state', 'waiting');
  await page.locator('#schema-file').setInputFiles(zipPath);
  await expect(page.locator('#t-folder-status')).toContainText('The page has opened the hospital schema from the saved file.');
  await expect(page.locator('#receipt-2')).toHaveText(d.dictionaryReceipt({ tables: 25, columns: 98, described: 98, keyed: 25, skipped: 0, source: 'saved' }));
  await expect(page.locator('#step-2')).toHaveAttribute('data-state', 'done');
  // The Yes given after the file was saved is not in it, so the restored tally is the one that was saved.
  await expect(page.locator('#t-tally')).toHaveText(tally(1, 1, 1, 0));
  await expect(page.locator('#confirm [data-about="role_reading.value"]')).toContainText(d.presence.large('OBS_READING', 25_000_000));
  await expect(page.locator('[data-key="role_reading.kind"] select[data-code="52"]')).toHaveValue('map_arterial');
  await stage(page, '10-restored', '#rail-nav');

  // The saved schema checked against the database: the schema proposed again is the same, and each query is offered
  // to run again.
  await expect(page.locator('#check')).toHaveText(d.checkButton);
  await page.locator('#check').click();
  await expect(page.locator('#check-result')).toContainText(d.checkSame, { timeout: 60_000 });
  const again = page.locator('#check-result [data-query="tables-and-columns"]');
  await again.locator('textarea').fill(tablesResult().split('\n').filter((line) => !line.includes('\tVISIT_DIAGNOSIS\t')).join('\n'));
  await again.getByRole('button', { name: d.checkCompare }).click();
  await expect(again).toContainText('The table VISIT_DIAGNOSIS was in the earlier result and is not in the new one.');
  await stage(page, '11-checked', '#step-check');
  expect(requestsWhileOffline).toEqual([]);
});

// Step 6's corrections: one of each kind made in its plain form, its sentence and SQL shown, tested on made-up rows
// in the worker and kept or discarded; a link that repeats readings reported, and kept only with a reason; a window
// with no key refused; and the probes of kept corrections written and read back.
test('each kind of correction is tested on made-up rows before it is kept', async ({ page, context, browserName }) => {
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

  // The hospital schema as it stands, tested with no change; each finding is in plain words and leads to its column.
  await page.locator('#model-check').click();
  await expect(page.locator('#model-check-result')).toContainText('The hospital schema as it stands has', { timeout: 120_000 });
  expect(await page.locator('#model-check-result').innerText()).not.toMatch(/\brole_[a-z]|contract/);
  const finding = page.locator('#model-check-result a.finding-link').first();
  const sought = (await finding.getAttribute('data-about'))!;
  await finding.click();
  await expect(page.locator(`#confirm [data-about="${sought}"]`)).toBeInViewport();
  await stage(page, 'c0-model-check', '#step-6');

  const entry = (about: string) => page.locator(`#confirm [data-about="${about}"]`);
  const open = async (about: string, form: string) => {
    // A column that already has an answer shows Change the answer first.
    if (await entry(about).locator('.answer-change').count()) await entry(about).locator('.answer-change').click();
    await entry(about).locator('.answer-another').click();
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
  // The 1-or-0 form starts with the column already bound; each value is a labelled box.
  await expect(entry(about).getByLabel(c.tableLabel, { exact: true })).toHaveValue('THEATRE_CASE');
  await entry(about).getByLabel(c.valueRows('Y', 120)).check();
  await expect(entry(about).locator('.correction .correction-sentence')).toHaveText(
    "The emergency operation in the anaesthetic's details is 1 where THEATRE_CASE.EMERGENCY_FLAG, reached by matching ANAES_RECORD.CASE_KEY to THEATRE_CASE.CASE_KEY, holds Y, 0 where it holds anything else, and empty where it is empty.");
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
  await expect(entry(about).locator('.correction .correction-sentence')).toHaveText("The birth weight in grams in the patient's details at birth is PERSON_MASTER_2.BIRTH_WEIGHT_G multiplied by 1000.");
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
    "The patient's identifier in Anaesthetics is VISIT.PERSON_KEY, which ANAES_RECORD reaches through THEATRE_CASE, matching ANAES_RECORD.CASE_KEY to THEATRE_CASE.CASE_KEY, then VISIT, matching THEATRE_CASE.VISIT_KEY to VISIT.VISIT_KEY.");
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
  await entry(about).locator('.answer-another').click();

  // A link that also needs a time window, kept, and its probe written as a script of two parts.
  about = 'role_reading.anaesthetic_key';
  await open(about, 'window');
  await table(about, c.sharedTable, 'OBS_SHEET');
  await column(about, c.sharedColumn, 'VISIT_KEY');
  await column(about, c.anaestheticKey('ANAES_RECORD'), 'VISIT_KEY');
  await expect(entry(about).locator('.correction .correction-sentence')).toHaveText(
    "A reading belongs to the anaesthetic whose VISIT_KEY it shares (OBS_SHEET.VISIT_KEY = ANAES_RECORD.VISIT_KEY), if its time of the reading lies between the anaesthetic's start and stop, allowing 15 minutes either side.");
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

  // The hospital's codes, from the column already chosen.
  about = 'role_reading.kind';
  await open(about, 'codes');
  const code = entry(about).getByLabel(c.codeLabel);
  await code.fill('52');
  await code.press('Tab');
  await entry(about).getByLabel(c.kindLabel).selectOption('map_arterial');
  await expect(entry(about).locator('.correction .correction-sentence')).toHaveText(
    'The local code of OBS_READING.OBS_TYPE_KEY is translated as follows: 52 to a mean arterial pressure from an arterial line, in mmHg (map_arterial). Every other code is other.');
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
  await expect(broken).toContainText('This change breaks the hospital schema in 1 place:');
  await expect(broken).toContainText('readings appear twice, each linked to a second anaesthetic, so a reading no longer links to exactly one anaesthetic.');
  await stage(page, 'c6-broken', '#confirm [data-about="role_reading.anaesthetic_key"]');
  // After a failed test, only Discard and the ticked keep with its reason are offered.
  await expect(entry(about).getByRole('button', { name: c.keep, exact: true })).toHaveCount(0);
  await expect(entry(about).getByRole('button', { name: c.discard })).toBeVisible();
  await entry(about).locator('input.although').check();
  await entry(about).getByLabel(c.reasonLabel).fill('The database team says that each visit holds one anaesthetic at this hospital.');
  await entry(about).getByRole('button', { name: c.keepAlthough }).click();
  await expect(entry(about).locator('.kept-correction')).toContainText('although the test on made-up rows failed');
  await expect(entry(about).locator('.kept-correction')).toContainText('The reason given: The database team says');
  await stage(page, 'c7-kept-failing', '#confirm [data-about="role_reading.anaesthetic_key"]');

  // The saved hospital schema records each correction with the outcome of its test on made-up rows.
  await openStep(page, 9);
  const download = page.waitForEvent('download');
  await page.locator('#write-save').click();
  const saved = await download;
  const unzipped = join(mkdtempSync(join(tmpdir(), 'hospital-')), 'hospital-schema');
  await saved.saveAs(unzipped + '.zip');
  execFileSync('python3', ['-c', 'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])', unzipped + '.zip', unzipped]);
  const confirmations = readFileSync(join(unzipped, 'confirmations.csv'), 'utf8');
  expect(confirmations.split('\n')[0]).toBe('attribute,answer,replacement,date,note,version,correction,test,reason');
  expect(confirmations).toContain('failed: In Readings charted during an anaesthetic');
  expect(confirmations).toContain('The database team says that each visit holds one anaesthetic at this hospital.');
  const journal = JSON.parse(readFileSync(join(unzipped, 'journal.json'), 'utf8')).entries as { name: string; passed?: boolean }[];
  expect(journal.filter((e) => e.name === 'correction').map((e) => e.passed)).toEqual([true, true, true, true, true, true, true, true, false]);
  expect(readFileSync(join(unzipped, 'map/role_reading.sql'), 'utf8')).toContain('LEFT JOIN ANAES_RECORD');
  expect(requestsWhileOffline).toEqual([]);
});

// A Yes on a column that holds codes: the row says what the page assumes, a Yes leads straight on to the 1-or-0 form,
// the row and the rail say that the codes are not yet translated, the hospital schema is a draft that lists the column, and the
// translation kept on the proposed column is a confirmation.
test('a Yes on a column that holds codes leads on to its translation', async ({ page, context, browserName }) => {
  test.setTimeout(400_000);
  const c = d.corrections;
  await loadAndGoOffline(page, context, browserName);
  await loadDictionary(page);
  await page.locator('#propose').click();
  await expect(page.locator('#t-propose-status')).toContainText('The page has proposed', { timeout: 60_000 });
  await page.locator('#tables-write').click();
  await page.locator('#tables-paste').fill(tablesResult());
  await page.locator('#tables-read').click();
  await expect(page.locator('#t-tables-status')).toContainText('The page has read the result');
  const row = page.locator('#confirm [data-about="role_patient.is_test"]');
  await expect(row.locator('.coded .assumed')).toContainText('The page assumes that the test patient in Patients is 1 where PERSON_MASTER.TEST_PERSON_FLAG holds Y, Yes or 1');
  await expect(row.locator('.coded')).toContainText(d.coded.flag);
  // The reason for the proposal is the dictionary's matched words, under it.
  await expect(row.locator('.reason')).toContainText(d.reasonLabel);
  await stage(page, 'd1-before-yes', '#confirm [data-about="role_patient.is_test"]');
  await row.getByRole('button', { name: d.yes }).click();
  await expect(row.locator('.answered')).toContainText('codes not yet translated');
  await expect(row.locator('.translation')).toContainText(d.coded.flagNext);
  await expect(row.getByLabel(c.tableLabel, { exact: true })).toHaveValue('PERSON_MASTER');
  await expect(page.locator('#rail a[href="#step-6"]')).toContainText('1 still to translate');
  await expect(page.locator('#step-6')).not.toHaveAttribute('data-state', 'done');
  // Step 9 lists the column and calls the hospital schema a draft until it is translated.
  await openStep(page, 9);
  await expect(page.locator('#write-draft-list')).toContainText('The test patient in Patients');
  await expect(page.locator('#t-write-draft')).toContainText('1 still to translate');
  await stage(page, 'd1-draft', '#step-9');
  await page.locator('#write-draft-list a').first().click();
  await expect(row).toBeInViewport();
  // The values query, its result read back, and the value that means yes.
  await row.getByRole('button', { name: c.valuesWrite }).click();
  await expect(row.locator('pre').first()).toContainText('TOP (50)');
  await row.getByLabel(c.valuesPasteLabel).fill('value\trows\nY\t20\nN\t1200\n');
  await row.getByRole('button', { name: c.valuesRead }).click();
  await expect(row.getByLabel(c.valueRows('N', 1200))).toBeVisible();
  const values = row.getByLabel(c.flagValuesLabel);
  await values.fill('Y');
  await values.press('Tab');
  await expect(row.getByLabel(c.valueRows('Y', 20))).toBeChecked();
  await expect(row.getByLabel(c.valueRows('N', 1200))).not.toBeChecked();
  await expect(row.locator('.correction-sentence')).toContainText('is 1 where PERSON_MASTER.TEST_PERSON_FLAG holds Y, and 0 where it holds anything else or is empty.');
  await row.getByRole('button', { name: c.checkButton }).click();
  // The first test of a sitting also builds the hospital schema as it stands, so it takes the longest.
  await expect(row.locator('.check-report')).toBeVisible({ timeout: 300_000 });
  await expect(row.locator('.passed-means')).toHaveText(c.passedMeans);
  await stage(page, 'd1-checked', '#confirm [data-about="role_patient.is_test"]');
  await row.getByRole('button', { name: c.keep, exact: true }).click();
  // Kept on the proposed column, the translation is a confirmation, not a correction.
  await expect(row.locator('.answered')).toContainText('Confirmed on');
  await expect(row).not.toContainText('Corrected to');
  await expect(row.locator('.translation')).toHaveCount(0);
  await expect(page.locator('#rail a[href="#step-6"]')).not.toContainText('still to translate');
  await expect(page.locator('#write-draft-list li')).toHaveCount(0);
  // Its test query counts 1 and 0 alone, as the form never leaves this flag empty.
  await expect(row.locator('.probe .note').first()).toHaveText(c.probeWhat.flag_two);
  await stage(page, 'd1-translated', '#confirm [data-about="role_patient.is_test"]');
});

// The invented dictionary, loaded at step 2 while the tab is still online, then the tab taken offline: the receipt says
// which dictionary was read, the real file's control stays shut until the tab is offline, and the saved hospital schema
// says on its first line that it describes no hospital.
test('the invented dictionary is loaded while online and marks the saved schema as practice', async ({ page, context, browserName }) => {
  test.setTimeout(240_000);
  await page.goto('./describe.html');
  await expect(page.getByText(d.loaded)).toBeVisible({ timeout: 90_000 });
  // A real dictionary is never loaded online, so its control is shut; the invented one is offered from step 1.
  await expect(page.locator('#dictionary')).toBeDisabled();
  await page.locator('#t-offline-invented a').click();
  await expect(page.locator('#step-2 .body')).toBeVisible();
  await expect(page.locator('#dictionary')).toBeDisabled();
  await expect(page.locator('#h-choice-real')).toHaveText(d.choiceReal);
  await page.locator('#invented-load').click();
  const receipt = d.dictionaryReceipt({ tables: 25, columns: 98, described: 98, keyed: 25, skipped: 0, source: 'invented' });
  await expect(page.locator('#t-dictionary-status')).toHaveText(receipt);
  await stage(page, 'i1-invented', '#step-2');
  await setOnline(page, context, browserName, false);
  await expect(page.locator('#receipt-1')).toHaveText(d.offlineDone);
  await expect(page.locator('#receipt-2')).toHaveText(receipt);
  // Offline, the invented dictionary is not fetched again, and the page says why.
  await openStep(page, 2);
  await page.locator('#invented-load').click();
  await expect(page.locator('#t-invented-status')).toHaveText(d.inventedOnlineOnly);
  await page.locator('#propose').click();
  await expect(page.locator('#step-4')).toHaveAttribute('data-state', 'done', { timeout: 60_000 });
  await openStep(page, 9);
  const download = page.waitForEvent('download');
  await page.locator('#write-save').click();
  const saved = await download;
  const path = join(mkdtempSync(join(tmpdir(), 'hospital-')), 'practice.schemalyser.zip');
  await saved.saveAs(path);
  const read = (name: string) => execFileSync('python3', ['-c', 'import sys, zipfile; sys.stdout.write(zipfile.ZipFile(sys.argv[1]).read(sys.argv[2]).decode())', path, name], { encoding: 'utf8' });
  expect(read('README.md').split('\n')[0]).toBe('This file was made with the invented dictionary, for practice, and describes no hospital.');
  expect(JSON.parse(read('settings.json')).invented).toBe(true);
  expect(JSON.parse(read('journal.json')).entries[0].invented).toBe(true);
});
