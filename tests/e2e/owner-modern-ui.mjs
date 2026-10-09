// Visual + behavioral regression gate for the source-first owner shell.
// Run only through the disposable preview runner. No business posting or live
// integration is performed. Image/PDF pixels use the existing TEST OCR corpus.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {chromium} from 'playwright';
import {navigateOwner, waitOwnerRoute, ownerRoutes, revealOwnerControl} from './navigation.mjs';
import {isVisibleOwnerControl} from './visibility.mjs';

const base = process.env.PREVIEW_URL || 'http://127.0.0.1:4173';
assert.ok(['127.0.0.1', 'localhost', '[::1]'].includes(new URL(base).hostname), 'Disposable local preview only');
assert.ok(process.env.PREVIEW_SECRET, 'The guarded preview runner must supply authentication');
const artifacts = process.env.E2E_ARTIFACTS || 'artifacts';
await fs.mkdir(artifacts, {recursive: true});
const browser = await chromium.launch({headless: true, args: ['--no-sandbox'],
  ...(process.env.CHROME_PATH ? {executablePath: process.env.CHROME_PATH} : {})});
const context = await browser.newContext({viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true});
const page = await context.newPage();
const errors = [], screenshots = [], fixtures = {};
let expectedUnavailable = false;
page.on('pageerror', error => errors.push(error.message));
page.on('console', message => {
  if (message.type() !== 'error') return;
  if (expectedUnavailable && /status of 503/.test(message.text())) return;
  errors.push(message.text());
});
await page.addInitScript(`window.isVisibleOwnerControl = (${isVisibleOwnerControl.toString()});`);
await page.addInitScript(() => {
  window.ownerUiFaults = [];
  new MutationObserver(() => {
    const text = document.querySelector('#notice')?.textContent || '';
    if (/TypeError|Cannot read properties|undefined is not|ReferenceError/.test(text)) window.ownerUiFaults.push(text);
  }).observe(document, {childList: true, subtree: true, characterData: true});
});
const sheet = () => page.locator('#sheet');
const ready = () => page.waitForFunction(() => document.querySelector('#app')?.getAttribute('aria-busy') === 'false');
const close = async () => {
  if (await sheet().isVisible()) await sheet().getByRole('button', {name: 'Закрыть', exact: true}).click();
  await sheet().waitFor({state: 'hidden'});
};
async function screenshot(name, width) {
  await page.evaluate(async () => {await document.fonts.ready; await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));});
  const file = `${artifacts}/owner-modern-${name}-${width}.png`;
  await page.screenshot({path: file, fullPage: true});
  screenshots.push(file);
}
async function checkedApi(path, body) {
  const response = body === undefined ? await context.request.get(base + path)
    : await context.request.post(base + path, {data: body, headers: {'Idempotency-Key': crypto.randomUUID()}});
  assert.ok(response.ok(), `${path}: ${response.status()} ${await response.text()}`);
  return response.json();
}
async function poll(read, message, timeout = 20000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {const value = await read(); if (value) return value; await page.waitForTimeout(100);}
  assert.fail(message);
}
async function layout(label) {
  await ready();
  await page.evaluate(async () => {await document.fonts.ready; await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));});
  const failures = await page.evaluate(() => {
    const failures = [], dock = document.querySelector('#owner-dock'), nav = document.querySelector('#nav'), capture = document.querySelector('#owner-capture-bar');
    const box = element => element.getBoundingClientRect();
    const visible = window.isVisibleOwnerControl;
    const describe = element => JSON.stringify({id: element.id || null, html: element.outerHTML.slice(0,700)});
    if (document.documentElement.scrollWidth > innerWidth + 1) failures.push('Horizontal page overflow');
    if (!dock?.contains(nav) || !dock?.contains(capture)) failures.push('Nav and capture must share the measured owner dock');
    if (!dock || !visible(dock) || !capture || !visible(capture)) return [...failures, 'Persistent owner dock missing'];
    const d = box(dock), n = box(nav), c = box(capture);
    if (d.left < -1 || d.right > innerWidth + 1 || d.bottom > innerHeight + 1) failures.push('Dock leaves the visible viewport');
    if (Math.min(n.bottom, c.bottom) - Math.max(n.top, c.top) > 1) failures.push('Primary navigation overlaps capture actions');
    for (const button of document.querySelectorAll('#nav button, #owner-capture-bar button, #app button')) {
      if (!visible(button)) continue;
      const b = box(button), name = button.getAttribute('aria-label') || button.innerText.trim();
      if (!name) failures.push(`Unnamed visible button: ${describe(button)}`);
      if (b.width < 43.5 || b.height < 43.5) failures.push(`Touch target below 44px: ${name}; ${describe(button)}`);
      if (b.left < -1 || b.right > innerWidth + 1) failures.push(`Control leaves viewport: ${name}; ${describe(button)}`);
    }
    return failures;
  });
  assert.deepEqual(failures, [], label);
  // At the end of the document, the last main-content control must remain
  // reachable above the combined capture + primary-navigation dock.
  await page.evaluate(() => scrollTo(0, document.documentElement.scrollHeight));
  assert.equal(await page.evaluate(() => {
    const controls = [...document.querySelectorAll('#app button, #app a, #app summary')]
      .filter(window.isVisibleOwnerControl);
    return !controls.length || controls.at(-1).getBoundingClientRect().bottom <= document.querySelector('#owner-dock').getBoundingClientRect().top + 1;
  }), true, `${label}: dock obscures the last main control`);
  await page.evaluate(() => scrollTo(0, 0));
}
async function dialogLayout(label) {
  await sheet().evaluate(async dialog => {await Promise.all(dialog.getAnimations().map(animation => animation.finished));});
  assert.deepEqual(await sheet().evaluate(dialog => {
    const b = dialog.getBoundingClientRect(), failures = [];
    if (b.left < -1 || b.right > innerWidth + 1 || b.top < -1 || b.bottom > innerHeight + 1) failures.push('Dialog outside viewport');
    if (dialog.scrollWidth > dialog.clientWidth + 1) failures.push('Horizontal dialog overflow');
    const content = dialog.querySelector('#sheet-content');
    if (content && content.scrollWidth > content.clientWidth + 1) failures.push('Horizontal dialog content overflow');
    const close = content?.querySelector('.close');
    if (close) {const c = close.getBoundingClientRect(), area = content.getBoundingClientRect(); if (c.left < area.left - 1 || c.right > area.right + 1) failures.push('Close target leaves dialog content');}
    if (dialog.scrollHeight > dialog.clientHeight + 1 && !/auto|scroll/.test(getComputedStyle(dialog).overflowY)) failures.push('Tall dialog cannot scroll');
    if (!dialog.contains(document.activeElement)) failures.push('Focus escaped the modal');
    for (const button of dialog.querySelectorAll('button')) {
      if (!window.isVisibleOwnerControl(button)) continue;
      const r = button.getBoundingClientRect(), name = button.getAttribute('aria-label') || button.innerText.trim();
      const description = JSON.stringify({id: button.id || null, html: button.outerHTML.slice(0,700)});
      if (!name) failures.push(`Unnamed dialog button: ${description}`);
      if (r.height < 43.5 || r.width < 43.5) failures.push(`Dialog target below 44px: ${name}; ${description}`);
    }
    return failures;
  }), [], label);
}

