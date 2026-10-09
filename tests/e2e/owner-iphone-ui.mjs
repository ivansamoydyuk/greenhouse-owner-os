// Real Chromium + authenticated disposable preview regression gate.
// SpeechRecognition and visualViewport are explicitly simulated. This suite
// does not claim physical-iPhone, Safari microphone or native-keyboard acceptance.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {chromium} from 'playwright';
import {navigateOwner, waitOwnerRoute, revealOwnerControl} from './navigation.mjs';
import {isVisibleOwnerControl} from './visibility.mjs';
import {intendedTask, sourceTasks, taskSnapshots} from './task-review-fixture.mjs';

const base = process.env.PREVIEW_URL || 'http://127.0.0.1:4173';
assert.ok(['127.0.0.1', 'localhost', '[::1]'].includes(new URL(base).hostname), 'Disposable local preview only');
assert.ok(process.env.PREVIEW_SECRET, 'Use the guarded preview runner');
const artifacts = process.env.E2E_ARTIFACTS || 'artifacts';
await fs.mkdir(artifacts, {recursive: true});
const browser = await chromium.launch({headless: true, args: ['--no-sandbox'],
  ...(process.env.CHROME_PATH ? {executablePath: process.env.CHROME_PATH} : {})});
const context = await browser.newContext({viewport: {width: 390, height: 844}, locale: 'uk-UA',
  timezoneId: 'Europe/Kyiv', isMobile: true, hasTouch: true});
const page = await context.newPage(), errors = [], screenshots = [], fixtures = {}, captureWrites = [];
page.on('request', request => {if (request.method() === 'POST' && new URL(request.url()).pathname.endsWith('/owner/captures')) captureWrites.push(request.postDataJSON());});
page.on('pageerror', error => errors.push(error.message));
page.on('console', message => {if (message.type() === 'error') errors.push(message.text());});
await page.addInitScript(`window.isVisibleOwnerControl = (${isVisibleOwnerControl.toString()});`);
await page.addInitScript(() => {
  window.iphoneUiFaults = [];
  new MutationObserver(() => {
    const text = document.querySelector('#notice')?.textContent || '';
    if (/TypeError|Cannot read properties|undefined is not|ReferenceError/.test(text)) window.iphoneUiFaults.push(text);
  }).observe(document, {childList: true, subtree: true, characterData: true});
  // A stack-scoped activation sentinel is stricter than transient activation:
  // it ends when this same native click finishes bubbling, before any promise.
  window.iphoneSpeech = {activeClick: false, starts: [], runs: [], failure: null};
  document.addEventListener('click', event => {window.iphoneSpeech.activeClick = event.isTrusted;}, true);
  document.addEventListener('click', () => {window.iphoneSpeech.activeClick = false;});
  class TestSpeechRecognition {
    constructor() {window.iphoneSpeech.runs.push(this);}
    start() {
      const state = window.iphoneSpeech;
      state.starts.push({sameClickStack: state.activeClick, lang: this.lang,
        continuous: this.continuous, interimResults: this.interimResults});
      this.lateResult = this.onresult;
      if (state.failure === 'throw') throw Error('TEST unavailable recognition');
      if (state.failure === 'denied') queueMicrotask(() => this.onerror?.({error: 'not-allowed'}));
    }
    abort() {this.aborted = true;}
    stop() {this.stopped = true;}
  }
  window.TestSpeechRecognition = TestSpeechRecognition;
  window.SpeechRecognition = TestSpeechRecognition;
  window.webkitSpeechRecognition = undefined;
  window.emitIphoneSpeech = (chunks, resultIndex = 0, late = false, index = -1) => {
    const results = chunks.map(([transcript, isFinal]) => Object.assign([{transcript}], {isFinal}));
    const run = window.iphoneSpeech.runs.at(index);
    (late ? run.lateResult : run.onresult)?.({results, resultIndex});
  };
  // Emulates only the browser viewport signal; no OS keyboard is opened.
  const viewport = new EventTarget();
  let height = null, top = 0;
  Object.defineProperties(viewport, {
    width: {get: () => innerWidth}, height: {get: () => height ?? innerHeight},
    offsetTop: {get: () => top}, offsetLeft: {get: () => 0},
    pageTop: {get: () => scrollY + top}, pageLeft: {get: () => scrollX}, scale: {get: () => 1},
  });
  Object.defineProperty(window, 'visualViewport', {configurable: true, value: viewport});
  window.simulateIphoneKeyboard = (nextHeight = null, nextTop = 0) => {
    height = nextHeight; top = nextTop;
    viewport.dispatchEvent(new Event('resize')); viewport.dispatchEvent(new Event('scroll'));
  };
});
const sheet = () => page.locator('#sheet');
const textInput = () => sheet().locator('textarea[name=text]');
const ready = () => page.waitForFunction(() => document.querySelector('#app')?.getAttribute('aria-busy') === 'false');
const settle = () => page.evaluate(async () => {
  await document.fonts.ready;
  for(const dialog of document.querySelectorAll('dialog[open]'))await Promise.all(dialog.getAnimations().map(animation=>animation.finished));
  await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  for(const dialog of document.querySelectorAll('dialog[open]'))if(getComputedStyle(dialog).opacity!=='1')throw Error('Open sheet must be opaque before capture');
});
const close = async () => {
  if (await sheet().isVisible()) await sheet().getByRole('button', {name: 'Закрыть', exact: true}).click();
  await sheet().waitFor({state: 'hidden'}); await settle();
};
async function api(path, body) {
  const response = body === undefined ? await context.request.get(base + path)
    : await context.request.post(base + path, {data: body, headers: {'Idempotency-Key': crypto.randomUUID()}});
  assert.ok(response.ok(), `${path}: ${response.status()} ${await response.text()}`);
  return response.json();
}
async function poll(read, message, timeout = 20000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {const value = await read(); if (value) return value; await page.waitForTimeout(80);}
  assert.fail(message);
}
async function shot(name, {keyboard = false} = {}) {
  await settle();
  const width = page.viewportSize().width, file = `${artifacts}/owner-iphone-${name}-${width}.png`;
  await page.screenshot({path: file, fullPage: true});
  screenshots.push({file, width, automated: true, browser: 'Chromium mobile emulation',
    speech: 'installed deterministic stub', keyboard: keyboard ? 'visualViewport resize simulation' : 'not opened', physicalDeviceAcceptance: false});
}
async function modalLayout(label, primary = null) {
  await settle();
  assert.deepEqual(await sheet().evaluate(dialog => {
    const failures = [], visible = window.isVisibleOwnerControl, viewport = window.visualViewport;
    const top = viewport.offsetTop, bottom = top + viewport.height, d = dialog.getBoundingClientRect();
    if (getComputedStyle(dialog).opacity !== '1') failures.push('Sheet is translucent');
    if (d.left < -1 || d.right > innerWidth + 1 || d.top < top - 1 || d.bottom > bottom + 1) failures.push('Sheet outside current visual viewport');
    if (document.documentElement.scrollWidth > innerWidth + 1 || dialog.scrollWidth > dialog.clientWidth + 1) failures.push('Horizontal overflow');
    if (visible(document.querySelector('#owner-dock'))) failures.push('Dock remains visible behind modal');
    if (!dialog.contains(document.activeElement)) failures.push('Focus escaped sheet');
    for (const control of dialog.querySelectorAll('button,select,summary')) {
      if (!visible(control)) continue;
      const b = control.getBoundingClientRect(), name = control.getAttribute('aria-label') || control.innerText.trim();
      if (!name) failures.push('Unnamed visible control');
      if (b.width < 43.5 || b.height < 43.5) failures.push(`Touch target below 44px: ${name}`);
      if (b.left < -1 || b.right > innerWidth + 1) failures.push(`Control outside width: ${name}`);
    }
    if (dialog.scrollHeight > dialog.clientHeight + 1 && !/auto|scroll/.test(getComputedStyle(dialog).overflowY)) failures.push('Sheet cannot scroll');
    return failures;
  }), [], label);
  if (primary) {
    await primary.scrollIntoViewIfNeeded(); await settle();
    assert.equal(await primary.evaluate(button => {
      const b = button.getBoundingClientRect(), v = window.visualViewport;
      const header = document.querySelector('.sheet-header').getBoundingClientRect();
      const hit = document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2);
      return b.top >= header.bottom - 1 && b.bottom <= v.offsetTop + v.height + 1 && button.contains(hit);
    }), true, `${label}: final action reachable without header or dock occlusion`);
  }
  assert.equal(await sheet().locator('.close').evaluate(button => {
    const b = button.getBoundingClientRect(), v = window.visualViewport;
    return b.top >= v.offsetTop - 1 && b.bottom <= v.offsetTop + v.height + 1
      && button.contains(document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2));
  }), true, `${label}: sticky Close stays reachable after scrolling`);
}
async function keyboardLayout(label, field, primary) {
  await field.focus();
  await page.evaluate(() => window.simulateIphoneKeyboard(410, 26)); await settle();
  assert.equal(await field.evaluate(input => {
    const b = input.getBoundingClientRect(), v = window.visualViewport;
    return document.activeElement === input && b.bottom <= v.offsetTop + v.height + 1
      && b.top >= document.querySelector('.sheet-header').getBoundingClientRect().bottom - 1;
  }), true, `${label}: focused field remains above simulated keyboard`);
  await modalLayout(`${label}/keyboard-simulation`, primary);
  await shot(`${label}-keyboard-SIMULATED`, {keyboard: true});
  await page.evaluate(() => window.simulateIphoneKeyboard()); await settle();
}
const sourceHashes = new Map();
async function originalHash(path) {
  const response = await context.request.get(base + path);
  assert.ok(response.ok(), `Original retrieval: ${response.status()}`);
  return createHash('sha256').update(await response.body()).digest('hex');
}

