// Additive real-click journeys for the bounded fertilizer storyboard.
// No scripted state mutations: UI entries are trusted clicks and keyboard input;
// snapshots are read-only assertions. Execution belongs to the final exact-SHA run.
import assert from 'node:assert/strict';
const widths = [320, 390, 700, 768, 820, 900, 1280];
const snapshot = (page) => page.evaluate(() => window.r3Demo.state());
const ledger = (state) => ({ daily: state.daily, stock: state.stock, receivables: state.receivables, payables: state.payables, workerTasks: state.workerTasks });
const record = async (page) => (await snapshot(page)).fertilizer.records[0];

export async function runFertilizerJourneys({ browser, engine, click, fill, ready, screenshot, layout, report, correctionJourney, correctionReload }) {
  report.fertilizer_journeys ??= [];
  report.fertilizer_layout_checks ??= [];
  const journey = async (name, run) => {
    const start = report.correction_journeys.length;
    try { await correctionJourney(browser, engine, `Fertilizer: ${name}`, run); }
    finally { report.fertilizer_journeys.push(...report.correction_journeys.splice(start)); }
  };
  const go = async (page, id) => { await click(page, id); await ready(page); };
  const select = async (page, id, value) => {
    const field = page.getByTestId(id);
    const index = await field.evaluate((element, wanted) => [...element.options].findIndex((option) => option.value === wanted), value);
    assert.ok(index >= 0, `Visible ${id} offers ${value}`);
    await field.click();
    for (const key of ['Home', ...Array(index).fill('ArrowDown'), 'Enter']) {
      await page.getByTestId(id).press(key);
      await page.evaluate((pressed) => window.__r3Metrics.key(pressed), key);
    }
    assert.equal(await page.getByTestId(id).inputValue(), value);
    await page.getByTestId(id).blur(); await ready(page);
  };
  const open = async (page) => {
    await go(page, 'nav-more'); await go(page, 'more-inventory'); await go(page, 'stock-kno3');
    await screenshot(page, engine, 'fertilizer-stock-product-detail', 390, 'B05 product detail with existing stock, unit, source location and contextual task action');
    await go(page, 'stock-fertilizer');
    assert.equal((await record(page)).areaMode, 'reference', 'Real greenhouse topology is the default reference');
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
    await fill(page, 'fertilizer-dose-component-1', '1'); await go(page, 'fertilizer-all-mix-1');
    assert.equal(await page.locator('[data-fertilizer-field="part"]').count(), 4);
    assert.match(await page.getByTestId('fertilizer-warnings').innerText(), /схема полива \/ бочка неизвестны/);
    assert.match(await page.getByTestId('fertilizer-total-kno3').innerText(), /20 кг/);
    await go(page, 'fertilizer-review');
    for (const part of ['1', '2', '3', '4']) await page.getByTestId(`worker-part-mix-1-demo-reference-imamli-${part}`).waitFor();
    await fill(page, 'fertilizer-dari', 'نمونهٔ محلی: چهار بخش، هر بخش 5 kg. آبیاری و بشکه مشخص نیست. این نمونه ارسال نمی‌شود.');
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
    assert.equal(await page.locator('[data-fertilizer-field="part"]').count(), 4, 'Imamli keeps four distinct current-reference parts');
    assert.match(await page.getByTestId('fertilizer-warnings').innerText(), /схема полива \/ бочка неизвестны/);
    await select(page, 'fertilizer-area-mode', 'unknown'); await go(page, 'fertilizer-all-mix-1');
    assert.match(await page.getByTestId('fertilizer-total-kno3').innerText(), /Неизвестно/);
    assert.match(await page.getByTestId('fertilizer-issues').innerText(), /площадь неизвестна/);
    await go(page, 'fertilizer-save'); await correctionReload(page);
    assert.equal((await record(page)).areaMode, 'unknown');
    await teaching(page); await go(page, 'fertilizer-all-mix-1');
    await page.getByTestId('fertilizer-component-component-1').locator('summary').click();
    await fill(page, 'fertilizer-override-component-1-p2', '2.25');
    assert.match(await page.getByTestId('fertilizer-part-mix-1-p2').locator('..').innerText(), /Учебная P2/);
    assert.match(await page.getByTestId('fertilizer-total-kno3').innerText(), /161,25 кг/);
    await go(page, 'fertilizer-add-group'); await go(page, 'fertilizer-add-component-mix-2');
    assert.equal(await page.getByTestId('fertilizer-product-component-2').inputValue(), 'demo-multilex');
    await fill(page, 'fertilizer-dose-component-2', '5'); await go(page, 'fertilizer-part-mix-2-p2');
    assert.match(await page.getByTestId('fertilizer-total-demo-multilex').innerText(), /25 L/);
    await go(page, 'fertilizer-review');
    assert.match(await page.getByTestId('worker-part-mix-1-p2').innerText(), /11,25 кг/);
    assert.match(await page.getByTestId('worker-part-mix-2-p2').innerText(), /25 L/);
    const packetState = await record(page);
    assert.equal(packetState.groups[0].components[0].dosePerDa, '10', 'Override never changes common dose');
    assert.equal(packetState.groups[0].components[0].overrides.p2.dosePerDa, '2.25');
    await screenshot(page, engine, 'fertilizer-separate-preparations', 390, 'Separate preparations retain each part, explicit override and distinct units');
  });

  await journey('builder and owner review on seven widths, live input and interruption/reopen', async (page) => {
    await open(page); await teaching(page); await three(page); await go(page, 'fertilizer-all-mix-1');
    for (const width of widths) {
      await page.setViewportSize({ width, height: 844 }); await ready(page);
      await page.evaluate(() => window.scrollTo(0, 0));
      await layout(page, 'fertilizer/builder', width);
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
      await screenshot(page, engine, 'fertilizer-review', width, 'Owner recipient and every-part worker instructions without dispatch');
      report.fertilizer_layout_checks.push({ engine, width, screen: 'builder-and-review', status: 'PASS' });
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
  });
}
