// Additive browser evidence for the bounded synthetic daily-action prototype.
// All mutations below use trusted UI clicks and field/keyboard entry. The public
// read-only demo snapshot is inspected for cross-view and accounting assertions.
import assert from 'node:assert/strict';

const WIDTHS = [320, 390, 700, 768, 820, 900, 1280];
const stateOf = page => page.evaluate(() => window.r3Demo.state());
const sum = (rows, key) => rows.reduce((total, row) => total + row[key], 0);
const financeOf = state => {
  const d = state.daily;
  const remaining = obligation => obligation.amountMinor - d.payments.flatMap(p => p.allocations).filter(a => a.obligationId === obligation.id).reduce((n, a) => n + a.amountMinor, 0);
  return {
    revenue: sum(d.receipts, 'totalMinor'), expense: sum(d.expenses, 'amountMinor'),
    received: sum(d.payments.filter(p => p.direction === 'in'), 'amountMinor'),
    paid: sum(d.payments.filter(p => p.direction === 'out'), 'amountMinor'),
    receivable: d.obligations.filter(o => o.direction === 'receivable').reduce((n, o) => n + remaining(o), 0),
    payable: d.obligations.filter(o => o.direction === 'payable').reduce((n, o) => n + remaining(o), 0),
  };
};
const sourceOf = (state, action) => state.daily.sources.find(source => source.id === action.sourceId);