try {
  const login = await context.request.post(base + '/preview/login', {data: {secret: process.env.PREVIEW_SECRET}});
  assert.ok(login.ok());
  const config = await api('/preview/config');
  assert.equal(config.environment, 'TEST_PREVIEW'); assert.equal(config.actual_operations, false);
  const prefix = `/v1/entities/${config.entity_id}`, owner = prefix + '/owner';
  const before = await api(owner + '/home'), beforeStock = await api(prefix + '/inventory'), beforeHarvest = await api(prefix + '/harvest');
  await page.goto(base); await navigateOwner(page, 'home');
  assert.equal(await page.evaluate(() => navigator.language), 'uk-UA');

  // The very first Voice click must start recognition; there is no second tap.
  await page.locator('[data-r2-mode=voice_transcript]').click();
  assert.deepEqual(await page.evaluate(() => window.iphoneSpeech.starts), [{sameClickStack: true, lang: 'ru-RU', continuous: true, interimResults: true}]);
  assert.equal(await sheet().locator('#r2-voice-state').getAttribute('data-state'), 'starting', 'Requesting microphone must not claim it is already listening');
  await page.evaluate(() => window.iphoneSpeech.runs.at(-1).onstart());
  assert.equal(await sheet().locator('#r2-voice-state').getAttribute('data-state'), 'listening');
  assert.equal(await sheet().locator('#r2-file').evaluate(node => window.isVisibleOwnerControl(node)), false);
  await page.evaluate(() => window.emitIphoneSpeech([['Полить', false]]));
  assert.equal(await textInput().inputValue(), 'Полить');
  await page.evaluate(() => window.emitIphoneSpeech([['Полить теплицу.', true], ['Проверить', false]], 1));
  assert.equal(await textInput().inputValue(), 'Полить теплицу. Проверить');
  await page.evaluate(() => window.emitIphoneSpeech([['Полить теплицу.', true], ['Проверить окна.', true]], 1));
  assert.equal(await textInput().inputValue(), 'Полить теплицу. Проверить окна.');
  await page.evaluate(() => window.emitIphoneSpeech([['Полить теплицу.', true], ['Проверить окна.', true]], 1));
  assert.equal(await textInput().inputValue(), 'Полить теплицу. Проверить окна.', 'Repeated cumulative final event must not duplicate transcript');
  await sheet().locator('#r2-voice-stop').click();
  assert.equal(await sheet().locator('#r2-voice-state').getAttribute('data-state'), 'finishing');
  assert.equal(await sheet().getByRole('button', {name: 'Сохранить источник', exact: true}).isDisabled(), true, 'Final results must settle before saving');
  await page.evaluate(() => {window.emitIphoneSpeech([['Полить теплицу.', true], ['Проверить окна.', true]], 1); window.iphoneSpeech.runs.at(-1).onend();});
  await page.evaluate(() => window.emitIphoneSpeech([['STALE AFTER STOP', true]], 0, true));
  assert.equal(await textInput().inputValue(), 'Полить теплицу. Проверить окна.');
  await sheet().locator('#r2-dictate').click();
  await page.evaluate(() => window.emitIphoneSpeech([['И записать результат.', true]]));
  assert.equal(await textInput().inputValue(), 'Полить теплицу. Проверить окна. И записать результат.');
  await textInput().fill('TEST · Вручную исправленная расшифровка');
  await page.evaluate(() => window.emitIphoneSpeech([['STALE AFTER MANUAL EDIT', true]], 0, true));
  assert.equal(await textInput().inputValue(), 'TEST · Вручную исправленная расшифровка');
  await sheet().locator('#r2-dictate').click();
  await sheet().locator('#r2-voice-done').click();
  assert.equal(await sheet().locator('#r2-voice-state').getAttribute('data-state'), 'finishing');
  await page.evaluate(() => window.iphoneSpeech.runs.at(-1).onend());
  assert.equal(await textInput().evaluate(input => document.activeElement === input), true, 'Done focuses editable transcript');
  await sheet().locator('#r2-dictate').click();
  await page.evaluate(() => window.emitIphoneSpeech([['TEST · Последняя фраза', false]]));
  await sheet().getByRole('button', {name: 'Сохранить источник', exact: true}).click();
  assert.equal(await sheet().locator('#r2-voice-state').getAttribute('data-state'), 'finishing');
  assert.equal(captureWrites.length, 0, 'Save during recognition first waits for its final result; it does not upload interim text');
  await page.evaluate(() => {window.emitIphoneSpeech([['TEST · Последняя фраза готова.', true]]); window.iphoneSpeech.runs.at(-1).onend();});
  assert.match(await textInput().inputValue(), /Последняя фраза готова\.$/);
  assert.equal(captureWrites.length, 0, 'Final transcript stays editable until a deliberate second Save');
  assert.equal(await sheet().getByRole('button', {name: 'Сохранить источник', exact: true}).isEnabled(), true);
  await shot('voice-stopped-SIMULATED'); await close();
  await page.locator('[data-r2-mode=text]').click(); await textInput().fill('TEST · Новый несохранённый источник');
  await page.evaluate(() => window.emitIphoneSpeech([['PRIVATE OLD VOICE', true]], 0, true));
  assert.equal(await textInput().inputValue(), 'TEST · Новый несохранённый источник', 'Closed recognition cannot overwrite reopened capture');
  await close(); await navigateOwner(page, 'more');
  await page.evaluate(() => window.emitIphoneSpeech([['PRIVATE NAVIGATION VOICE', true]], 0, true));
  assert.equal(await sheet().isVisible(), false); await waitOwnerRoute(page, 'more');
  await navigateOwner(page, 'home');

  for (const failure of ['unsupported', 'denied', 'throw']) {
    await page.evaluate(kind => {
      window.iphoneSpeech.failure = kind;
      window.SpeechRecognition = kind === 'unsupported' ? undefined : window.TestSpeechRecognition;
    }, failure);
    await page.locator('[data-r2-mode=voice_transcript]').click();
    await page.waitForFunction(() => document.activeElement === document.querySelector('#r2-source-form textarea'));
    assert.equal(await sheet().locator('#r2-voice-state').getAttribute('data-state'), 'fallback');
    assert.match(await sheet().locator('#r2-input-help').innerText(), /iPhone.*микрофон.*клавиатуре/);
    await textInput().fill(`TEST · ${failure} · текстовый запасной путь`);
    await shot(`voice-${failure}-SIMULATED`); await close();
  }
  await page.evaluate(() => {window.iphoneSpeech.failure = null; window.SpeechRecognition = window.TestSpeechRecognition;});

  // Switch real mode buttons; retain the exact same File and draft text.
  const tinyPng = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a7WQAAAAASUVORK5CYII=', 'base64');
  await page.locator('[data-r2-mode=photo]').click();
  assert.equal(await textInput().evaluate(node => window.isVisibleOwnerControl(node)), false, 'Photo caption is optional and collapsed');
  await sheet().locator('#r2-file').setInputFiles({name: 'TEST-iphone-source.png', mimeType: 'image/png', buffer: tinyPng});
  await sheet().locator('#r2-file-preview img').waitFor();
  await (await revealOwnerControl(textInput())).fill('TEST · Не потерять подпись и оригинальный файл');
  for (const mode of ['text', 'voice_transcript', 'photo']) {
    await sheet().locator(`[data-capture-mode=${mode}]`).click();
    assert.equal(await sheet().locator('#r2-source-form').getAttribute('data-capture-active'), mode);
    assert.equal(await sheet().locator('#r2-file').evaluate(input => input.files[0].name), 'TEST-iphone-source.png');
    assert.equal(await textInput().inputValue(), 'TEST · Не потерять подпись и оригинальный файл');
    assert.equal(await sheet().locator('#r2-file').evaluate(node => window.isVisibleOwnerControl(node)), mode === 'photo');
    assert.equal(await textInput().evaluate(node => window.isVisibleOwnerControl(node)), mode !== 'photo');
  }
  assert.ok((await page.evaluate(() => window.iphoneSpeech.starts)).every(start => start.sameClickStack), 'Every recognition start stays within its own click dispatch');
  await close();

  // Physical owner defect replay is separate from the historical language GOLD.
  // Actual capture/review controls, simulated transcript only; no physical claim.
  const physicalOwnerPhrase = 'Нужно переделать полив на Озвади на следующей неделе';
  const preparedRelativeTasks = [];
  const knownOzvadi = (await api(prefix + '/tasks')).areas.find(area => area.kind === 'greenhouse' && area.name === 'Özvadi');
  assert.ok(knownOzvadi, 'TEST preview has the current Özvadi reference');
  for (const mode of ['text', 'voice_transcript']) {
    await navigateOwner(page, 'home');
    await page.locator(`[data-r2-mode=${mode}]`).click();
    if (mode === 'voice_transcript') {
      await page.evaluate(raw => {
        window.iphoneSpeech.runs.at(-1).onstart();
        window.emitIphoneSpeech([[raw, true]]);
      }, physicalOwnerPhrase);
      await sheet().locator('#r2-voice-stop').click();
      await page.evaluate(() => window.iphoneSpeech.runs.at(-1).onend());
    } else await textInput().fill(physicalOwnerPhrase);
    assert.equal(await textInput().inputValue(), physicalOwnerPhrase);
    const savedResponse = page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname === owner + '/captures');
    await sheet().getByRole('button', {name: 'Сохранить источник', exact: true}).click();
    const saved = await (await savedResponse).json();
    await sheet().waitFor({state: 'hidden'});
    let proposal = await poll(async () => (await api(owner + '/confirmations')).items.find(item => item.inbox_id === saved.id), 'Relative irrigation proposal missing');
    assert.equal(proposal.action_type, 'PERSONAL_TASK');
    const facts = proposal.revision.payload;
    assert.equal(facts.category, 'irrigation');
    assert.equal(facts.greenhouse_id, knownOzvadi.id);
    assert.equal(facts.relative_time_context, 'следующая неделя');
    assert.equal(facts.planned_date, null); assert.equal(facts.worker_id, null);
    assert.equal(facts.original_instruction, physicalOwnerPhrase);
    assert.deepEqual(proposal.revision.missing, mode === 'text' ? [] : ['Возможный повтор источника: проверьте оригинал']);
    const originalPath = owner + `/captures/${saved.id}/original`;
    const hash = await originalHash(originalPath);
    assert.equal(hash, createHash('sha256').update(physicalOwnerPhrase, 'utf8').digest('hex'));
    sourceHashes.set(originalPath, hash);
    preparedRelativeTasks.push({mode, sourceId: saved.id, proposalId: proposal.id, payload: facts});
    await navigateOwner(page, 'review');
    await sheet().locator(`[data-proposal="${proposal.id}"] [data-r2-open]`).click();
    await sheet().locator('#r2-confirm').waitFor();
    for (const width of [320, 390, 768, 1280]) {
      await page.setViewportSize({width, height: 844});
      const primaryFacts = await sheet().locator('.owner-review-facts').innerText();
      assert.match(primaryFacts, /Özvadi/);
      assert.match(primaryFacts, /Полив/);
      assert.match(primaryFacts, /следующая неделя/i);
      assert.doesNotMatch(await sheet().locator('.owner-proposal').innerText(), /Нужно уточнить событие|Выберите точный ручной режим/);
      assert.equal(await sheet().locator('#r2-confirm').isEnabled(), mode === 'text', 'Only repeated-source acknowledgement blocks the duplicate voice card, never missing date or worker');
      await modalLayout(`${width}/relative-irrigation-${mode}`, sheet().locator('#r2-confirm'));
      await shot(`relative-irrigation-${mode}`);
    }
    await page.setViewportSize({width: 390, height: 844});
    await sheet().locator('#r2-edit').click();
    assert.equal(await sheet().locator('[name=planned_date]').inputValue(), '');
    assert.match(await sheet().locator('[data-task-fact-value=planned_date]').innerText(), /следующая неделя/i);
    assert.equal(await sheet().locator('[name=greenhouse_id]').inputValue(), knownOzvadi.id);
    // Saving without re-entering said facts must retain all prepared metadata.
    await sheet().getByRole('button', {name: 'Сохранить исправления', exact: true}).click();
    await sheet().locator('#r2-confirm').waitFor();
    proposal = await api(owner + `/confirmations/${proposal.id}/preview`);
    assert.equal(proposal.revision.payload.relative_time_context, facts.relative_time_context);
    assert.equal(proposal.revision.payload.category, facts.category);
    assert.equal(proposal.revision.payload.original_instruction, physicalOwnerPhrase);
    assert.equal(proposal.revision.payload.planned_date, null);
    if (mode === 'text') {
      await sheet().locator('#r2-confirm').click();
      await poll(async () => (await api(owner + `/confirmations/${proposal.id}/preview`)).state === 'POSTED', 'Relative personal task was not confirmed');
      await close(); await navigateOwner(page, 'home');
      const homeTask = (await api(owner + '/home')).tasks.find(task => task.proposal_id === proposal.id);
      assert.ok(homeTask); assert.equal(homeTask.planned_date, null);
      assert.equal(homeTask.relative_time_context, 'следующая неделя');
      const shownTask = page.locator('.owner-task').filter({has: page.locator(`[data-owner-done="${homeTask.id}"]`)});
      assert.match(await shownTask.innerText(), /следующая неделя/i);
      assert.doesNotMatch(await shownTask.innerText(), /Без даты/);
      await shot('relative-irrigation-confirmed-home');
    } else await close();
  }
  assert.deepEqual(preparedRelativeTasks[0].payload, preparedRelativeTasks[1].payload, 'TEXT and VOICE share identical preparation');
  fixtures.physicalOwnerRelativeTask = {cases: preparedRelativeTasks, originalGoldChanged: false, physicalIphoneAcceptance: false};

  // Store one original through the real API but never confirm its proposal.
  const longText = ('Сегодня полить İmamlı. Сегодня полить İmamlı. TEST · Проверить давление в дальней теплице, осмотреть все соединения и записать показания после полива. ' +
    'Сверить каждую строку с исходным поручением владельца и сохранить подробный результат без сокращений. '.repeat(4)).trim();
  await page.locator('[data-r2-mode=text]').click(); await textInput().fill(longText);
  await sheet().getByRole('button', {name: 'Сохранить источник', exact: true}).click(); await sheet().waitFor({state: 'hidden'});
  const source = await poll(async () => (await api(owner + '/captures')).items.find(item => item.text === longText), 'Long source was not saved');
  fixtures.source = source.id;
  const sourceOriginal = owner + `/captures/${source.id}/original`;
  sourceHashes.set(sourceOriginal, await originalHash(sourceOriginal));
  const duplicateMarker = 'Сегодня полить İmamlı.';
  let proposal = await poll(async () => intendedTask((await api(owner + '/confirmations')).items, source.id, duplicateMarker), 'Intended long source task missing');
  const preparedTasks = sourceTasks((await api(owner + '/confirmations')).items, source.id);
  assert.equal(preparedTasks.filter(p => p.revision.payload.original_instruction.startsWith('Сегодня полить')).length, 2, 'Both repeated source task slots survive');
  assert.equal(preparedTasks.filter(p => p.revision.payload.original_instruction.includes('Проверить давление')).length, 1, 'Pressure instruction survives independently');
  const pressure = preparedTasks.find(p => p.revision.payload.original_instruction.includes('Проверить давление'));
  assert.equal(pressure.revision.payload.worker_id ?? null, null, 'A generic TEST marker never identifies the synthetic worker');
  assert.equal(pressure.revision.payload.language ?? null, null, 'Unknown worker identity never supplies a worker language');
  assert.equal(preparedTasks.filter(p => p.revision.payload.original_instruction.startsWith('Сверить каждую строку')).length, 4, 'Every explicit sibling instruction survives');
  // Isolate summary rendering from parser slot ordering. The normal TEST
  // revision path provides a repeated long title; source text stays immutable.
  const reviewTitle = longText.slice(0, 200);
  assert.equal(reviewTitle.split(duplicateMarker).length - 1, 2);
  proposal = await api(owner + `/confirmations/${proposal.id}/revise`, {expected_version: proposal.version,
    payload: {...proposal.revision.payload, title: reviewTitle}});
  assert.equal(proposal.revision.payload.title, reviewTitle);
  const sourceTaskSnapshots = taskSnapshots((await api(owner + '/confirmations')).items, source.id);
  fixtures.proposal = proposal.id;
  const proposalBefore = await api(owner + `/confirmations/${proposal.id}/preview`);
  const openReview = async () => {
    await navigateOwner(page, 'review');
    await sheet().locator(`[data-proposal="${proposal.id}"] [data-r2-open]`).click();
    await sheet().locator('#r2-confirm').waitFor();
  };

  // A compact task genuinely saves through the common path without opening
  // Additional or making unsupported accounting/product assumptions.
  const taskInstruction = 'TEST · Проверить полив и записать давление';
  await navigateOwner(page, 'tasks'); await page.locator('#task-new').click();
  const primaryControls = await sheet().locator('#task-form input,#task-form select,#task-form textarea')
    .evaluateAll(nodes => nodes.filter(window.isVisibleOwnerControl).map(node => node.id));
  assert.deepEqual(primaryControls, ['task-description', 'task-greenhouse']);
  assert.equal(await sheet().locator('.task-advanced').evaluate(node => node.open), false);
  await sheet().locator('#task-description').fill(taskInstruction);
  const areas = (await api(prefix + '/tasks')).areas.filter(area => area.kind === 'greenhouse');
  assert.ok(areas.length, 'Synthetic entity has real greenhouse references');
  await sheet().locator('#task-greenhouse').selectOption(areas[0].id);
  await sheet().getByRole('button', {name: 'Создать черновик задачи', exact: true}).click();
  await sheet().getByRole('heading', {name: taskInstruction, exact: true}).waitFor();
  const task = (await api(prefix + '/tasks')).tasks.find(item => item.title === taskInstruction);
  assert.ok(task); fixtures.task = task.id;
  const taskDetail = await api(prefix + '/tasks/' + task.id);
  assert.equal(taskDetail.original_instruction, taskInstruction);
  assert.equal(taskDetail.status, 'draft'); assert.equal(taskDetail.greenhouse_id, areas[0].id);
  assert.equal(taskDetail.planned_date, null); assert.equal(taskDetail.priority, 'normal');
  assert.equal(await sheet().locator('#task-assign').evaluate(node => window.isVisibleOwnerControl(node)), true);
  assert.equal(await sheet().locator('#task-link').evaluate(node => window.isVisibleOwnerControl(node)), false, 'Rare actual-event linkage stays under Additional');
  assert.equal(await sheet().locator('#task-repeat').evaluate(node => window.isVisibleOwnerControl(node)), false, 'Recurrence stays under Additional');
  await (await revealOwnerControl(sheet().locator('#task-link'))).waitFor({state: 'visible'});
  assert.equal(await sheet().locator('#task-link').evaluate(node => window.isVisibleOwnerControl(node)), true, 'Rare linkage remains accessible through real disclosure');
  await shot('ordinary-task-additional');
  await close();

  for (const width of [320, 390, 768, 1280]) {
    await page.setViewportSize({width, height: 844});
    await navigateOwner(page, 'home');
    for (const mode of ['text', 'photo', 'voice_transcript']) {
      await page.locator(`[data-r2-mode=${mode}]`).click();
      if (mode === 'text') await textInput().fill('TEST · Длинная русская запись о проверке давления после полива в дальней теплице. '.repeat(5));
      if (mode === 'voice_transcript') await page.evaluate(() => window.iphoneSpeech.runs.at(-1).onstart());
      if (mode === 'photo') await sheet().locator('#r2-file').setInputFiles({name: 'TEST-документ-с-длинным-русским-названием.png', mimeType: 'image/png', buffer: tinyPng});
      const primary = sheet().getByRole('button', {name: 'Сохранить источник', exact: true});
      await modalLayout(`${width}/capture-${mode}`, primary); await shot(`capture-${mode}-SIMULATED`);
      if (width === 390 && mode === 'text') await keyboardLayout('capture-text', textInput(), primary);
      await close();
    }
    await openReview();
    const primaryText = await sheet().locator('.owner-proposal').innerText();
    assert.equal(primaryText.split(duplicateMarker).length - 1, 1, 'Primary review states the long instruction only once');
    assert.equal(await sheet().locator('.source-text').evaluate(node => window.isVisibleOwnerControl(node)), false, 'Full original remains under disclosure');
    await modalLayout(`${width}/long-review`, sheet().locator('#r2-reject')); await shot('long-review');
    await sheet().getByText('Оригинальный источник', {exact: true}).click();
    assert.equal(await sheet().locator('.source-text').innerText(), longText, 'Original remains complete and exact under disclosure');
    await modalLayout(`${width}/original-disclosed`, sheet().locator('#r2-reject'));
    await sheet().locator('#r2-edit').click();
    assert.equal(await sheet().locator('.task-extra').evaluate(node => node.open), false, 'R2 advanced task fields start collapsed');
    assert.equal(await sheet().locator('[data-task-fact=planned_date]').count(), 1);
    await (await revealOwnerControl(sheet().locator('[name=planned_date]'))).fill('2026-10-15');
    await sheet().locator('[name=planned_date]').dispatchEvent('change');
    assert.match(await sheet().locator('[data-task-fact-value=planned_date]').innerText(), /15.*окт.*2026/);
    await (await revealOwnerControl(sheet().locator('[name=greenhouse_id]'))).selectOption(areas[0].id);
    assert.equal(await sheet().locator('[name=greenhouse_id]').inputValue(), areas[0].id);
    await modalLayout(`${width}/R2-task-compact-editor`, sheet().getByRole('button', {name: 'Сохранить исправления', exact: true}));
    await shot('R2-task-compact-editor');
    await close();
    await navigateOwner(page, 'tasks'); await page.locator('#task-new').click();
    await sheet().locator('#task-description').fill('TEST · Проверить давление в самой дальней части теплицы и записать подробный результат');
    await sheet().locator('#task-greenhouse').selectOption(areas[0].id);
    assert.equal(await sheet().locator('#task-greenhouse').evaluate(node => node.tagName), 'SELECT');
    assert.equal(await sheet().locator('#task-greenhouse').inputValue(), areas[0].id, 'Native select retains explicitly selected greenhouse');
    const primary = sheet().getByRole('button', {name: 'Создать черновик задачи', exact: true});
    await modalLayout(`${width}/task-common`, primary); await shot('task-common');
    if (width === 390) {
      await keyboardLayout('task-common', sheet().locator('#task-description'), primary);
      await keyboardLayout('task-greenhouse-selector', sheet().locator('#task-greenhouse'), primary);
    }
    await (await revealOwnerControl(sheet().locator('#task-planned'))).fill('2026-10-15');
    await sheet().locator('#task-planned').dispatchEvent('change');
    const ruDate = await page.evaluate(() => new Intl.DateTimeFormat('ru-RU', {timeZone: 'UTC', day: 'numeric', month: 'short', year: 'numeric'}).format(new Date('2026-10-15T12:00:00Z')));
    assert.equal(await sheet().locator('#task-planned-summary').innerText(), `Плановая дата · ${ruDate}`);
    assert.match(ruDate, /окт/); assert.doesNotMatch(await sheet().locator('#task-planned-summary').innerText(), /жовт/);
    assert.equal(await sheet().locator('#task-planned').getAttribute('type'), 'date');
    assert.equal(await sheet().locator('#task-planned').inputValue(), '2026-10-15', 'Display localization never changes date payload');
    await (await revealOwnerControl(sheet().locator('#task-due'))).fill('2026-10-15T14:30');
    await sheet().locator('#task-due').dispatchEvent('change');
    assert.match(await sheet().locator('#task-due-summary').innerText(), /окт.*14:30/);
    await modalLayout(`${width}/task-additional`, primary); await shot('task-additional-native-dates');
    await close();
    assert.equal(await page.locator('#owner-dock').evaluate(node => window.isVisibleOwnerControl(node)), true, 'Dock returns after modal dismissal');
    await page.evaluate(() => scrollTo(0, document.documentElement.scrollHeight));
    assert.equal(await page.evaluate(() => {
      const controls = [...document.querySelectorAll('#app button,#app summary')].filter(window.isVisibleOwnerControl);
      return controls.at(-1).getBoundingClientRect().bottom <= document.querySelector('#owner-dock').getBoundingClientRect().top + 1;
    }), true, `${width}: dock does not hide the last task control`);
  }

  // General assignment coverage runs even when authored R2 fixtures are off.
  await page.setViewportSize({width: 390, height: 844});
  const workerA = await api(prefix + '/workers', {display_name: 'TEST · Длинное имя работника для проверки выбора языка и назначения', language: 'fa', source: 'test:iphone-ui'});
  const workerB = await api(prefix + '/workers', {display_name: 'TEST · Другой работник на том же языке', language: 'fa', source: 'test:iphone-ui'});
  fixtures.workers = [workerA.id, workerB.id];
  await navigateOwner(page, 'tasks');
  const openTask = async id => {
    await page.locator(`[data-task="${id}"]`).first().click();
    await sheet().locator('#task-assign').waitFor();
  };
  await openTask(task.id); await sheet().locator('#task-assign').click();
  assert.equal(await sheet().locator('input[type=checkbox]').count(), 0, 'Assign itself is the explicit review action; no extra checkbox');
  const assignmentButton = () => sheet().locator('#task-assign-form button.primary');
  await sheet().locator('#assign-worker').selectOption(workerA.id);
  assert.equal(await assignmentButton().isDisabled(), true, 'Missing translation never silently relabels the Russian original');
  assert.equal(await sheet().locator('#assign-worker').evaluate(node => node.tagName), 'SELECT');
  assert.equal(await sheet().locator('#assign-text').inputValue(), '');
  await sheet().locator('#assign-text').fill(taskInstruction);
  assert.equal(await assignmentButton().isDisabled(), true, 'Unchanged original is not a translated assignment');
  const manualText = 'TEST · متن آزمایشی';
  await sheet().locator('#assign-text').fill(manualText);
  assert.equal(await assignmentButton().isEnabled(), true);
  await sheet().locator('#assign-worker').selectOption(workerB.id);
  assert.equal(await sheet().locator('#assign-text').inputValue(), '', 'Even same-language worker change clears manually reviewed text');
  assert.equal(await assignmentButton().isDisabled(), true);
  await sheet().locator('#assign-worker').selectOption(workerA.id);
  await sheet().locator('#assign-text').fill(manualText);
  await keyboardLayout('assignment-manual', sheet().locator('#assign-text'), assignmentButton());
  await (await revealOwnerControl(sheet().locator('#assign-use-original'))).click();
  assert.match(await sheet().locator('#assign-original-state').innerText(), /Русский.*не перевод/);
  assert.equal(await assignmentButton().isEnabled(), true);
  await modalLayout('assignment-explicit-original', assignmentButton()); await shot('assignment-explicit-original');
  // Hold a successful response after commit, close, and open a new editor.
  // A valid old mutation is allowed to finish, but cannot hijack the new view.
  let assignmentEntered, releaseAssignment;
  const assignmentHeld = new Promise(resolve => {assignmentEntered = resolve;});
  const assignmentGate = new Promise(resolve => {releaseAssignment = resolve;});
  const assignmentPattern = `**/tasks/${task.id}/assignments`;
  await page.route(assignmentPattern, async route => {
    const response = await route.fetch(); assignmentEntered(); await assignmentGate;
    await route.fulfill({response});
  }, {times: 1});
  const assignedResponse = page.waitForResponse(response => response.url().endsWith(`/tasks/${task.id}/assignments`));
  await assignmentButton().click(); await assignmentHeld;
  await close(); await page.locator('#task-new').click();
  await sheet().locator('#task-description').fill('TEST · Новый черновик после позднего назначения');
  releaseAssignment(); await (await assignedResponse).finished(); await settle();
  assert.equal(await sheet().locator('#task-description').inputValue(), 'TEST · Новый черновик после позднего назначения');
  assert.equal(await sheet().locator('#sheet-title').innerText(), 'Новая задача · черновик');
  await page.unroute(assignmentPattern); await close();
  const assigned = await api(prefix + '/tasks/' + task.id);
  assert.equal(assigned.original_instruction, taskInstruction, 'Assignment never overwrites original task');
  assert.equal(assigned.assignments.length, 1);
  assert.equal(assigned.assignments[0].worker_id, workerA.id);
  assert.equal(assigned.assignments[0].language, 'ru', 'Explicit original retains the actual owner language');
  assert.equal(assigned.assignments[0].rendered_text, taskInstruction);
  assert.equal(assigned.assignments[0].translation_source, 'original');
  assert.equal(assigned.assignments[0].rendering_reviewed, true);

  // Delayed task detail must not replace a newer editor or a newer route.
  for (const destination of ['new-editor', 'home']) {
    await navigateOwner(page, 'tasks');
    let entered, release;
    const held = new Promise(resolve => {entered = resolve;}), gate = new Promise(resolve => {release = resolve;});
    const pattern = `**/tasks/${task.id}`;
    await page.route(pattern, async route => {const response = await route.fetch(); entered(); await gate; await route.fulfill({response});}, {times: 1});
    const response = page.waitForResponse(result => result.url().endsWith(`/tasks/${task.id}`));
    await page.locator(`[data-task="${task.id}"]`).first().click(); await held;
    if (destination === 'new-editor') {
      await page.locator('#task-new').click(); await sheet().locator('#task-description').fill('TEST · Более новый редактор');
    } else await navigateOwner(page, 'home');
    release(); await (await response).finished(); await settle(); await page.unroute(pattern);
    if (destination === 'new-editor') {
      assert.equal(await sheet().locator('#task-description').inputValue(), 'TEST · Более новый редактор'); await close();
    } else {assert.equal(await sheet().isVisible(), false); await waitOwnerRoute(page, 'home');}
  }

  if (config.r2_fixtures) {
    const fixture = config.worker_assignment_fixture;
    assert.ok(fixture?.worker_id && fixture.task_id && fixture.source_id, 'R2 preview must expose its guarded authored assignment example');
    assert.equal(fixture.translation_scope, 'ONE_AUTHORED_TEST_EXAMPLE'); assert.equal(fixture.real_dispatch, false);
    fixtures.authoredWorkerAssignment = fixture;
    const detail = await api(prefix + '/tasks/' + fixture.task_id);
    const original = 'TEST Muhammed, проверьте трубу в İmamlı 2026-10-10.';
    const translation = 'TEST Muhammed، لوله را در گلخانهٔ İmamlı در تاریخ ۲۰۲۶-۱۰-۱۰ بررسی کنید.';
    assert.equal(detail.status, 'draft'); assert.equal(detail.assignments.length, 0);
    assert.equal(detail.original_instruction, original);
    assert.deepEqual(detail.prepared_assignment, {worker_id: fixture.worker_id, language: 'fa', rendered_text: translation});
    const fixtureWorker = (await api(prefix + '/tasks')).workers.find(worker => worker.id === fixture.worker_id);
    assert.equal(fixtureWorker.display_name, 'TEST Muhammed'); assert.equal(fixtureWorker.language, 'fa');
    assert.equal(fixtureWorker.contact, null); assert.equal(fixtureWorker.telegram_ref, null);
    const fixtureOriginal = owner + `/captures/${fixture.source_id}/original`;
    sourceHashes.set(fixtureOriginal, await originalHash(fixtureOriginal));
    for (const width of [320, 390, 768, 1280]) {
      await page.setViewportSize({width, height: 844}); await navigateOwner(page, 'tasks');
      await openTask(fixture.task_id); await sheet().locator('#task-assign').click();
      assert.equal(await sheet().locator('#assign-worker').inputValue(), fixture.worker_id, 'Prepared assignment selects only its exact authored worker');
      assert.equal(await sheet().locator('#assign-prepared-text').innerText(), translation);
      assert.equal(await sheet().locator('.task-assignment-copy blockquote').innerText(), original);
      assert.equal(await sheet().locator('#assign-manual').evaluate(node => node.open), false, 'Prepared common path needs no manual translation entry');
      assert.equal(await sheet().locator('input[type=checkbox]').count(), 0);
      assert.equal(await assignmentButton().isEnabled(), true);
      await modalLayout(`${width}/TEST-prepared-assignment`, assignmentButton()); await shot('TEST-authored-prepared-assignment');
      await sheet().locator('#assign-worker').selectOption(workerB.id);
      assert.equal(await sheet().locator('#assign-prepared-text').count(), 0, 'Prepared text never transfers to another same-language worker');
      assert.equal(await sheet().locator('#assign-text').inputValue(), '');
      assert.equal(await assignmentButton().isDisabled(), true);
      await close();
    }
    // Presentation-only mismatch: show a stale prepared language from a GET.
    // No mutation is allowed while this scoped response override is installed.
    const fixturePattern = `**/tasks/${fixture.task_id}`;
    await page.route(fixturePattern, async route => {
      const response = await route.fetch(), real = await response.json();
      await route.fulfill({response, json: {...real, prepared_assignment: {...real.prepared_assignment, language: 'tr'}}});
    }, {times: 1});
    await page.setViewportSize({width: 390, height: 844}); await navigateOwner(page, 'tasks');
    await openTask(fixture.task_id); await sheet().locator('#task-assign').click();
    assert.equal(await sheet().locator('#assign-prepared-text').count(), 0, 'Prepared language mismatch cannot display or submit stale text');
    assert.equal(await sheet().locator('#assign-text').inputValue(), '');
    assert.equal(await assignmentButton().isDisabled(), true); await shot('TEST-stale-prepared-language-SIMULATED');
    await close(); await page.unroute(fixturePattern);
    assert.deepEqual((await api(prefix + '/tasks/' + fixture.task_id)).prepared_assignment, detail.prepared_assignment, 'Presentation mismatch fixture never changes real prepared assignment');
    await openTask(fixture.task_id); await sheet().locator('#task-assign').click();
    await assignmentButton().click(); await sheet().getByRole('heading', {name: 'Кому', exact: true}).waitFor();
    const delivered = await api(prefix + '/tasks/' + fixture.task_id);
    assert.equal(delivered.assignments.length, 1);
    assert.equal(delivered.assignments[0].worker_id, fixture.worker_id);
    assert.equal(delivered.assignments[0].language, 'fa');
    assert.equal(delivered.assignments[0].rendered_text, translation);
    assert.equal(delivered.assignments[0].rendering_reviewed, true);
    assert.equal(delivered.original_instruction, original);
    const session = await api(`/preview/workers/${fixture.worker_id}/session`, {});
    const workerContext = await browser.newContext({viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true});
    try {
      const workerPage = await workerContext.newPage(); workerPage.on('pageerror', error => errors.push(error.message));
      await workerPage.goto(base + '/worker');
      await workerPage.getByLabel('Ключ работника / Worker key').fill(session.token);
      await workerPage.getByRole('button', {name: 'Открыть задачи', exact: true}).click();
      await workerPage.locator(`[data-worker-task="${fixture.task_id}"]`).waitFor();
      assert.equal(await workerPage.locator('[data-worker-task]').count(), 1, 'Only the authored TEST assignment is visible to this worker');
      assert.equal(await workerPage.locator(`[data-worker-task="${task.id}"]`).count(), 0, 'Other workers’ tasks remain private');
      await workerPage.locator(`[data-worker-task="${fixture.task_id}"]`).click();
      await workerPage.locator('#worker-app > blockquote').filter({hasText: translation}).waitFor();
      assert.equal((await workerContext.request.get(base + prefix + '/finance-overview')).status(), 401, 'Worker cannot read owner Finance');
      const file = `${artifacts}/owner-iphone-TEST-authored-worker-page-390.png`;
      await workerPage.screenshot({path: file, fullPage: true}); screenshots.push({file, width: 390, automated: true, fixture: 'ONE_AUTHORED_TEST_EXAMPLE', realDispatch: false, physicalDeviceAcceptance: false});
    } finally {await workerContext.close();}
    await close();
  } else {
    assert.deepEqual(config.worker_assignment_fixture, {}, 'Disabled authored fixture has no worker, task, source or prepared rendering metadata');
    fixtures.authoredWorkerAssignment = {skipped: true, reason: 'r2_fixtures disabled; general assignment coverage ran'};
  }


  const proposalAfter = await api(owner + `/confirmations/${proposal.id}/preview`);
  assert.deepEqual(taskSnapshots((await api(owner + '/confirmations')).items, source.id), sourceTaskSnapshots, 'All source task siblings and payloads survive layout/disclosure checks unchanged');
  assert.equal(proposalAfter.version, proposalBefore.version, 'Layout/disclosure checks never revise a proposal');
  assert.equal(proposalAfter.state, proposalBefore.state, 'Layout/disclosure checks never authorize posting');
  for (const [path, hash] of sourceHashes) assert.equal(await originalHash(path), hash, 'Original source bytes are immutable');
  assert.deepEqual((await api(owner + '/home')).finance.balances, before.finance.balances, 'Capture/task/assignment UI never moves Finance');
  assert.deepEqual((await api(prefix + '/inventory')).stock, beforeStock.stock, 'Capture/task/assignment UI never moves stock');
  assert.equal((await api(prefix + '/harvest')).reports.length, beforeHarvest.reports.length, 'Capture/task/assignment UI never fabricates harvest');
  assert.deepEqual(await page.evaluate(() => window.iphoneUiFaults), []);
  assert.deepEqual(errors, []);
  await fs.writeFile(`${artifacts}/owner-iphone-ui.json`, JSON.stringify({ok: true, browser: 'Chromium mobile emulation', locale: 'uk-UA',
    datePayload: '2026-10-15', widths: [320, 390, 768, 1280], speech: 'deterministic SpeechRecognition stub',
    keyboard: 'visualViewport resize simulation', physicalIphoneAcceptance: false, nativeSafariMicrophoneAcceptance: false,
    screenshots, fixtures, immutableOriginalSources: true, financeUnchanged: true, stockUnchanged: true, harvestUnchanged: true, errors}, null, 2));
  console.log('PASS automated iPhone UX regression: one-tap speech stack/fallback/transcript fencing, mode preservation, compact tasks, Russian dates under uk-UA, native selectors, sticky modal controls, simulated keyboard, 320/390/768/1280, unchanged independent domains and original sources. Physical iPhone/Safari acceptance remains separate.');
} catch (error) {
  await fs.writeFile(`${artifacts}/owner-iphone-failure.html`, await page.content());
  await fs.writeFile(`${artifacts}/owner-iphone-failure-console.json`, JSON.stringify({message: String(error), errors}, null, 2));
  await page.screenshot({path: `${artifacts}/owner-iphone-failure.png`, fullPage: true}).catch(() => {});
  await fs.writeFile(`${artifacts}/owner-iphone-ui-failure.json`, JSON.stringify({ok: false, error: String(error),
    automatedSimulationOnly: true, physicalIphoneAcceptance: false, screenshots, errors}, null, 2));
  throw error;
} finally {await browser.close();}