try {
  const login = await context.request.post(base + '/preview/login', {data: {secret: process.env.PREVIEW_SECRET}});
  assert.ok(login.ok());
  const config = await checkedApi('/preview/config'), prefix = `/v1/entities/${config.entity_id}`, owner = prefix + '/owner';
  const before = await checkedApi(owner + '/home'), initialStock = (await checkedApi(prefix + '/inventory')).stock;
  const day = before.finance.as_of_date;
  // Clearly labelled, non-posting fixtures make empty fresh-R2 and full runs
  // equally reviewable. They remain confined to the reset disposable database.
  fixtures.task = await checkedApi(prefix + '/tasks', {
    title: 'TEST · Проверить полив в İmamlı', original_instruction: 'TEST · Проверить давление и записать показания после полива.',
    description: 'TEST · Проверить давление и записать показания после полива.', planned_date: day,
    source: 'test:modern-ui', locator: 'modern-ui-task-' + crypto.randomUUID(),
  });
  fixtures.harvest = await checkedApi(prefix + '/harvest/reports', {
    event_date: day, original_greenhouse: 'TEST · İmamlı', cut_count: 55, reporter: 'TEST · Работник',
    notes: 'TEST · Полевой отчёт, ещё не продажа.',
    evidence: {source: 'test:modern-ui', raw_text: 'TEST · Сегодня срезали 55 деревьев в İmamlı.'},
  });
  fixtures.inventory = await checkedApi(prefix + '/inventory/events', {
    kind: 'receipt', event_date: day, notes: 'TEST · Приход ждёт проверки, остаток не изменён.',
    lines: [{original_product_name: 'TEST · Калийная селитра KNO3', quantity: '100', unit: 'kg'}],
    evidence: {source: 'test:modern-ui', locator: 'modern-ui-stock-' + crypto.randomUUID(), raw_text: 'TEST · Привезли 100 кг KNO3; цена пока неизвестна.'},
  });
  assert.equal(fixtures.inventory.status, 'draft');
  await page.goto(base);
  await navigateOwner(page, 'home');
  assert.deepEqual(await page.locator('#nav [data-page]').evaluateAll(nodes => nodes.map(n => n.dataset.page)), ['home', 'review', 'overview', 'more']);
  for (const [route, label] of [['home', 'Главная'], ['review', 'Проверка'], ['overview', 'Финансы'], ['more', 'Ещё']]) {
    assert.ok((await page.locator(`#nav [data-page=${route}]`).innerText()).includes(label));
  }

  // Save one ordinary source but never confirm its business action.
  const sourceText = 'Сегодня полить İmamlı. TEST UI · проверить давление после полива.';
  await page.locator('[data-r2-mode=text]').click();
  await sheet().getByRole('button', {name: 'Сохранить источник', exact: true}).click();
  await sheet().locator('#r2-source-status').filter({hasText: 'Добавьте текст или файл'}).waitFor();
  assert.equal(await sheet().getByRole('button', {name: 'Сохранить источник', exact: true}).isEnabled(), true);
  await screenshot('capture-validation', 390);
  await sheet().locator('textarea[name=text]').fill(sourceText);
  await sheet().getByRole('button', {name: 'Сохранить источник', exact: true}).click();
  await sheet().waitFor({state: 'hidden'});
  const capture = await poll(async () => (await checkedApi(owner + '/captures')).items.find(s => s.text === sourceText), 'TEST source was not saved');
  const proposals = await poll(async () => {
    const list = (await checkedApi(owner + '/confirmations')).items.filter(p => p.inbox_id === capture.id);
    return list.length ? list : null;
  }, 'TEST source did not prepare a review card');
  fixtures.proposals = proposals.map(p => p.id);

  // Four primary routes, secondary destinations, Back/Forward and reload.
  await navigateOwner(page, 'overview');
  assert.deepEqual(await page.locator('.section-tabs [data-go]').evaluateAll(nodes => nodes.map(n => n.dataset.go)), ['overview', 'debts', 'operations']);
  for (const route of ['debts', 'operations']) await navigateOwner(page, route);
  await navigateOwner(page, 'home');
  await navigateOwner(page, 'overview');
  await navigateOwner(page, 'more');
  assert.deepEqual(await page.locator('.destination-grid [data-go]').evaluateAll(nodes => nodes.map(n => n.dataset.go)), ['harvest', 'inventory', 'tasks', 'inbox', 'parties']);
  await page.locator('#app [data-go=inventory]').click(); await waitOwnerRoute(page, 'inventory');
  await page.goBack(); await waitOwnerRoute(page, 'more');
  await page.goBack(); await waitOwnerRoute(page, 'overview');
  await page.goForward(); await waitOwnerRoute(page, 'more');
  await page.goForward(); await waitOwnerRoute(page, 'inventory');
  await page.reload(); await waitOwnerRoute(page, 'inventory');
  for (const route of ['harvest', 'tasks', 'inbox', 'parties']) await navigateOwner(page, route);
  await navigateOwner(page, 'review'); await close();
  await page.locator('#refresh').click(); await ready(); assert.equal(await sheet().isVisible(), false, 'Refresh must not reopen dismissed Review');
  await page.reload(); await waitOwnerRoute(page, 'review', {dialog: false});
  assert.equal(await sheet().isVisible(), false, 'Reloading Review must not reopen its modal');

  const photo = execFileSync('python', ['-c', 'from tests.ocr_corpus import document;import sys;sys.stdout.buffer.write(document(1)[0])']);
  const pdf = execFileSync('python', ['-c', 'from tests.ocr_corpus import document;import sys;sys.stdout.buffer.write(document(28)[0])']);
  for (const width of [320, 390, 768, 1280]) {
    await page.setViewportSize({width, height: 844});
    for (const route of ['home', 'overview', 'harvest', 'inventory', 'tasks', 'more']) {
      await navigateOwner(page, route); await layout(`${width}/${route}`);
      if (route === 'overview') {
        // Hidden advanced actions are excluded only while native details are
        // closed. The exact same controls must pass every name/size/bounds
        // assertion once the owner opens their real disclosure summaries.
        for (const id of ['global-capture', 'reconcile']) {
          const control = page.locator(`#${id}`);
          assert.equal(await control.evaluate(node => window.isVisibleOwnerControl(node)), false, `${id} starts in its closed disclosure`);
          await revealOwnerControl(control);
          assert.equal(await control.evaluate(node => window.isVisibleOwnerControl(node)), true, `${id} must enter the visible-control assertions`);
          await layout(`${width}/overview-open-${id}`);
          await control.locator('xpath=ancestor::details[1]').locator(':scope > summary').click();
          assert.equal(await control.evaluate(node => window.isVisibleOwnerControl(node)), false, `${id} leaves the visible surface after closing`);
        }
        await page.evaluate(() => scrollTo(0, 0));
      }
      await screenshot(route, width);
      assert.equal(await page.locator('#nav [aria-current=page]').getAttribute('data-page'), ownerRoutes[route].primary);
    }
    await navigateOwner(page, 'review'); await dialogLayout(`${width}/queue`); await screenshot('queue', width); await close();
    await navigateOwner(page, 'home');
    for (const mode of ['text', 'photo', 'pdf']) {
      const opener = page.locator(`[data-r2-mode=${mode === 'text' ? 'text' : 'photo'}]`);
      await opener.click();
      await (await revealOwnerControl(sheet().locator('textarea[name=text]'))).fill('TEST · Привезли 100 кг KNO3 в İmamlı. Оригинал сохраняется до проверки.');
      if (mode !== 'text') {
        await sheet().locator('#r2-file').setInputFiles({name: mode === 'photo' ? 'TEST-fatura.png' : 'TEST-fatura.pdf', mimeType: mode === 'photo' ? 'image/png' : 'application/pdf', buffer: mode === 'photo' ? photo : pdf});
        const preview = sheet().locator('#r2-file-preview');
        if (mode === 'photo') {
          await preview.locator('.capture-preview-image').waitFor();
          await preview.locator('img').evaluate(image => image.decode());
          assert.match(await preview.locator('img').getAttribute('src'), /^data:image\/png;base64,/);
          assert.ok((await preview.locator('img').getAttribute('alt'))?.trim(), 'Photo preview needs a semantic name');
        } else {
          await preview.locator('.capture-file-tile').waitFor();
          assert.match(await preview.innerText(), /TEST-fatura\.pdf/);
          assert.equal(await preview.locator('img').count(), 0, 'PDF must not pretend to have a raster preview');
        }
        await sheet().locator('[data-capture-mode=text]').click();
        assert.equal(await sheet().locator('#r2-file').evaluate(input => input.files.length), 1, 'Mode switch preserves selected original');
        assert.match(await sheet().locator('textarea[name=text]').inputValue(), /100 кг/);
        await sheet().locator('[data-capture-mode=photo]').click();
      }
      await dialogLayout(`${width}/capture-${mode}`); await screenshot(`capture-${mode}`, width);
      if (width === 320 && mode === 'photo') {
        await page.setViewportSize({width, height: 420});
        await dialogLayout('320/capture-photo-short-viewport');
        await sheet().getByRole('button', {name: 'Сохранить источник', exact: true}).scrollIntoViewIfNeeded();
        await screenshot('capture-photo-short-viewport', width);
        await page.setViewportSize({width, height: 844});
      }
      await page.keyboard.press('Escape'); await sheet().waitFor({state: 'hidden'});
      assert.equal(await opener.evaluate(button => document.activeElement === button), true, 'Escape restores capture opener focus');
    }
  }

  // Replacing an already-open queue dialog with a fresh review and editor
  // must move focus into the new content. No edit is saved or confirmed here.
  await page.setViewportSize({width: 390, height: 844});
  await navigateOwner(page, 'review');
  const fixtureProposal = fixtures.proposals[0];
  await sheet().locator(`[data-proposal="${fixtureProposal}"] [data-r2-open]`).click();
  await sheet().getByRole('heading', {name: 'Проверить действие', exact: true}).waitFor();
  await dialogLayout('queue-to-fresh-review-focus');
  await sheet().locator('#r2-edit').click();
  await sheet().getByRole('heading', {name: 'Исправить карточку', exact: true}).waitFor();
  await dialogLayout('fresh-review-to-editor-focus');
  const editorTargets = await sheet().locator('button,input,select,textarea,a[href],summary')
    .evaluateAll(nodes => nodes.filter(node => !node.disabled && window.isVisibleOwnerControl(node)).length);
  for (let step = 0; step < editorTargets + 2; step++) {
    await page.keyboard.press('Tab');
    assert.equal(await sheet().evaluate(dialog => dialog.contains(document.activeElement)), true, 'Tab remains inside the replacement editor');
  }
  await page.keyboard.press('Shift+Tab');
  assert.equal(await sheet().evaluate(dialog => dialog.contains(document.activeElement)), true, 'Reverse Tab remains inside the replacement editor');
  await page.keyboard.press('Escape'); await sheet().waitFor({state: 'hidden'});
  assert.equal(await page.evaluate(() => document.querySelector('#app').contains(document.activeElement)
    || document.activeElement === document.querySelector('#nav [aria-current=page]')), true, 'Dismissed editor restores focus to its Review surface');
  const untouchedProposal = await checkedApi(owner + `/confirmations/${fixtureProposal}/preview`);
  assert.equal(untouchedProposal.version, proposals.find(proposal => proposal.id === fixtureProposal).version, 'Focus checks must not save or confirm a proposal');

  // Presentation-only stress fixture: intercept the Home response without
  // posting or revising any amount. Real season analytics remain untouched.
  // Screenshots are deliberately separated and visibly marked TEST synthetic.
  await navigateOwner(page, 'home');
  const realReceivableText = await page.locator('.owner-receivable .big').innerText();
  const stress = {
    amount: '999999999999999999.99',
    display: '999 999 999 999 999 999,99 TRY',
    entity: 'TEST · СИНТЕТИЧЕСКИЙ ТЕСТ ВЁРСТКИ · сумма и названия только для проверки интерфейса',
    task: 'TEST · Проверить давление после продолжительного полива в дальней теплице, записать показания каждого датчика и сверить результат с сохранённым поручением владельца',
    exception: 'TEST · Работник сообщил о незавершённой проверке системы полива в дальней теплице: требуется уточнить причину и внимательно проверить исходный отчёт перед решением',
  };
  const homePattern = '**/owner/home';
  await page.route(homePattern, async route => {
    const response = await route.fetch(), actual = await response.json();
    await route.fulfill({response, json: {
      ...actual,
      finance: {...actual.finance, entity_name: stress.entity,
        balances: {...actual.finance.balances, TRY: {...actual.finance.balances.TRY, receivable: stress.amount}}},
      tasks: [{id: '00000000-0000-4000-8000-000000000001', version: 1, title: stress.task, planned_date: null}],
      task_count: 1,
      worker_exceptions: [{id: '00000000-0000-4000-8000-000000000002', title: stress.exception}],
    }});
  });
  try {
    for (const width of [320, 390, 768, 1280]) {
      await page.setViewportSize({width, height: 844});
      await page.locator('#refresh').click(); await ready();
      assert.equal(await page.locator('.owner-receivable .big').innerText(), stress.display, 'Large TRY amount must retain every exact decimal digit');
      await page.locator('#app').getByText(stress.entity, {exact: true}).waitFor();
      assert.ok((await page.locator('.owner-task').innerText()).includes(stress.task), 'Long task text must remain complete');
      assert.equal(await page.locator('.worker-exception strong').innerText(), stress.exception, 'Long worker exception must remain complete');
      await layout(`${width}/TEST-synthetic-stress-home`);
      await screenshot('TEST-synthetic-stress-home', width);
    }
  } finally {
    await page.unroute(homePattern);
    await page.locator('#refresh').click(); await ready();
  }
  assert.equal(await page.locator('.owner-receivable .big').innerText(), realReceivableText, 'Removing the presentation fixture restores the actual Home amount');
  assert.deepEqual((await checkedApi(owner + '/home')).finance.balances, before.finance.balances, 'Synthetic stress amount never reaches the database');

  // A slow older navigation cannot move focus back or open Review over More.
  await navigateOwner(page, 'home');
  let releaseOld, oldEntered, financeReads = 0, queueReads = 0;
  const oldGate = new Promise(resolve => {releaseOld = resolve;});
  const oldHeld = new Promise(resolve => {oldEntered = resolve;});
  const countQueue = request => {if (request.url().includes('/owner/confirmations?')) queueReads++;};
  page.on('request', countQueue);
  await page.route('**/finance-overview?*', async route => {
    if (++financeReads === 1) {oldEntered(); await oldGate;}
    await route.continue();
  });
  try {
    await page.locator('#nav [data-page=review]').click(); await oldHeld;
    await page.locator('#nav [data-page=more]').click();
    await page.locator('#app').getByRole('heading', {name: 'Ещё', exact: true}).waitFor();
  } finally {releaseOld();}
  await ready(); await page.unroute('**/finance-overview?*'); page.off('request', countQueue);
  await waitOwnerRoute(page, 'more');
  assert.equal(queueReads, 0, 'Stale Review reload must never start its queue');
  assert.equal(await sheet().isVisible(), false);

  // A queue response already in flight is also fenced when the owner uses
  // the brand Home link, not only when clicking another primary button.
  let releaseQueue, queueEntered;
  const queueGate = new Promise(resolve => {releaseQueue = resolve;});
  const queueHeld = new Promise(resolve => {queueEntered = resolve;});
  await page.route('**/owner/confirmations?offset=0', async route => {
    const response = await route.fetch(); queueEntered(); await queueGate;
    await route.fulfill({response});
  }, {times: 1});
  const lateQueueResponse = page.waitForResponse(response => response.url().includes('/owner/confirmations?offset=0'));
  try {
    await page.locator('#nav [data-page=review]').click(); await queueHeld;
    await page.locator('.brand').click(); await waitOwnerRoute(page, 'home');
  } finally {releaseQueue();}
  await (await lateQueueResponse).finished();
  await page.unrouteAll({behavior: 'wait'});
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  assert.equal(await sheet().isVisible(), false, 'Late Review response must not reopen over Home');
  await waitOwnerRoute(page, 'home');

  // Visible loading and recoverable server error, followed by a real reload.
  await page.setViewportSize({width: 390, height: 844}); await navigateOwner(page, 'home');
  let release, entered;
  const gate = new Promise(resolve => {release = resolve;}), held = new Promise(resolve => {entered = resolve;});
  const financePattern = '**/finance-overview?*';
  await page.route(financePattern, async route => {entered(); await gate; await route.continue();});
  try {
    await page.locator('#refresh').click(); await held;
    assert.equal(await page.locator('#app').getAttribute('aria-busy'), 'true');
    await screenshot('loading', 390);
  } finally {release();}
  await ready(); await page.unroute(financePattern);
  expectedUnavailable = true;
  await page.route(financePattern, route => route.fulfill({status: 503, contentType: 'application/json', body: JSON.stringify({error: {message: 'TEST · Сервер временно недоступен. Повторите обновление.'}})}), {times: 1});
  await page.locator('#refresh').click(); await page.locator('#notice').filter({hasText: 'TEST · Сервер временно недоступен. Повторите обновление.'}).waitFor(); await ready();
  await screenshot('recoverable-error', 390);
  await page.unroute(financePattern); await page.locator('#refresh').click(); await ready(); expectedUnavailable = false;
  await waitOwnerRoute(page, 'home');

  for (const id of fixtures.proposals) {
    const proposal = await checkedApi(owner + `/confirmations/${id}/preview`);
    assert.notEqual(proposal.state, 'POSTED');
    await checkedApi(owner + `/confirmations/${id}/reject`, {expected_version: proposal.version});
  }
  assert.deepEqual((await checkedApi(owner + '/home')).finance.balances, before.finance.balances, 'Presentation and capture must not move Finance');
  assert.deepEqual((await checkedApi(prefix + '/inventory')).stock, initialStock, 'Draft receipt must not move stock');
  assert.deepEqual(await page.evaluate(() => window.ownerUiFaults), []);
  assert.deepEqual(errors, [], 'Unexpected console or runtime errors');
  await fs.writeFile(`${artifacts}/owner-modern-ui.json`, JSON.stringify({ok: true, widths: [320, 390, 768, 1280], screenshots, presentationStress: {synthetic: true, amount: stress.amount, display: stress.display, databaseAmountsChanged: false, widths: [320, 390, 768, 1280]}, fixtureIds: {task: fixtures.task.id, harvest: fixtures.harvest.id, inventoryDraft: fixtures.inventory.id, proposals: fixtures.proposals}, financeUnchanged: true, stockUnchanged: true, errors}, null, 2));
  console.log('PASS modern owner UI: four-route navigation + More + history/reload, source-first photo/text/PDF previews, queue, operational modules, 320/390/768/1280, 44px named controls, dock clearance, scrollable dialogs, Escape/focus, loading/error recovery, unchanged Finance/stock');
} catch (error) {
  await page.screenshot({path: `${artifacts}/owner-modern-failure.png`, fullPage: true}).catch(() => {});
  throw error;
} finally {await browser.close();}
