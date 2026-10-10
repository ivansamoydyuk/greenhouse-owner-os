// Additive real-click journeys for the bounded fertilizer storyboard.
// No scripted state mutations: UI entries are trusted clicks and keyboard input;
// snapshots are read-only assertions. Execution belongs to the final exact-SHA run.
import assert from 'node:assert/strict';
const widths = [320, 390, 700, 768, 820, 900, 1280];
const snapshot = (page) => page.evaluate(() => window.r3Demo.state());
const ledger = (state) => ({ daily: state.daily, stock: state.stock, receivables: state.receivables, payables: state.payables, workerTasks: state.workerTasks });
// Scroll positions are presentation state and necessarily change when text wraps.
// Every other key, including all records, revisions, history and ledgers, stays equal.
const withoutScrollPositions = ({ positions, ...state }) => state;
const record = async (page) => (await snapshot(page)).fertilizer.records[0];

export async function runFertilizerJourneys({ browser, engine, click, fill, ready, screenshot, layout, report, correctionJourney, correctionReload }) {
  report.fertilizer_journeys ??= [];
  report.fertilizer_layout_checks ??= [];
  const journey = async (name, run) => {
    const start = report.correction_journeys.length;
    try { await correctionJourney(browser, engine, `Fertilizer: ${name}`, run); }
    finally { report.fertilizer_journeys.push(...report.correction_journeys.splice(start)); }
  };
  const reveal = async (page, id) => {
    const details = page.getByTestId(id).locator('xpath=ancestor::details');
    for (let i = 0; i < await details.count(); i++) {
      const disclosure = details.nth(i);
      if (!await disclosure.getAttribute('open')) {
        if (!await disclosure.evaluate(element => element.open)) await disclosure.locator(':scope > summary').click();
      }
    }
  };
  const go = async (page, id) => { await reveal(page, id); await click(page, id); await ready(page); };
  const readableQuantities = async (page, width) => {
    const issues = await page.evaluate(width => {
      const issues = [];
      const boxes = element => { const range = document.createRange(); range.selectNodeContents(element); return [...range.getClientRects()].filter(box => box.width && box.height); };
      for (const row of document.querySelectorAll('.calculation-line,.fertilizer-total-line .summary-pair,.quantity-inline')) {
        if (!row.getClientRects().length) continue;
        const bounds = row.getBoundingClientRect();
        for (const text of row.querySelectorAll('strong,small,b,:scope > span')) for (const box of boxes(text)) {
          if (box.left < Math.max(0,bounds.left) - 1 || box.right > Math.min(width,bounds.right) + 1) issues.push(`Quantity/name glyphs escape row: ${text.textContent}`);
        }
        const left = row.querySelector(':scope > strong'), right = row.querySelector(':scope > span,:scope > strong:last-child');
        if (left && right && left !== right) for (const a of boxes(left)) for (const b of boxes(right)) {
          if (a.left < b.right - 1 && a.right > b.left + 1 && a.top < b.bottom - 1 && a.bottom > b.top + 1) issues.push(`Name and quantity overlap: ${row.textContent}`);
        }
      }
      return issues;
    }, width);
    assert.deepEqual(issues, [], `Readable independent amounts/units at ${width}px`);
  };
  const select = async (page, id, value) => {
    await reveal(page, id);
    const field = page.getByTestId(id);
    const index = await field.evaluate((element, wanted) => [...element.options].findIndex((option) => option.value === wanted), value);
    assert.ok(index >= 0, `Visible ${id} offers ${value}`);
    await field.click();
    // Keep the native keyboard sequence fast: each change may rerender the
    // control, and the app must preserve its disclosure/focus between keys.
    for (const key of ['Home', ...Array(index).fill('ArrowDown'), 'Enter']) {
      await page.getByTestId(id).press(key);
      await page.evaluate((pressed) => window.__r3Metrics.key(pressed), key);
    }
    assert.equal(await page.getByTestId(id).inputValue(), value);
    await page.getByTestId(id).blur(); await ready(page);
  };
  const retainedDisclosures = async (page, step) => {
    for (const id of ['fertilizer-source-details', 'fertilizer-part-details']) {
      assert.equal(await page.getByTestId(id).evaluate(element => element.open), true, `${id} remains open ${step}`);
    }
  };
  const open = async (page) => {
    await go(page, 'nav-more'); await go(page, 'more-inventory'); await go(page, 'stock-kno3');
    await screenshot(page, engine, 'fertilizer-stock-product-detail', 390, 'B05 product detail with existing stock, unit, source location and contextual task action');
    await go(page, 'stock-fertilizer');
    assert.equal((await record(page)).areaMode, 'reference', 'Real greenhouse topology is the default reference');
    assert.deepEqual((await record(page)).groups[0].partIds, [], 'Stock location never selects a target part');
    assert.equal(await page.locator('[data-fertilizer-field="part"]:checked').count(), 0);
    assert.match(await page.getByTestId('fertilizer-selected-scope').innerText(), /Выберите части/);
    assert.equal((await record(page)).groups[0].components[0].dosePerDa, '', '180kg stock is not a dosage');
    assert.equal(await page.getByTestId('fertilizer-worker-override').count(), 0, 'No routine worker picker');
  };
  const teaching = async (page) => {
    await select(page, 'fertilizer-area-mode', 'synthetic');
    await page.getByTestId('fertilizer-teaching-mode').waitFor();
    await go(page, 'fertilizer-part-mix-1-p1');
  };
  const three = async (page) => {
    await fill(page, 'fertilizer-dose-component-1', '10');
    await go(page, 'fertilizer-add-component-mix-1');
    assert.equal(await page.getByTestId('fertilizer-product-component-2').inputValue(), 'demo-multilex');
    await fill(page, 'fertilizer-dose-component-2', '5');
    await go(page, 'fertilizer-add-component-mix-1');
    assert.equal(await page.getByTestId('fertilizer-product-component-3').inputValue(), 'demo-atocrop');
    await fill(page, 'fertilizer-dose-component-3', '2');
  };
  const reviewChecks = async (page) => {
    await fill(page, 'fertilizer-dari', 'این یک نمونهٔ آموزشی است. P1: KNO3 50 kg، Multilex 25 L، Atocrop 10 kg. P2: KNO3 50 kg، Multilex 25 L، Atocrop 10 kg. P3: KNO3 100 kg، Multilex 50 L، Atocrop 20 kg.');
    await go(page, 'fertilizer-dari-reviewed'); await go(page, 'fertilizer-owner-reviewed');
    if (await page.getByTestId('fertilizer-warnings-reviewed').count()) await go(page, 'fertilizer-warnings-reviewed');
  };

  await journey('contextual 3-product task, every-part review, manual Dari, single confirm, reload and DONE isolation', async (page) => {
    const before = ledger(await snapshot(page)); await open(page); await teaching(page); await three(page);
    assert.match(await page.getByTestId('fertilizer-total-kno3').innerText(), /50 кг/);
    assert.match(await page.getByTestId('fertilizer-total-demo-multilex').innerText(), /25 L/);
    await go(page, 'fertilizer-part-mix-1-p2');
    assert.match(await page.getByTestId('fertilizer-total-kno3').innerText(), /100 кг/);
    await go(page, 'fertilizer-all-mix-1');
    assert.deepEqual((await record(page)).groups[0].partIds, ['p1', 'p2', 'p3']);
    assert.match(await page.getByTestId('fertilizer-total-kno3').innerText(), /200 кг/);
    assert.match(await page.getByTestId('fertilizer-total-demo-multilex').innerText(), /100 L/);
    assert.match(await page.getByTestId('fertilizer-total-demo-atocrop').innerText(), /40 кг/);
    assert.match(await page.getByTestId('fertilizer-warnings').innerText(), /Не хватает/);
    await go(page, 'fertilizer-save'); await correctionReload(page);
    assert.equal((await snapshot(page)).fertilizer.records.length, 1);
    await go(page, 'fertilizer-review');
    assert.match(await page.getByTestId('fertilizer-review-worker').innerText(), /Мухаммед/);
    for (const part of ['p1', 'p2', 'p3']) await page.getByTestId(`worker-part-mix-1-${part}`).waitFor();
    await go(page, 'fertilizer-confirm'); assert.equal((await record(page)).status, 'draft', 'Unreviewed Dari cannot confirm');
    await reviewChecks(page);
    await screenshot(page, engine, 'fertilizer-owner-review', 390, 'Three-component, each-part task review; stock shortage acknowledged and Dari manually reviewed in DEMO');
    await go(page, 'fertilizer-confirm'); await page.getByTestId('fertilizer-confirmed').waitFor();
    assert.equal((await record(page)).status, 'confirmed');
    assert.deepEqual(ledger(await snapshot(page)), before, 'Task creation makes no inventory or finance effect');
    await correctionReload(page);
    assert.equal(await page.getByTestId('fertilizer-confirm').count(), 0, 'Reload cannot create another confirm');
    assert.equal((await snapshot(page)).fertilizer.records.length, 1);
    await go(page, 'fertilizer-done'); await page.getByTestId('fertilizer-done-status').waitFor();
    await correctionReload(page); assert.equal((await record(page)).status, 'done');
    assert.equal((await record(page)).workerReport.ownerAcceptedActual, false);
    assert.deepEqual(ledger(await snapshot(page)), before, 'DONE and reload leave all physical and financial state intact');
    await go(page, 'fertilizer-stock-return'); await page.getByTestId('fertilizer-task-fertilizer-1').waitFor();
    await go(page, 'fertilizer-task-fertilizer-1');
    assert.equal((await snapshot(page)).fertilizer.records.length, 1);
    report.checks.push(`${engine}: fertilizer contextual entry, exact 50/100/200kg, mixed units, each-part worker packet, explicit manual Dari gate, one durable task and DONE with zero ledger effects`);
  });

  await journey('normal CURRENT topology supports reviewed per-part DEMO task with unknown physical mapping', async (page) => {
    const before = ledger(await snapshot(page)); await open(page);
    const draftId = (await record(page)).id, source = (await record(page)).source;
    await fill(page, 'fertilizer-dose-component-1', '3');
    assert.equal(await page.getByTestId('fertilizer-total-kno3').count(), 0, 'Dose alone creates no scoped total');
    await go(page, 'fertilizer-save'); await correctionReload(page);
    assert.equal((await record(page)).id, draftId); assert.deepEqual((await record(page)).groups[0].partIds, []);
    await go(page, 'back');
    await page.getByTestId('stock-fertilizer').waitFor(); await go(page, 'stock-fertilizer');
    assert.equal((await record(page)).id, draftId); assert.equal((await snapshot(page)).fertilizer.records.length, 1);
    await go(page, 'fertilizer-review'); await go(page, 'fertilizer-confirm');
    assert.equal((await record(page)).status, 'draft', 'Empty scope cannot confirm');
    await go(page, 'fertilizer-edit');
    await fill(page, 'fertilizer-dose-component-1', '1'); await go(page, 'fertilizer-all-mix-1');
    assert.equal(await page.locator('[data-fertilizer-field="part"]').count(), 4);
    assert.match(await page.getByTestId('fertilizer-warnings').innerText(), /схема полива \/ бочка неизвестны/);
    assert.match(await page.getByTestId('fertilizer-total-kno3').innerText(), /20 кг/);
    await go(page, 'fertilizer-all-mix-1');
    await fill(page, 'fertilizer-dose-component-1', '3');
    await go(page, 'fertilizer-add-component-mix-1'); await fill(page, 'fertilizer-dose-component-2', '2');
    await go(page, 'fertilizer-add-component-mix-1'); await fill(page, 'fertilizer-dose-component-3', '1');
    for (const [target, values] of [
      ['fertilizer-part-mix-1-demo-reference-imamli-1', ['15 кг', '10 L', '5 кг']],
      ['fertilizer-part-mix-1-demo-reference-imamli-2', ['30 кг', '20 L', '10 кг']],
      ['fertilizer-all-mix-1', ['60 кг', '40 L', '20 кг']],
    ]) {
      await go(page, target);
      for (const [index, product] of ['kno3', 'demo-multilex', 'demo-atocrop'].entries()) {
        assert.ok((await page.getByTestId(`fertilizer-total-${product}`).innerText()).includes(values[index]));
      }
    }
    const documentMetrics = async () => page.evaluate(() => {
      const at = selector => { const e = document.querySelector(selector); if (!e) return null; const b = e.getBoundingClientRect(); return { top: b.top + scrollY, height: b.height }; };
      return { height: document.documentElement.scrollHeight, cta: at('[data-testid="fertilizer-review"],[data-testid="fertilizer-confirm"]'), totals: at('.fertilizer-totals'), recipient: at('[data-testid="fertilizer-auto-worker"],[data-testid="fertilizer-review-worker"]'), dari: at('[data-testid="fertilizer-dari"]') };
    });
    const builder = await documentMetrics(); assert.ok(builder.height <= 2900, `CURRENT compact editor ${builder.height}px exceeds 2900px`);
    await screenshot(page, engine, 'fertilizer-current-builder', 390, 'CURRENT four 5da parts, 3kg/2L/1kg per da, zero automatic scope and explicit one/two/all selection');
    await go(page, 'fertilizer-review');
    for (const id of ['1', '2', '3', '4']) {
      const part = await page.getByTestId(`worker-part-mix-1-demo-reference-imamli-${id}`).innerText();
      for (const quantity of ['15 кг', '10 L', '5 кг']) assert.ok(part.includes(quantity), `${id}: ${quantity}`);
    }
    const baselineDari = 'نمونهٔ آزمایشی، نه دستور واقعی: چهار بخش ۵ da. برای هر بخش KNO3 ۱۵ kg، Multilex ۱۰ L، Atocrop ۵ kg. آبیاری و بشکه نامعلوم است.';
    await fill(page, 'fertilizer-dari', baselineDari);
    const reviewGeometry = await documentMetrics(); assert.ok(reviewGeometry.height <= 2700, `CURRENT compact review ${reviewGeometry.height}px exceeds 2700px`);
    report.fertilizer_compression ??= [];
    report.fertilizer_compression.push({engine,width:390,height:844,fixture:'CURRENT İmamlı 4×5da; KNO3 3kg/da, Multilex 2L/da, Atocrop 1kg/da',baseline_dari:baselineDari,builder,review:reviewGeometry});
    assert.deepEqual((await record(page)).source, source);
    await screenshot(page, engine, 'fertilizer-current-review', 390, 'Full CURRENT per-part review with original baseline Dari length, separate units and UNKNOWN physical mapping');
    for (const part of ['1', '2', '3', '4']) await page.getByTestId(`worker-part-mix-1-demo-reference-imamli-${part}`).waitFor();
    assert.equal(await page.getByTestId('fertilizer-dari').inputValue(), baselineDari);
    await go(page, 'fertilizer-dari-reviewed'); await go(page, 'fertilizer-owner-reviewed');
    await go(page, 'fertilizer-confirm');
    assert.equal((await record(page)).status, 'draft', 'Unknown physical mapping needs explicit warning acknowledgement');
    await go(page, 'fertilizer-warnings-reviewed'); await go(page, 'fertilizer-confirm');
    await page.getByTestId('fertilizer-confirmed').waitFor();
    const task = await record(page);
    assert.equal(task.status, 'confirmed'); assert.equal(task.confirmedPacket.areaMode, 'reference');
    assert.equal(task.confirmedPacket.groups[0].parts.length, 4);
    for (const part of task.confirmedPacket.groups[0].parts) { assert.equal(part.barrelGroupId, null); assert.equal(part.irrigationGroupId, null); }
    assert.deepEqual(ledger(await snapshot(page)), before);
    await screenshot(page, engine, 'fertilizer-current-parts-task', 390, 'Normal CURRENT topology: four per-part quantities, unknown physical mappings acknowledged, local DEMO only');
  });

  await journey('CURRENT topology, UNKNOWN safety, separate preparation and explicit one-part override', async (page) => {
    await open(page); await fill(page, 'fertilizer-dose-component-1', '10');
    await go(page, 'fertilizer-part-mix-1-demo-reference-imamli-1');
    assert.equal(await page.locator('[data-fertilizer-field="part"]').count(), 4, 'Imamli keeps four distinct current-reference parts');
    assert.match(await page.getByTestId('fertilizer-warnings').innerText(), /схема полива \/ бочка неизвестны/);
    await select(page, 'fertilizer-area-mode', 'unknown'); await go(page, 'fertilizer-all-mix-1');
    assert.match(await page.getByTestId('fertilizer-total-kno3').innerText(), /Неизвестно/);
    assert.match(await page.getByTestId('fertilizer-issues').innerText(), /площадь неизвестна/);
    await go(page, 'fertilizer-review'); await reviewChecks(page);
    await go(page, 'fertilizer-confirm'); assert.equal((await record(page)).status, 'draft', 'UNKNOWN area never confirms even after all review checks');
    await go(page, 'fertilizer-edit');
    await go(page, 'fertilizer-save'); await correctionReload(page);
    assert.equal((await record(page)).areaMode, 'unknown');
    await teaching(page); await go(page, 'fertilizer-all-mix-1');
    await page.getByTestId('fertilizer-component-component-1').locator('summary').click();
    await fill(page, 'fertilizer-override-component-1-p2', '2.25');
    assert.match(await page.getByTestId('fertilizer-part-mix-1-p2').locator('..').innerText(), /Учебная P2/);
    assert.match(await page.getByTestId('fertilizer-total-kno3').innerText(), /161,25 кг/);
    await page.getByTestId('fertilizer-component-component-1').locator('summary').click();
    assert.match(await page.getByTestId('fertilizer-override-state-component-1').innerText(), /изменено: 1/i);
    assert.equal(await page.getByTestId('fertilizer-override-component-1-p2').isVisible(), false, 'Closed override still has visible changed-state indication');
    await go(page, 'fertilizer-add-group'); await go(page, 'fertilizer-add-component-mix-2');
    assert.equal(await page.getByTestId('fertilizer-product-component-2').inputValue(), 'demo-multilex');
    await fill(page, 'fertilizer-dose-component-2', '5'); await go(page, 'fertilizer-part-mix-2-p2');
    const longLabel = 'Отдельное приготовление для второй части: исходные дозы, литры и килограммы не объединять; физические бочки остаются неизвестны';
    await page.locator('[data-fertilizer-field="groupLabel"][data-group-id="mix-2"]').fill(longLabel);
    await page.locator('[data-fertilizer-field="groupLabel"][data-group-id="mix-2"]').blur();
    assert.match(await page.getByTestId('fertilizer-total-demo-multilex').innerText(), /25 L/);
    await go(page, 'fertilizer-review');
    assert.match(await page.getByTestId('worker-part-mix-1-p2').innerText(), /11,25 кг/);
    assert.match(await page.getByTestId('worker-part-mix-2-p2').innerText(), /25 L/);
    const packetState = await record(page);
    assert.equal(packetState.groups[0].components[0].dosePerDa, '10', 'Override never changes common dose');
    assert.equal(packetState.groups[0].components[0].overrides.p2.dosePerDa, '2.25');
    await layout(page, 'fertilizer/separate-preparations-long-label', 390);
    assert.ok((await page.getByTestId('fertilizer-group-preview-mix-2').innerText()).includes(longLabel));
    await screenshot(page, engine, 'fertilizer-separate-preparations', 390, 'Separate preparations retain each part, explicit override, long label and distinct units');
    await go(page, 'fertilizer-edit'); await go(page, 'fertilizer-add-component-mix-2');
    await select(page, 'fertilizer-product-component-3', 'kno3'); await fill(page, 'fertilizer-dose-component-3', '1');
    assert.match(await page.getByTestId('fertilizer-issues').innerText(), /повтор/i);
    assert.match(await page.getByTestId('fertilizer-total-kno3').innerText(), /Неизвестно/);
    await go(page, 'fertilizer-review'); await reviewChecks(page);
    await go(page, 'fertilizer-confirm'); assert.equal((await record(page)).status, 'draft', 'Overlapping product/part scope blocks confirmation');
  });

  await journey('builder and owner review on seven widths, live input and interruption/reopen', async (page) => {
    await open(page); await teaching(page); await three(page); await go(page, 'fertilizer-all-mix-1');
    for (const width of widths) {
      await page.setViewportSize({ width, height: 844 }); await ready(page);
      await page.evaluate(() => window.scrollTo(0, 0));
      await layout(page, 'fertilizer/builder', width);
      await readableQuantities(page, width);
      const componentGeometry = await page.locator('.mix-component').evaluateAll(rows => rows.map(row => {
        const box = row.getBoundingClientRect(), select = row.querySelector('select'), dose = row.querySelector('[data-fertilizer-field="dosePerDa"]');
        return { height: box.height, width: box.width, selectHeight: select.getBoundingClientRect().height, doseHeight: dose.getBoundingClientRect().height, font: parseFloat(getComputedStyle(select).fontSize), stock: row.querySelector('.component-stock').textContent };
      }));
      assert.equal(componentGeometry.length, 3);
      for (const item of componentGeometry) {
        assert.ok(item.height <= 230, `Compact component at ${width}px: ${item.height}px, without hiding facts`);
        assert.ok(item.selectHeight >= 48 && item.doseHeight >= 48, 'Compact controls retain 48px targets');
        assert.ok(item.font >= 16, 'Product stays readable');
        assert.match(item.stock, /запас/);
      }

      await screenshot(page, engine, 'fertilizer-builder', width, 'B06 compact multiple fertilizers with per-da doses and separate teaching topology');
      await go(page, 'fertilizer-review');
      await layout(page, 'fertilizer/review', width);
      await readableQuantities(page, width);
      await screenshot(page, engine, 'fertilizer-review', width, 'Owner recipient and every-part worker instructions without dispatch');
      report.fertilizer_layout_checks.push({ engine, width, screen: 'builder-and-review', status: 'PASS' });
      const longDari = 'نمونهٔ آموزشی، نه دستور واقعی. P1: KNO3 50 kg، Multilex 25 L، Atocrop 10 kg. P2: KNO3 50 kg، Multilex 25 L، Atocrop 10 kg. P3: KNO3 100 kg، Multilex 50 L، Atocrop 20 kg. ارسال نشود. '.repeat(5).trim();
      await fill(page, 'fertilizer-dari', longDari);
      assert.equal(await page.getByTestId('fertilizer-dari').inputValue(), longDari);
      const textGeometry = await page.getByTestId('fertilizer-dari').evaluate(element => ({client:element.clientHeight,scroll:element.scrollHeight,overflow:getComputedStyle(element).overflowY}));
      assert.ok(textGeometry.scroll <= textGeometry.client + 2, `Complete Dari readable without nested scroll at ${width}px: ${JSON.stringify(textGeometry)}`);
      // Presentation-only 125% text-size stress: never mutate the fixture or business state.
      const beforeTextScale = await snapshot(page);
      await page.evaluate(() => {
        const rows = [...document.querySelectorAll('.fertilizer-review h1,.fertilizer-review h2,.fertilizer-review h3,.fertilizer-review p,.fertilizer-review span,.fertilizer-review strong,.fertilizer-review small,.fertilizer-review label,.fertilizer-review button,.fertilizer-review a,.fertilizer-review summary,.fertilizer-review dt,.fertilizer-review dd,.fertilizer-review textarea')].map(element => [element, parseFloat(getComputedStyle(element).fontSize)]);
        for (const [element,size] of rows) element.style.fontSize = `${size * 1.25}px`;
      });
      // A browser/user text-size change must expand the complete translation
      // without depending on an unrelated viewport resize or another keystroke.
      await ready(page);
      await layout(page, 'fertilizer/review-enhanced-text', width);
      await readableQuantities(page, width);
      assert.deepEqual(withoutScrollPositions(await snapshot(page)), withoutScrollPositions(beforeTextScale), 'Text scaling leaves every non-scroll state field unchanged');
      const expandedDari = await page.getByTestId('fertilizer-dari').evaluate(element => {
        const style = getComputedStyle(element), box = element.getBoundingClientRect();
        return {client:element.clientHeight,scroll:element.scrollHeight,width:box.width,height:box.height,font_size:style.fontSize,line_height:style.lineHeight,inline_height:element.style.height,overflow_y:style.overflowY,viewport_width:innerWidth,viewport_height:innerHeight};
      });
      report.fertilizer_text_checks ??= [];
      report.fertilizer_text_checks.push({engine,width,text_scale:1.25,dari_characters:longDari.length,font_change_without_viewport_resize:true,geometry:expandedDari,status:expandedDari.scroll <= expandedDari.client + 2 ? 'PASS' : 'FAIL'});
      assert.ok(expandedDari.scroll <= expandedDari.client + 2, `Scaled Dari remains fully expanded without viewport resize at ${width}px: ${JSON.stringify(expandedDari)}`);
      await screenshot(page, engine, 'fertilizer-review-enhanced-text', width, '125% text size and complete long Dari; presentation stress only, no fixture injection');
      await go(page, 'fertilizer-edit');
    }
    await page.setViewportSize({ width: 390, height: 844 });
    // A real text-entry blur followed immediately by the CTA must not lose its click.
    await page.getByTestId('fertilizer-dose-component-1').fill('9.5');
    await go(page, 'fertilizer-review');
    assert.match(await page.getByTestId('worker-part-mix-1-p1').innerText(), /47,5 кг/);
    await go(page, 'fertilizer-edit'); await go(page, 'fertilizer-save'); await correctionReload(page);
    assert.equal(await page.getByTestId('fertilizer-dose-component-1').inputValue(), '9.5');
    assert.equal((await snapshot(page)).fertilizer.records.length, 1);
    const id = (await record(page)).id, source = (await record(page)).source;
    const original = 'Полная исходная инструкция: каждая часть отдельно. Дозы указаны на 1 da; литры не складывать с килограммами. '.repeat(4).trim();
    await reveal(page, 'fertilizer-instruction'); await fill(page, 'fertilizer-instruction', original);
    await page.getByTestId('fertilizer-part-details').locator('summary').click();
    await page.getByTestId('fertilizer-dose-component-1').click();
    await page.getByTestId('fertilizer-dose-component-1').press('End');
    const scrollBefore = await page.evaluate(() => scrollY);
    await page.keyboard.type('0'); await ready(page);
    assert.equal(await page.getByTestId('fertilizer-dose-component-1').inputValue(), '9.50');
    assert.equal(await page.getByTestId('fertilizer-dose-component-1').evaluate(element => element === document.activeElement), true, 'Live totals never replace active dose field');
    assert.ok(Math.abs(await page.evaluate(() => scrollY) - scrollBefore) < 3, 'Live typing preserves scroll');
    assert.equal(await page.getByTestId('fertilizer-part-details').evaluate(element => element.open), true, 'Live typing preserves formula disclosure');
    const partControl = page.getByTestId('fertilizer-part-mix-1-p2');
    await partControl.scrollIntoViewIfNeeded(); await ready(page);
    const checkboxScroll = await page.evaluate(() => scrollY);
    await go(page, 'fertilizer-part-mix-1-p2');
    assert.equal(await page.getByTestId('fertilizer-part-mix-1-p2').evaluate(element => element === document.activeElement), true, 'Full checkbox rerender restores the changed control’s focus');
    assert.ok(Math.abs(await page.evaluate(() => scrollY) - checkboxScroll) < 3, 'Full checkbox rerender preserves scroll');
    for (const disclosure of ['fertilizer-source-details','fertilizer-part-details']) assert.equal(await page.getByTestId(disclosure).evaluate(element => element.open), true, `${disclosure} survives full checkbox rerender`);
    assert.equal(await page.getByTestId('fertilizer-dose-component-1').inputValue(), '9.50');
    await go(page, 'fertilizer-part-mix-1-p2');
    await go(page, 'fertilizer-review'); await reviewChecks(page);
    for (const flag of ['dariReviewed','ownerReviewed','warningsReviewed']) assert.equal((await record(page))[flag], true);
    await fill(page, 'fertilizer-dari', 'متن تازه؛ هنوز بررسی نشده است. ارسال نشود.');
    for (const flag of ['dariReviewed','ownerReviewed','warningsReviewed']) assert.equal((await record(page))[flag], false, `Dari correction invalidates ${flag}`);
    assert.equal(await page.getByTestId('fertilizer-dari-reviewed').isChecked(), false);
    await page.getByTestId('fertilizer-review-invalidated').waitFor();
    await go(page, 'fertilizer-confirm'); assert.equal((await record(page)).status, 'draft');
    await reviewChecks(page);
    await go(page, 'fertilizer-edit');
    await fill(page, 'fertilizer-dose-component-1', '9.4');
    for (const flag of ['dariReviewed','ownerReviewed','warningsReviewed']) assert.equal((await record(page))[flag], false, `Dose correction invalidates ${flag}`);
    await go(page, 'fertilizer-review');
    for (const checkbox of ['fertilizer-dari-reviewed','fertilizer-owner-reviewed','fertilizer-warnings-reviewed']) assert.equal(await page.getByTestId(checkbox).isChecked(), false);
    await page.getByTestId('fertilizer-review-invalidated').waitFor();
    await go(page, 'fertilizer-confirm'); assert.equal((await record(page)).status, 'draft', 'Old review authority cannot survive a changed dose');
    await reviewChecks(page); await go(page, 'fertilizer-edit');
    await go(page, 'fertilizer-part-mix-1-p2');
    for (const flag of ['dariReviewed','ownerReviewed','warningsReviewed']) assert.equal((await record(page))[flag], false, `Part correction invalidates ${flag}`);
    await go(page, 'fertilizer-review');
    assert.equal(await page.getByTestId('fertilizer-owner-reviewed').isChecked(), false);
    await page.getByTestId('fertilizer-review-invalidated').waitFor();
    await go(page, 'fertilizer-confirm'); assert.equal((await record(page)).status, 'draft', 'Old review authority cannot survive changed parts');
    await go(page, 'fertilizer-edit'); await go(page, 'fertilizer-part-mix-1-p2');
    await fill(page, 'fertilizer-dose-component-1', '9.50'); await go(page, 'fertilizer-review'); await reviewChecks(page);
    await select(page, 'fertilizer-worker-override', 'umar');
    for (const flag of ['dariReviewed','ownerReviewed','warningsReviewed']) assert.equal((await record(page))[flag], false, `Recipient correction invalidates ${flag}`);
    await fill(page, 'fertilizer-worker-reason', 'Синтетическая проверка замены получателя');
    await reviewChecks(page); await go(page, 'fertilizer-edit');
    await reveal(page, 'fertilizer-instruction'); await fill(page, 'fertilizer-instruction', `${original} Уточнение.`);
    assert.equal((await record(page)).ownerReviewed, false); assert.equal((await record(page)).dariReviewed, false);
    await select(page, 'fertilizer-greenhouse', 'yesiloz');
    await retainedDisclosures(page, 'after native greenhouse keyboard changes');
    assert.deepEqual((await record(page)).groups[0].partIds, []);
    assert.match(await page.getByTestId('fertilizer-context-reset').innerText(), /част|выберите/i);
    assert.match(await page.getByTestId('fertilizer-auto-worker').innerText(), /Умар/);
    assert.deepEqual((await record(page)).source, source);
    assert.equal(await page.getByTestId('fertilizer-dose-component-1').inputValue(), '9.50');
    await select(page, 'fertilizer-area-mode', 'reference');
    await retainedDisclosures(page, 'after native library keyboard changes');
    assert.equal(await page.locator('[data-fertilizer-field="part"]').count(), 5);
    assert.deepEqual((await record(page)).groups[0].partIds, []);
    await go(page, 'fertilizer-part-mix-1-demo-reference-yesiloz-1');
    await go(page, 'fertilizer-save'); await correctionReload(page);
    await retainedDisclosures(page, 'immediately after reload');
    assert.equal((await record(page)).id, id); assert.equal((await snapshot(page)).fertilizer.records.length, 1);
    assert.deepEqual((await record(page)).groups[0].partIds, ['demo-reference-yesiloz-1']);
    const beforeRemove = (await record(page)).groups[0].components;
    await go(page, 'fertilizer-add-component-mix-1');
    await retainedDisclosures(page, 'after adding a component');
    const extra = (await record(page)).groups[0].components.at(-1).id;
    await go(page, `fertilizer-remove-${extra}`);
    assert.deepEqual((await record(page)).groups[0].components, beforeRemove, 'Removing the added component preserves all existing doses and provenance');
    assert.equal(await page.getByTestId('fertilizer-add-component-mix-1').evaluate(element => element === document.activeElement), true, 'Deleting the focused component returns focus to the same group’s Add fertilizer action');
    for (const disclosure of ['fertilizer-source-details','fertilizer-part-details']) assert.equal(await page.getByTestId(disclosure).evaluate(element => element.open), true, `${disclosure} survives reload and add/remove rerenders`);
  });
}