export async function runDailyJourneys({browser, engine, newPage, click, fill, ready, route, screenshot, layout, report, correctionJourney, correctionReload}) {
  // Reuse the independently instrumented trusted-click collector without changing
  // the historical twenty design-lock correction records or their gates.
  report.daily_journeys ??= [];
  report.daily_layout_checks ??= [];
  const journey = async (name, run) => {
    const start = report.correction_journeys.length;
    try { await correctionJourney(browser, engine, `Daily: ${name}`, run); }
    finally { report.daily_journeys.push(...report.correction_journeys.splice(start)); }
  };
  const key = async (page, value) => {
    await page.keyboard.press(value);
    await page.evaluate(value => window.__r3Metrics.key(value), value);
  };
  const select = async (page, name, value) => {
    const field = page.getByTestId(`daily-${name}`);
    const index = await field.evaluate((el, wanted) => [...el.options].findIndex(option => option.value === wanted), value);
    assert.ok(index >= 0, `The visible ${name} select offers ${value}`);
    await field.click();
    for (const pressed of ['Home', ...Array(index).fill('ArrowDown'), 'Enter']) await key(page, pressed);
    assert.equal(await field.inputValue(), value, `${name} changed through real keyboard selection`);
    await field.blur();
  };
  const go = async (page, id) => { await click(page, id); await ready(page); };
  const finance = async page => { await go(page, 'nav-more'); await go(page, 'more-finance'); };
  const inventory = async page => { await go(page, 'nav-more'); await go(page, 'more-inventory'); };
  const saveAction = async page => {
    await go(page, 'daily-save');
    await page.getByTestId('daily-original').waitFor();
    return (await stateOf(page)).daily.actions[0];
  };
  const confirmAction = async page => {
    await go(page, 'daily-confirm');
    await page.getByTestId('daily-confirmed').waitFor();
    assert.equal(await page.getByTestId('daily-confirm').count(), 0, 'Confirmed action has no repeat-confirm control');
  };
  const financialProjection = state => {
    assert.equal(sum(state.receivables, 'amountMinor'), financeOf(state).receivable, 'Receivable cards match the same confirmed ledger');
    assert.equal(sum(state.payables, 'amountMinor'), financeOf(state).payable, 'Payable cards match the same confirmed ledger');
  };

  await journey('radial toggle, outside, Escape, Back, focus and motion at 320/390', async page => {
    for (const motion of ['reduce', 'no-preference']) for (const width of [320, 390]) {
      await page.setViewportSize({width, height: 844});
      await page.emulateMedia({reducedMotion: motion});
      const toggle = page.getByTestId('radial-toggle');
      const collapsed = async () => {
        assert.equal(await toggle.getAttribute('aria-expanded'), 'false');
        assert.equal(await page.locator('#radial-menu').isVisible(), false);
        assert.equal(await page.locator('#main').evaluate(el => el.inert), false);
        await page.waitForFunction(() => !history.state?.r3Radial);
      };
      const open = async () => {
        await go(page, 'radial-toggle');
        assert.equal(await toggle.getAttribute('aria-expanded'), 'true');
        await page.locator('#radial-menu').evaluate(async el => { await Promise.all(el.getAnimations({subtree: true}).map(animation => animation.finished.catch(() => {}))); });
        assert.equal(await page.locator('#main').evaluate(el => el.inert), true);
        assert.equal(await page.evaluate(() => document.activeElement?.dataset.testid), 'capture-photo', 'Opening puts keyboard focus on the first capture choice');
        const targets = await page.locator('#radial-menu button').evaluateAll(buttons => buttons.map(button => {
          const b = button.getBoundingClientRect(), style = getComputedStyle(button);
          const hit = document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2);
          return {id: button.dataset.testid, x: b.x, y: b.y, right: b.right, bottom: b.bottom, width: b.width, height: b.height, topmost: hit === button || button.contains(hit), animation: style.animationName};
        }));
        assert.equal(targets.length, 4);
        for (const t of targets) {
          assert.ok(t.width >= 44 && t.height >= 44, `${t.id} is a touch target`);
          assert.ok(t.x >= 0 && t.right <= width && t.y >= 0 && t.bottom <= 844, `${t.id} fits ${width}px`);
          assert.equal(t.topmost, true, `${t.id} is clickable and unobscured`);
          assert.equal(t.animation === 'none', motion === 'reduce', 'Motion preference is honored');
        }
        for (let i = 0; i < targets.length; i++) for (let j = i + 1; j < targets.length; j++) {
          const a = targets[i], b = targets[j];
          assert.ok(a.right <= b.x || b.right <= a.x || a.bottom <= b.y || b.bottom <= a.y, 'Radial touch targets do not overlap');
        }
      };
      await collapsed();
      if (motion === 'reduce') await screenshot(page, engine, 'daily-radial-collapsed', width, 'Daily Home with collapsed AI Capture');
      await open();
      for (const target of ['capture-voice', 'capture-text', 'capture-video', 'radial-toggle', 'capture-photo']) {
        await key(page, 'Tab');
        assert.equal(await page.evaluate(() => document.activeElement?.dataset.testid), target, 'Tab stays inside the expanded capture controls');
      }
      await key(page, 'Shift+Tab');
      assert.equal(await page.evaluate(() => document.activeElement?.dataset.testid), 'radial-toggle');
      await screenshot(page, engine, `daily-radial-expanded-${motion}`, width, `Expanded AI Capture, ${motion} motion`);
      await go(page, 'radial-toggle'); await collapsed();
      assert.equal(await toggle.evaluate(el => el === document.activeElement), true);
      await open();
      await page.getByTestId('radial-outside').click({position: {x: 10, y: 40}});
      await collapsed();
      await open(); await key(page, 'Escape'); await collapsed();
      assert.equal(await toggle.evaluate(el => el === document.activeElement), true, 'Escape restores focus');
      await open(); await page.goBack(); await collapsed();
      assert.equal(await page.evaluate(() => location.hash), '#home', 'Back dismisses the fan without leaving Home');
      await go(page, 'home-expense');
      assert.equal(await page.locator('#daily-form').getAttribute('data-kind'), 'expense', 'Main actions are usable after radial dismissal');
      await go(page, 'back'); await toggle.waitFor();
    }
    report.checks.push(`${engine}: trusted radial interactions at 320/390, normal/reduced motion, four unobscured 44px targets, Tab trap, toggle/outside/Escape/Back dismissal`);
  });

  await journey('raw expense without counterparty, clarification, immutable source and reload', async page => {
    const before = await stateOf(page), money = financeOf(before), raw = 'Инструменты 7250,25 лир';
    await go(page, 'home-expense');
    assert.match(await page.getByTestId('date-assumption').innerText(), /Предположение/);
    assert.equal(await page.getByTestId('daily-party').getAttribute('required'), null);
    await fill(page, 'daily-raw', raw);
    const action = await saveAction(page), pending = await stateOf(page), original = sourceOf(pending, action);
    assert.equal(action.canConfirm, false); assert.equal(action.status, 'pending');
    assert.equal(await page.getByTestId('daily-confirm').count(), 0);
    assert.equal(pending.daily.sources.length, before.daily.sources.length + 1);
    assert.deepEqual(financeOf(pending), money, 'Saving source alone creates no financial event');
    assert.equal(await page.getByTestId('daily-original').innerText(), raw);
    assert.match(await page.getByTestId('daily-clarifications').innerText(), /назначение|сумму/);
    await correctionReload(page);
    assert.equal(await page.getByTestId('daily-original').innerText(), raw, 'Pending raw source survives a reload');
    await go(page, 'daily-edit'); await fill(page, 'daily-purpose', 'Инструменты'); await fill(page, 'daily-amount', '7250,25');
    await saveAction(page);
    assert.deepEqual(sourceOf(await stateOf(page), action), original, 'Clarification does not rewrite the original');
    await confirmAction(page);
    const after = await stateOf(page), posted = after.daily.expenses.find(e => e.id === action.id);
    assert.equal(posted.amountMinor, 725025); assert.equal(posted.party, null);
    assert.deepEqual(financeOf(after), {...money, expense: money.expense + 725025}, 'Expense is neither a payment nor an automatic payable');
    assert.equal(after.daily.actions.filter(a => a.id === action.id).length, 1);
    financialProjection(after);
    await correctionReload(page); assert.deepEqual(financeOf(await stateOf(page)), financeOf(after));
    assert.equal(await page.getByTestId('daily-confirm').count(), 0);
    await go(page, 'daily-result-open'); await page.getByTestId('finance-overview').waitFor();
    assert.match(await page.getByTestId('finance-overview').innerText(), /Неполные данные/);
  });

  await journey('quick harvest remains a field report; same-date depot candidate needs confirmation', async page => {
    const before = await stateOf(page), money = financeOf(before);
    await go(page, 'home-cut'); await select(page, 'greenhouseId', 'imamli'); await fill(page, 'daily-bunches', '18');
    const action = await saveAction(page), saved = await stateOf(page), original = sourceOf(saved, action);
    assert.equal(saved.daily.fieldReports.length, before.daily.fieldReports.length, 'Save has not confirmed a field report');
    assert.equal(original.structuredOriginal.bunches, '18'); assert.equal(original.structuredOriginal.greenhouseId, 'imamli');
    assert.match(await page.locator('#main').innerText(), /UNKNOWN/);
    await confirmAction(page);
    const after = await stateOf(page), field = after.daily.fieldReports.find(r => r.id === action.id);
    assert.equal(field.bunches, 18); assert.equal(field.kg, null); assert.equal(field.revenueMinor, null); assert.equal(field.receiptId, null);
    assert.deepEqual(financeOf(after), money);
    await go(page, 'daily-result-open');
    const candidate = `link-${action.id}-depot-oct09`;
    await page.getByTestId(candidate).waitFor();
    assert.match(await page.locator('#main').innerText(), /не доказательство связи/);
    await go(page, candidate);
    assert.equal((await stateOf(page)).daily.fieldReports.find(r => r.id === action.id).receiptId, null, 'Clicking a candidate does not link it');
    const link = (await stateOf(page)).daily.actions[0]; assert.equal(link.kind, 'harvest-link');
    await confirmAction(page);
    const linked = await stateOf(page), linkedField = linked.daily.fieldReports.find(r => r.id === action.id);
    assert.equal(linkedField.receiptId, 'depot-oct09'); assert.equal(linkedField.kg, null); assert.equal(linkedField.revenueMinor, null);
    assert.equal(linked.daily.links.filter(r => r.reportId === action.id).length, 1);
    assert.deepEqual(financeOf(linked), money, 'Linking never duplicates a receipt, revenue, receivable or cash');
    assert.deepEqual(sourceOf(linked, action), original);
    await correctionReload(page); assert.deepEqual(financeOf(await stateOf(page)), money);
    await go(page, 'daily-result-open'); await go(page, `linked-${action.id}`);
    assert.match(await page.locator('#main').innerText(), /Чек депо/);
    assert.equal(await page.getByTestId('depot-line').count(), before.daily.receipts.find(r => r.id === 'depot-oct09').lines.length);
  });

  await journey('finance period, incomplete profit/cash, three salary payments and loan separation', async page => {
    await finance(page);
    const state = await stateOf(page), d = state.daily, totals = financeOf(state);
    assert.equal(d.period.from, '2026-09-01'); assert.equal(d.period.to, '2026-10-09');
    const overview = await page.getByTestId('finance-overview').innerText();
    for (const label of ['Выручка', 'Операционные расходы', 'Операционный результат', 'Денежные поступления', 'Денежные выплаты', 'Дебиторская задолженность', 'Кредиторская задолженность', '1 сентября — 9 октября 2026', 'TRY', 'Неполные данные', 'Не рассчитан', 'UNKNOWN']) assert.ok(overview.includes(label), label);
    assert.equal(d.completeness.openingCashKnown, false); assert.equal(d.completeness.operatingCoverageComplete, false);
    assert.notEqual(totals.revenue, totals.received); assert.notEqual(totals.expense, totals.paid);
    assert.deepEqual(totals, {revenue: 23536500, expense: 26720000, received: 15500000, paid: 25800000, receivable: 8036500, payable: 3420000});
    await page.getByText('Полнота и основания расчёта', {exact: true}).click();
    assert.match(await page.locator('#main').innerText(), /не прибыль предприятия/);
    await go(page, 'finance-tab-movements');
    const settlement = d.salarySettlements[0], cash = d.payments.filter(p => settlement.paymentIds.includes(p.id));
    assert.equal(cash.length, 3); assert.equal(sum(cash, 'amountMinor'), settlement.totalMinor); assert.equal(settlement.postsCash, false);
    assert.equal(d.payments.filter(p => p.salarySettlementId === settlement.id).length, 3);
    assert.match(await page.locator('#main').innerText(), /Три денежных события/);
    const loan = d.payments.find(p => p.category === 'loan-principal'); assert.ok(loan);
    assert.equal(d.expenses.some(e => e.id === loan.id || e.category === 'loan-principal'), false);
    assert.match(await page.locator('#main').innerText(), /ТЕЛО ЗАЙМА/);
    assert.match(await page.locator('#main').innerText(), /Новый приход и новый платёж не создаются/);
    await go(page, 'finance-tab-overview'); financialProjection(await stateOf(page));
  });

  await journey('payment requires account/classification and debt changes only through explicit allocation', async page => {
    await finance(page); const initial = await stateOf(page), before = financeOf(initial);
    await go(page, 'finance-payment');
    assert.equal(await page.getByTestId('daily-accountId').inputValue(), '');
    assert.equal(await page.getByTestId('daily-category').inputValue(), '');
    await fill(page, 'daily-purpose', 'DEMO аванс на материалы'); await fill(page, 'daily-amount', '1200');
    const action = await saveAction(page);
    assert.equal(action.canConfirm, false); assert.equal(await page.getByTestId('daily-confirm').count(), 0);
    assert.match(await page.getByTestId('daily-clarifications').innerText(), /счёт/);
    assert.deepEqual(financeOf(await stateOf(page)), before);
    await go(page, 'daily-edit'); await select(page, 'accountId', 'cash-try'); await select(page, 'category', 'advance');
    assert.equal(await page.getByTestId('daily-obligationId').inputValue(), '', 'An obligation is not silently selected');
    await saveAction(page); await confirmAction(page);
    const advance = await stateOf(page), payment = advance.daily.payments.find(p => p.id === action.id);
    assert.equal(payment.category, 'advance'); assert.deepEqual(payment.allocations, []);
    assert.deepEqual(financeOf(advance), {...before, paid: before.paid + 120000});
    await go(page, 'daily-result-open'); await go(page, 'finance-payment');
    await fill(page, 'daily-purpose', 'Частичная оплата ремонта'); await fill(page, 'daily-amount', '8000');
    await select(page, 'accountId', 'cash-try'); await select(page, 'category', 'operating'); await select(page, 'obligationId', 'pump-payable');
    const allocated = await saveAction(page);
    assert.deepEqual(financeOf(await stateOf(page)), financeOf(advance), 'Choosing debt and saving remain pending');
    await confirmAction(page);
    const after = await stateOf(page);
    assert.deepEqual(after.daily.payments.find(p => p.id === allocated.id).allocations, [{obligationId: 'pump-payable', amountMinor: 800000}]);
    assert.deepEqual(financeOf(after), {...before, paid: before.paid + 920000, payable: before.payable - 800000});
    financialProjection(after); await correctionReload(page); financialProjection(await stateOf(page));
    assert.deepEqual(financeOf(await stateOf(page)), financeOf(after));
  });

  await journey('obligation needs an explicit creditor and never creates payment or expense', async page => {
    await finance(page); const before = financeOf(await stateOf(page));
    await go(page, 'finance-obligation'); await fill(page, 'daily-purpose', 'DEMO обязательство по проверке насоса'); await fill(page, 'daily-amount', '950');
    const action = await saveAction(page);
    assert.equal(action.canConfirm, false); assert.equal(await page.getByTestId('daily-confirm').count(), 0);
    assert.match(await page.getByTestId('daily-clarifications').innerText(), /кредитора|дебитора/);
    await go(page, 'daily-edit'); await fill(page, 'daily-party', 'DEMO Проверка оборудования');
    await saveAction(page); await confirmAction(page);
    const after = await stateOf(page);
    assert.equal(after.daily.obligations.find(o => o.id === action.id).direction, 'payable');
    assert.deepEqual(financeOf(after), {...before, payable: before.payable + 95000});
    financialProjection(after);
    await go(page, 'daily-result-open'); await go(page, 'finance-tab-payable');
    await go(page, 'finance-DEMO Проверка оборудования');
    assert.match(await page.locator('#main').innerText(), /DEMO Проверка оборудования/);
  });

  await journey('arrival, consumption, transfer and UNKNOWN stock share one inventory without finance effects', async page => {
    await inventory(page); const initial = await stateOf(page), money = financeOf(initial);
    const post = async (kind, stockId, quantity, toPlace) => {
      await go(page, `inventory-${kind}`); await select(page, 'stockId', stockId); await fill(page, 'daily-quantity', quantity);
      if (toPlace) await select(page, 'toPlace', toPlace);
      const before = await stateOf(page), action = await saveAction(page);
      assert.deepEqual((await stateOf(page)).stock, before.stock, 'Saving an inventory source does not post physical movement');
      await confirmAction(page); const after = await stateOf(page);
      assert.deepEqual(after.stock, after.daily.inventory, 'Inventory list and daily ledger project the same stock');
      assert.deepEqual(financeOf(after), money, 'Physical movement never posts debt, payment or expense');
      assert.equal(after.daily.invoices[0].arrivalConfirmed, false);
      await go(page, 'daily-result-open'); return action;
    };
    await post('arrival', 'kno3', '12.125');
    assert.equal((await stateOf(page)).stock.find(s => s.id === 'kno3').quantityMinor, 192125);
    assert.match(await page.getByTestId('stock-kno3').innerText(), /192,125/);
    await post('consumption', 'kno3', '0.125');
    assert.equal((await stateOf(page)).stock.find(s => s.id === 'kno3').quantityMinor, 192000);
    const transfer = await post('transfer', 'kno3', '7.125', 'Özvadi');
    const moved = await stateOf(page), legs = moved.stock.flatMap(s => s.movements).filter(m => m.transferId === transfer.id);
    assert.equal(legs.length, 2); assert.equal(sum(legs, 'quantityMinor'), 0);
    assert.equal(moved.stock.find(s => s.id === 'kno3').quantityMinor, 184875);
    assert.equal(moved.stock.find(s => s.id === 'kno3-sun').quantityMinor, 27125);
    await post('arrival', 'film-stock', '12');
    const unknown = (await stateOf(page)).stock.find(s => s.id === 'film-stock');
    assert.equal(unknown.quantity, null); assert.equal(unknown.quantityMinor, null); assert.equal(unknown.movements.at(-1).quantity, 12);
    assert.match(await page.getByTestId('stock-film-stock').innerText(), /Неизвестно|Неизвестен|неизвест/i);
    const persisted = await stateOf(page); await correctionReload(page);
    assert.deepEqual((await stateOf(page)).stock, persisted.stock); assert.deepEqual(financeOf(await stateOf(page)), money);
    await go(page, 'stock-kno3'); await go(page, 'stock-movements');
    assert.match(await page.locator('#main').innerText(), /Перемещение в Özvadi/);
  });

  await journey('new personal task, DONE/restore, separate reviewed worker assignment and reload', async page => {
    const initial = await stateOf(page); assert.ok(initial.tasks.length >= 4);
    await go(page, 'nav-tasks'); await go(page, 'tasks-add-self');
    await fill(page, 'daily-title', 'Проверить капельную линию в TEST секторе'); await select(page, 'place', 'İmamlı'); await fill(page, 'daily-when', 'Следующая неделя');
    await go(page, 'daily-save'); await page.getByTestId('task-save').waitFor();
    const added = await stateOf(page), task = added.tasks.find(t => !initial.tasks.some(old => old.id === t.id));
    assert.ok(task); assert.equal(task.worker, null); assert.equal(task.status, 'todo'); assert.equal(task.date, null);
    assert.equal(added.workerTask.status, initial.workerTask.status);
    await go(page, 'task-save'); assert.equal((await stateOf(page)).tasks.find(t => t.id === task.id).status, 'done');
    assert.match(await page.getByTestId('task-save').innerText(), /Вернуть/);
    await correctionReload(page); assert.equal((await stateOf(page)).tasks.find(t => t.id === task.id).status, 'done');
    await go(page, 'task-save'); assert.equal((await stateOf(page)).tasks.find(t => t.id === task.id).status, 'todo');
    await go(page, 'nav-tasks'); await go(page, 'tasks-add-workers');
    await fill(page, 'daily-title', 'Осмотреть запасной фильтр · TEST'); await select(page, 'worker', 'muhammed'); await select(page, 'place', 'İmamlı');
    assert.match(await page.locator('#main').innerText(), /Автоматического перевода и отправки нет/);
    const workersBefore=(await stateOf(page)).workerTasks.length;
    await go(page, 'daily-save'); await page.getByTestId('current-action').waitFor();
    const assigned=await stateOf(page), assignment=assigned.workerTasks.find(t=>t.title==='Осмотреть запасной фильтр · TEST');
    assert.equal(assigned.workerTasks.length,workersBefore+1); assert.equal(assignment.worker,'muhammed');assert.equal(assignment.translation,null);assert.equal(assignment.synthetic,true);
    assert.match(await page.locator('#main').innerText(), /Перевод на дари не подготовлен/);
    assert.equal(assigned.tasks.find(t=>t.id===task.id).worker,null,'New worker creation leaves the personal task separate');
    await correctionReload(page);assert.equal((await stateOf(page)).workerTasks.find(t=>t.id===assignment.id).title,assignment.title);
    await go(page,'nav-more');await go(page,'more-workers');await go(page,'worker-muhammed');await go(page,`task-${assignment.id}`);
    assert.match(await page.locator('#main').innerText(), /TEST Мухаммед/);
  });

  await journey('depot quality lines and estimates, section actions, and synthetic video source', async page => {
    await go(page, 'nav-more'); await go(page, 'more-harvest'); await go(page, 'harvest-all-depots');
    await go(page, 'depot-depot-sep17');
    assert.equal(await page.getByTestId('depot-line').count(), 4, 'Repeated quality stays on separate original lines');
    const receipt = (await stateOf(page)).daily.receipts.find(r => r.id === 'depot-sep17');
    assert.equal(receipt.lines.filter(l => l.quality === 'II').length, 2);
    assert.ok(receipt.allocations.every(a => a.provenance === 'derived-estimate' && a.kg === null && a.revenueMinor === null));
    assert.match(await page.locator('#main').innerText(), /DERIVED ESTIMATE/);
    assert.match(await page.locator('#main').innerText(), /Rolling Alacak/);
    await go(page, 'depot-link-open'); await go(page, 'reconcile-depots'); await go(page, 'depot-add');
    await go(page, 'depot-capture'); await page.getByTestId('photo-file').waitFor();
    await go(page, 'back'); await go(page, 'depot-records');
    await go(page, 'nav-more'); await go(page, 'more-workers'); await go(page, 'workers-problems');
    assert.match(await page.locator('#main').innerText(), /Есть проблема/);
    await go(page, 'nav-more'); await go(page, 'more-workers'); await go(page, 'workers-assign');
    await page.getByTestId('daily-worker').waitFor(); await go(page, 'back');
    await go(page, 'nav-home');
    await go(page,'capture-text');await fill(page,'capture-input','Нужно переделать полив на Озвади на следующей неделе');await go(page,'back');
    const before = await stateOf(page);
    await go(page, 'radial-toggle'); await go(page, 'capture-video');
    assert.match(await page.locator('#main').innerText(), /ИИ не анализирует видео/);
    assert.equal(await page.getByTestId('video-file').count(), 1);
    assert.equal((await stateOf(page)).draft.text,'','Video never inherits hidden unsaved text');
    await go(page, 'demo-video'); await fill(page, 'capture-comment', 'TEST визуальный источник без AI-анализа');
    await go(page, 'capture-save'); await page.getByText('Нужна ручная проверка', {exact: true}).waitFor();
    const after = await stateOf(page), source = after.sources.find(s => !before.sources.some(old => old.id === s.id));
    assert.ok(source); assert.equal(source.file.kind, 'video'); assert.equal(source.file.fixture, true);
    assert.equal(await page.getByTestId('confirm-proposal').count(), 0, 'Video is not falsely understood or posted');
    assert.deepEqual(financeOf(after), financeOf(before));
    await page.getByText('Исходник и история', {exact: true}).click(); assert.match(await page.locator('#main').innerText(), /анализ ИИ не выполнялся/);
    await screenshot(page, engine, 'daily-video-synthetic-source', 390, 'Saved synthetic video source with explicit no-AI boundary');
    await correctionReload(page); assert.equal((await stateOf(page)).sources.find(s => s.id === source.id).file.kind, 'video');
    assert.equal((await stateOf(page)).tasks.length,before.tasks.length,'Video source creates no accidental task');
    await go(page,'nav-home');await go(page,'capture-text');assert.equal(await page.getByTestId('capture-input').inputValue(),'Нужно переделать полив на Озвади на следующей неделе','The independent text draft survives cross-mode capture and reload');
    await go(page,'back');await go(page,'capture-photo');assert.equal((await stateOf(page)).draft.text,'');await go(page,'demo-photo');await go(page,'capture-save');await page.getByTestId('confirm-proposal').waitFor();assert.match(await page.locator('#main').innerText(),/Чек депо/);assert.equal((await stateOf(page)).tasks.length,before.tasks.length);
  });

  await journey('media type/size guards, interrupted reader, and failed local storage with idempotent retry', async page => {
    await go(page,'capture-video');
    await page.getByTestId('video-file').setInputFiles({name:'SYNTHETIC.png',mimeType:'image/png',buffer:Buffer.from('TEST')});
    assert.match(await page.locator('#toast').innerText(),/видео MP4/);assert.equal((await stateOf(page)).draft.file,null);
    await page.getByTestId('video-file').setInputFiles({name:'SYNTHETIC-large.mp4',mimeType:'video/mp4',buffer:Buffer.alloc(512*1024+1)});
    assert.match(await page.locator('#toast').innerText(),/до 512 КБ/);assert.equal((await stateOf(page)).draft.file,null);
    await page.getByTestId('video-file').setInputFiles({name:'SYNTHETIC-green-frame.mp4',mimeType:'video/mp4',buffer:Buffer.from('AAAAIGZ0eXBpc29tAAACAGlzb21pc28yYXZjMW1wNDEAAAMxbW9vdgAAAGxtdmhkAAAAAAAAAAAAAAAAAAAD6AAAA+gAAQAAAQAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAgAAAlx0cmFrAAAAXHRraGQAAAADAAAAAAAAAAAAAAABAAAAAAAAA+gAAAAAAAAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAAAGAAAABAAAAAAAAkZWR0cwAAABxlbHN0AAAAAAAAAAEAAAPoAAAAAAABAAAAAAHUbWRpYQAAACBtZGhkAAAAAAAAAAAAAAAAAABAAAAAQABVxAAAAAAALWhkbHIAAAAAAAAAAHZpZGUAAAAAAAAAAAAAAABWaWRlb0hhbmRsZXIAAAABf21pbmYAAAAUdm1oZAAAAAEAAAAAAAAAAAAAACRkaW5mAAAAHGRyZWYAAAAAAAAAAQAAAAx1cmwgAAAAAQAAAT9zdGJsAAAAv3N0c2QAAAAAAAAAAQAAAK9hdmMxAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAAAAGAAQABIAAAASAAAAAAAAAABFUxhdmM2MS4xOS4xMDEgbGlieDI2NAAAAAAAAAAAAAAAGP//AAAANWF2Y0MBZAAK/+EAGGdkAAqs2UYmwEQAAAMABAAAAwAQPEiWWAEABmjr48siwP34+AAAAAAQcGFzcAAAAAEAAAABAAAAFGJ0cnQAAAAAAAAXeAAAAAAAAAAYc3R0cwAAAAAAAAABAAAAAgAAIAAAAAAUc3RzcwAAAAAAAAABAAAAAQAAABxzdHNjAAAAAAAAAAEAAAABAAAAAgAAAAEAAAAcc3RzegAAAAAAAAAAAAAAAgAAAuAAAAAPAAAAFHN0Y28AAAAAAAAAAQAAA2EAAABhdWR0YQAAAFltZXRhAAAAAAAAACFoZGxyAAAAAAAAAABtZGlyYXBwbAAAAAAAAAAAAAAAACxpbHN0AAAAJKl0b28AAAAcZGF0YQAAAAEAAAAATGF2ZjYxLjcuMTAzAAAACGZyZWUAAAL3bWRhdAAAAq0GBf//qdxF6b3m2Ui3lizYINkj7u94MjY0IC0gY29yZSAxNjQgcjMxMDggMzFlMTlmOSAtIEguMjY0L01QRUctNCBBVkMgY29kZWMgLSBDb3B5bGVmdCAyMDAzLTIwMjMgLSBodHRwOi8vd3d3LnZpZGVvbGFuLm9yZy94MjY0Lmh0bWwgLSBvcHRpb25zOiBjYWJhYz0xIHJlZj0zIGRlYmxvY2s9MTowOjAgYW5hbHlzZT0weDM6MHgxMTMgbWU9aGV4IHN1Ym1lPTcgcHN5PTEgcHN5X3JkPTEuMDA6MC4wMCBtaXhlZF9yZWY9MSBtZV9yYW5nZT0xNiBjaHJvbWFfbWU9MSB0cmVsbGlzPTEgOHg4ZGN0PTEgY3FtPTAgZGVhZHpvbmU9MjEsMTEgZmFzdF9wc2tpcD0xIGNocm9tYV9xcF9vZmZzZXQ9LTIgdGhyZWFkcz0yIGxvb2thaGVhZF90aHJlYWRzPTEgc2xpY2VkX3RocmVhZHM9MCBucj0wIGRlY2ltYXRlPTEgaW50ZXJsYWNlZD0wIGJsdXJheV9jb21wYXQ9MCBjb25zdHJhaW5lZF9pbnRyYT0wIGJmcmFtZXM9MyBiX3B5cmFtaWQ9MiBiX2FkYXB0PTEgYl9iaWFzPTAgZGlyZWN0PTEgd2VpZ2h0Yj0xIG9wZW5fZ29wPTAgd2VpZ2h0cD0yIGtleWludD0yNTAga2V5aW50X21pbj0yIHNjZW5lY3V0PTQwIGludHJhX3JlZnJlc2g9MCByY19sb29rYWhlYWQ9NDAgcmM9Y3JmIG1idHJlZT0xIGNyZj0yMy4wIHFjb21wPTAuNjAgcXBtaW49MCBxcG1heD02OSBxcHN0ZXA9NCBpcF9yYXRpbz0xLjQwIGFxPTE6MS4wMACAAAAAK2WIhAAU//7s2n4FNtyOTufSkInVjmKemJnYualmddMKRGzPC/FP46g6MskAAAALQZohbEEv/rUqjyg=','base64')});
    await page.getByTestId('video-preview').waitFor();
    await page.getByTestId('video-preview').evaluate(video=>new Promise((resolve,reject)=>{if(video.readyState>=1)return resolve();video.addEventListener('loadedmetadata',resolve,{once:true});video.addEventListener('error',()=>reject(Error('Synthetic local MP4 preview did not decode')),{once:true});}));
    assert.equal(await page.getByTestId('video-preview').evaluate(v=>v.videoWidth),96,'A real tiny generated video has a local decoded preview');
    await go(page,'clear-file');
    await page.evaluate(()=>{
      const Native=window.FileReader;window.__restoreReader=()=>{window.FileReader=Native;};
      window.FileReader=class{readAsDataURL(){window.__finishRead=()=>{this.result='data:video/mp4;base64,VEVTVA==';this.onload();};}};
    });
    await page.getByTestId('video-file').setInputFiles({name:'SYNTHETIC-interrupted.mp4',mimeType:'video/mp4',buffer:Buffer.from('TEST')});
    await go(page,'back');await go(page,'capture-text');await fill(page,'capture-input','TEST независимый текстовый черновик');
    await page.evaluate(()=>{window.__finishRead();window.__restoreReader();});
    const interrupted=await stateOf(page);assert.equal(interrupted.draft.mode,'text');assert.equal(interrupted.draft.file,null);assert.equal(interrupted.draft.text,'TEST независимый текстовый черновик');assert.equal(interrupted.captureDrafts.video.file,null);
    const before=interrupted.sources.length;
    await page.evaluate(()=>{const original=Storage.prototype.setItem;window.__restoreStorage=()=>{Storage.prototype.setItem=original;};Storage.prototype.setItem=function(){throw new DOMException('Synthetic quota test','QuotaExceededError');};});
    await go(page,'capture-save');await page.getByTestId('capture-retry').waitFor();
    assert.match(await page.getByTestId('storage-warning').innerText(),/Не сохранено на устройстве/);
    assert.match(await page.locator('#main').innerText(),/только в памяти вкладки/);
    assert.equal((await stateOf(page)).sources.length,before+1);assert.equal(await page.getByTestId('confirm-proposal').count(),0);
    await screenshot(page,engine,'daily-storage-failure',390,'Persistent storage failure is explicit; source remains only in current tab');
    await go(page,'capture-retry');assert.equal((await stateOf(page)).sources.length,before+1,'Failed retry never duplicates original');
    await page.evaluate(()=>window.__restoreStorage());await go(page,'capture-retry');await page.getByText('Нужна ручная проверка',{exact:true}).waitFor();
    assert.equal((await stateOf(page)).sources.length,before+1);assert.equal(await page.getByTestId('storage-warning').count(),0);
    await correctionReload(page);assert.equal((await stateOf(page)).sources.length,before+1);assert.equal((await stateOf(page)).sources.at(-1).text,'TEST независимый текстовый черновик');
    await go(page,'nav-home');await go(page,'capture-text');await fill(page,'capture-input','TEST предыдущий несохранённый источник');
    await page.evaluate(()=>{const original=Storage.prototype.setItem;window.__restoreStorage=()=>{Storage.prototype.setItem=original;};Storage.prototype.setItem=function(){throw new DOMException('Synthetic quota test','QuotaExceededError');};});
    await go(page,'capture-save');await page.getByTestId('capture-retry').waitFor();
    await go(page,'capture-edit');await fill(page,'capture-input','TEST новый видимый источник');await page.evaluate(()=>window.__restoreStorage());
    await go(page,'capture-save');await page.getByText('Нужна ручная проверка',{exact:true}).waitFor();
    assert.equal((await stateOf(page)).sources.at(-1).text,'TEST новый видимый источник','New visible Save never silently retries the abandoned source');
    assert.match(await page.getByTestId('source-original').innerText(),/TEST новый видимый источник/);

  });

  await journey('seven-width daily pages and financial text geometry through actual contextual navigation', async page => {
    const shot = async (name, label, width) => {
      await page.evaluate(() => window.scrollTo(0, 0)); await ready(page);
      await layout(page, name, width);
      if (name === 'finance/overview') {
        const boxes = await page.locator('.overview-cell').evaluateAll(cells => cells.map(cell => {
          const value = cell.querySelector('strong'), bounds = cell.getBoundingClientRect(), range = document.createRange();
          range.selectNodeContents(value);
          return {value: value.textContent, font: parseFloat(getComputedStyle(value).fontSize), bounds: {left: bounds.left, right: bounds.right}, lines: [...range.getClientRects()].map(r => ({left: r.left, right: r.right, top: r.top, bottom: r.bottom}))};
        }));
        assert.equal(boxes.length, 7);
        for (const cell of boxes) {
          assert.ok(cell.font >= 19, 'Finance values remain readable at all matrix widths');
          assert.equal(new Set(cell.lines.map(b => Math.round(b.top))).size, 1, `${cell.value} stays on one line`);
          for (const line of cell.lines) assert.ok(line.left >= cell.bounds.left && line.right <= cell.bounds.right && line.left >= 0 && line.right <= width, `${cell.value} glyphs stay inside their financial card`);
        }
      }
      await screenshot(page, engine, `daily-${name}`, width, label);
      report.daily_layout_checks.push({engine, width, screen: name, status: 'PASS'});
    };
    for (const width of WIDTHS) {
      await page.setViewportSize({width, height: 844}); await go(page, 'nav-home');
      if (width === 390) {
        const firstViewport = await page.getByTestId('home-expense').evaluate(el => {
          const a = el.getBoundingClientRect(), b = document.querySelector('[data-testid="home-cut"]').getBoundingClientRect(), dock = document.querySelector('.dock').getBoundingClientRect();
          return a.top >= 0 && b.top >= 0 && a.bottom < dock.top && b.bottom < dock.top;
        });
        assert.equal(firstViewport, true, 'Both direct Home actions are visible above the dock in the first 390px viewport');
      }
      await shot('home', 'Home with two direct daily business actions', width);
      await go(page, 'home-expense'); await shot('quick/expense', 'Quick Expense with optional counterparty and visible date assumption', width); await go(page, 'back');
      await go(page, 'home-cut'); await shot('quick/harvest', 'Quick Harvest with UNKNOWN weight', width); await go(page, 'back');
      await finance(page); await shot('finance/overview', 'Full-period Finance overview with explicit incomplete profit and cash', width);
      await go(page, 'nav-more'); await go(page, 'more-harvest'); await go(page, 'harvest-all-depots'); await shot('depots', 'Depot receipts and contextual actions', width);
      await inventory(page); await shot('inventory', 'Inventory balances, UNKNOWN and three contextual actions', width);
      await go(page, 'nav-tasks'); await shot('tasks', 'Personal/worker tasks with contextual creation actions', width);
    }
    report.checks.push(`${engine}: daily Home, quick Expense, quick Harvest, Finance overview, depots, inventory and tasks at all seven requested widths; actual glyph geometry and first-viewport daily actions checked`);
  });
}
