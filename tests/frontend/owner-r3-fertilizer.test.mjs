import test from 'node:test';
import assert from 'node:assert/strict';
import { initialState } from '../../prototype/owner-r3/fixtures.js';
import { ensureDaily, projectDaily } from '../../prototype/owner-r3/daily-demo.js';
import { loadState, persist } from '../../prototype/owner-r3/demo-adapter.js';
import {
  ensureFertilizer, fertilizerParts, fertilizerProducts, responsibleWorker, startFertilizerDraft,
  fertilizerRecord, updateFertilizerField, addFertilizerComponent, removeFertilizerComponent,
  addFertilizerGroup, removeFertilizerGroup, calculateFertilizer, fertilizerWorkerPacket,
  confirmFertilizer, markFertilizerDone, decimal, multiplyDecimals, sumDecimals,
} from '../../prototype/owner-r3/fertilizer-demo.js';
import { renderFertilizer, handleFertilizerEvent, fertilizerTaskRows } from '../../prototype/owner-r3/fertilizer-ui.js';
const fresh = () => { const state = initialState(); ensureDaily(state); projectDaily(state); ensureFertilizer(state); return state; };
const set = (state, record, field, value, context = {}) => updateFertilizerField(state, record.id, field, value, context);
const dose = (state, record, value, component = record.groups[0].components[0], group = record.groups[0]) => set(state, record, 'dosePerDa', value, { groupId: group.id, componentId: component.id });
const example = () => { const state = fresh(), record = startFertilizerDraft(state, 'kno3'); set(state, record, 'areaMode', 'synthetic'); set(state, record, 'part', true, { groupId: 'mix-1', partId: 'p1' }); return { state, record }; };
const reviewed = (state, record) => {
  set(state, record, 'dari', 'متن آزمایشی؛ مقدار هر بخش و واحدها توسط مالک بررسی شده است.');
  set(state, record, 'dariReviewed', true); set(state, record, 'ownerReviewed', true); set(state, record, 'warningsReviewed', true);
};
const addThree = (state, record) => {
  addFertilizerComponent(state, record.id, 'mix-1', 'demo-multilex');
  addFertilizerComponent(state, record.id, 'mix-1', 'demo-atocrop');
  ['10', '5', '2'].forEach((value, index) => dose(state, record, value, record.groups[0].components[index]));
};
const ledger = (state) => structuredClone({ daily: state.daily, stock: state.stock, receivables: state.receivables, payables: state.payables, workerTasks: state.workerTasks });

test('additive namespace leaves existing local state and stock baseline intact', () => {
  const state = fresh(), before = ledger(state); ensureFertilizer(state);
  assert.deepEqual(ledger(state), before); assert.equal(state.daily.inventory.find((row) => row.id === 'kno3').quantity, 180);
  const record = startFertilizerDraft(state, 'kno3'); ensureFertilizer(state);
  assert.equal(state.fertilizer.records.length, 1); assert.equal(fertilizerRecord(state, record.id), record);
});

test('CURRENT topology preserves four Imamli parts and distinct canonical reference areas; no invented operational IDs/barrels', () => {
  assert.deepEqual(fertilizerParts('imamli').map((part) => part.areaDa), ['5', '5', '5', '5']);
  assert.deepEqual(fertilizerParts('yesiloz').map((part) => part.areaDa), ['3', '5', '5', '3.5', '3.5']);
  assert.deepEqual(fertilizerParts('ozvadi').map((part) => part.areaDa), ['7', '7', '6', '6', '4']);
  for (const greenhouse of ['imamli', 'yesiloz', 'ozvadi']) for (const part of fertilizerParts(greenhouse)) {
    assert.ok(part.id.startsWith('demo-reference-')); assert.equal(part.operationalIdentityVerified, false);
    assert.equal(part.irrigationGroupId, null); assert.equal(part.barrelGroupId, null);
  }
  const state = fresh(), record = startFertilizerDraft(state, 'kno3'); dose(state, record, '10');
  assert.equal(record.areaMode, 'reference');
  assert.equal(calculateFertilizer(state, record).needs[0].quantity, '50');
  assert.match(calculateFertilizer(state, record).warnings.join(' '), /схема полива/);
  assert.equal(calculateFertilizer(state, record).calculable, true);
  assert.match(renderFertilizer(state, record.id), /CURRENT.*справочная/);
});

test('5/5/10 fixture is explicit separate teaching mode, never default greenhouse topology', () => {
  const parts = fertilizerParts('imamli', 'synthetic');
  assert.deepEqual(parts.map((part) => part.areaDa), ['5', '5', '10']);
  assert.ok(parts.every((part) => part.synthetic && part.provenance === 'verified-synthetic-fixture-v1'));
  const { state, record } = example();
  assert.match(renderFertilizer(state, record.id), /не топология выбранной теплицы/);
});

test('product entry prefills identity, source location and unit but never turns stock into a dose', () => {
  const state = fresh(), record = startFertilizerDraft(state, 'kno3-sun'), component = record.groups[0].components[0];
  assert.equal(record.greenhouseId, 'ozvadi'); assert.equal(component.productId, 'kno3');
  assert.equal(component.stockId, 'kno3-sun'); assert.equal(component.unit, 'kg');
  assert.equal(component.stockLocationId, 'stock-location-ozvadi'); assert.equal(component.dosePerDa, '');
  assert.equal(record.source.quantity, '20');
});

test('precise decimal arithmetic includes fractional areas/doses and rejects ambiguous/negative/rounded input', () => {
  assert.equal(decimal('0010,250000'), '10.25');
  assert.equal(multiplyDecimals('0.1', '0.2'), '0.02');
  assert.equal(multiplyDecimals('1.234567', '3.5'), '4.3209845');
  assert.equal(multiplyDecimals('0.000001', '0.000001'), '0.000000000001');
  assert.equal(sumDecimals(['50', '50', '100']), '200');
  assert.equal(sumDecimals(['0.1', '0.2', '0.003']), '0.303');
  for (const invalid of ['-1', '1e2', 'NaN', 'Infinity', '1.1234567', '1,000.00', '']) assert.throws(() => decimal(invalid));
  assert.throws(() => decimal('0', { positive: true }));
});

test('three products, kg and L: P1 then P1+P2 then all conserve exact part and owner totals', () => {
  const { state, record } = example(); addThree(state, record);
  let result = calculateFertilizer(state, record);
  assert.deepEqual(result.needs.map((need) => need.quantity), ['50', '25', '10']); assert.equal(result.totalAreaDa, '5');
  set(state, record, 'part', true, { groupId: 'mix-1', partId: 'p2' }); result = calculateFertilizer(state, record);
  assert.deepEqual(result.needs.map((need) => need.quantity), ['100', '50', '20']); assert.equal(result.totalAreaDa, '10');
  set(state, record, 'allParts', true, { groupId: 'mix-1' }); result = calculateFertilizer(state, record);
  assert.deepEqual(result.needs.map((need) => need.quantity), ['200', '100', '40']); assert.equal(result.totalAreaDa, '20');
  assert.deepEqual(result.needs.map((need) => need.unit), ['kg', 'L', 'kg']);
  assert.equal(result.needs[0].stockStatus, 'insufficient'); assert.match(result.warnings.join(' '), /Не хватает/);
  assert.deepEqual(result.groups[0].parts[2].components.map((item) => item.quantity), ['100', '50', '20']);
  assert.deepEqual(record.groups[0].components.map((item) => item.dosePerDa), ['10', '5', '2']);
});

test('unselect all allows safe durable draft and blocks misleading empty confirmation', () => {
  const { state, record } = example(); dose(state, record, '10'); set(state, record, 'allParts', false, { groupId: 'mix-1' });
  const result = calculateFertilizer(state, record); assert.equal(result.totalAreaDa, null); assert.deepEqual(result.needs, []);
  reviewed(state, record); assert.throws(() => confirmFertilizer(state, record.id, record.revision), /выберите части/);
  assert.equal(record.status, 'draft');
});

test('unknown area/grouping cannot become zero or produce an invented total', () => {
  const { state, record } = example(); dose(state, record, '10');
  set(state, record, 'areaMode', 'unknown'); set(state, record, 'allParts', true, { groupId: 'mix-1' });
  const result = calculateFertilizer(state, record); assert.equal(result.totalAreaDa, null); assert.equal(result.needs[0].quantity, null);
  assert.equal(result.groups[0].parts[0].components[0].quantity, null); assert.equal(result.needs[0].stockStatus, 'unknown');
  reviewed(state, record); assert.throws(() => confirmFertilizer(state, record.id, record.revision), /площадь неизвестна/);
  const html = renderFertilizer(state, record.id); assert.doesNotMatch(html, /null|NaN|undefined/);
});

test('unknown stock remains unknown; no unit conversion is invented', () => {
  const { state, record } = example(); dose(state, record, '1'); state.daily.inventory.find((row) => row.id === 'kno3').quantity = null;
  let result = calculateFertilizer(state, record); assert.equal(result.needs[0].stock, null); assert.equal(result.needs[0].stockStatus, 'unknown');
  assert.equal(result.needs[0].quantity, '5'); assert.match(result.warnings.join(' '), /запас неизвестен/);
  state.daily.inventory.find((row) => row.id === 'kno3').unit = 'мешок'; result = calculateFertilizer(state, record);
  assert.equal(result.needs[0].quantity, null); assert.match(result.issues.join(' '), /Конверсия не выполнялась/);
});

test('different preparations and one part dose override remain independent', () => {
  const { state, record } = example(); dose(state, record, '10');
  set(state, record, 'allParts', true, { groupId: 'mix-1' });
  set(state, record, 'override', '2.25', { groupId: 'mix-1', componentId: 'component-1', partId: 'p2' });
  addFertilizerGroup(state, record.id); addFertilizerComponent(state, record.id, 'mix-2', 'demo-multilex');
  const second = record.groups[1]; dose(state, record, '5', second.components[0], second);
  set(state, record, 'part', true, { groupId: 'mix-2', partId: 'p2' });
  const result = calculateFertilizer(state, record);
  assert.deepEqual(result.groups[0].parts.map((part) => part.components[0].quantity), ['50', '11.25', '100']);
  assert.equal(result.groups[1].parts[0].components[0].quantity, '25'); assert.equal(result.totalAreaDa, '20');
  assert.equal(result.groups[0].parts[1].components[0].provenance, 'explicit-owner-override');
  assert.deepEqual(result.needs.map((need) => need.quantity), ['161.25', '25']);
});

test('duplicate physical part/product across mixtures warns, blocks confirmation and suppresses misleading total', () => {
  const { state, record } = example(); dose(state, record, '1'); addFertilizerGroup(state, record.id);
  addFertilizerComponent(state, record.id, 'mix-2', 'kno3'); dose(state, record, '2', record.groups[1].components[0], record.groups[1]);
  set(state, record, 'part', true, { groupId: 'mix-2', partId: 'p1' });
  const result = calculateFertilizer(state, record); assert.equal(result.overlapping, true); assert.equal(result.needs[0].quantity, null);
  assert.match(result.issues.join(' '), /повтор/); reviewed(state, record);
  assert.throws(() => confirmFertilizer(state, record.id, record.revision), /повтор/);
});

test('worker mapping uses stable R3 IDs, with no routine picker and no fuzzy fallback', () => {
  const state = fresh(); assert.equal(responsibleWorker(state, 'imamli').id, 'muhammed');
  assert.equal(responsibleWorker(state, 'yesiloz').id, 'umar'); assert.equal(responsibleWorker(state, 'ozvadi').id, 'inayat');
  assert.equal(responsibleWorker(state, 'İmamlı'), null);
  const record = startFertilizerDraft(state, 'kno3');
  const html = renderFertilizer(state, record.id); assert.doesNotMatch(html, /Выбрать работника|fertilizer-worker-override/); assert.match(html, /TEST Мухаммед/);
  set(state, record, 'greenhouseId', 'yesiloz'); assert.match(renderFertilizer(state, record.id), /TEST Умар/);
  assert.equal(record.groups[0].partIds.length, 0, 'Changing greenhouse clears incompatible target parts');
});

test('exception recipient requires a reason and never modifies global responsibility mapping', () => {
  const { state, record } = example(); dose(state, record, '1'); set(state, record, 'workerOverrideId', 'umar');
  assert.match(calculateFertilizer(state, record).issues.join(' '), /причину исключения/);
  set(state, record, 'workerOverrideReason', 'Учебный пример отсутствия основного работника'); reviewed(state, record);
  confirmFertilizer(state, record.id, record.revision); assert.equal(record.confirmedPacket.recipientId, 'umar');
  assert.equal(responsibleWorker(state, 'imamli').id, 'muhammed');
});

test('worker packet always contains each selected part, formula quantities and independent preparations', () => {
  const { state, record } = example(); addThree(state, record); set(state, record, 'allParts', true, { groupId: 'mix-1' });
  const packet = fertilizerWorkerPacket(state, record); assert.equal(packet.groups[0].parts.length, 3);
  for (const part of packet.groups[0].parts) assert.equal(part.components.length, 3);
  assert.equal(packet.dispatched, false); assert.equal(packet.stockEffect, false); assert.equal(packet.financeEffect, false);
  const html = renderFertilizer(state, record.id, 'review');
  for (const part of ['p1', 'p2', 'p3']) assert.match(html, new RegExp(`worker-part-mix-1-${part}`));
  assert.match(html, /Автоперевода нет/);
});

test('manual Dari review is explicit, cannot be inferred from text, and structural correction invalidates reviews', () => {
  const { state, record } = example(); dose(state, record, '1');
  set(state, record, 'dari', 'متن آزمایشی'); set(state, record, 'ownerReviewed', true);
  assert.throws(() => confirmFertilizer(state, record.id, record.revision), /ручную проверку/);
  reviewed(state, record); dose(state, record, '2');
  assert.equal(record.dariReviewed, false); assert.equal(record.ownerReviewed, false); assert.equal(record.warningsReviewed, false);
});

test('stale review cannot confirm; double-confirm, reload and DONE leave ledgers untouched', () => {
  const { state, record } = example(); const before = ledger(state); addThree(state, record);
  set(state, record, 'allParts', true, { groupId: 'mix-1' }); reviewed(state, record);
  assert.throws(() => confirmFertilizer(state, record.id, record.revision - 1), /Карточка изменилась/);
  const revision = record.revision; confirmFertilizer(state, record.id, revision); confirmFertilizer(state, record.id, revision);
  assert.deepEqual(ledger(state), before); assert.equal(state.fertilizer.records.length, 1);
  const storage = { value: null, getItem() { return this.value; }, setItem(key, value) { this.value = value; } };
  assert.equal(persist(storage, state), true); const loaded = loadState(storage); ensureFertilizer(loaded);
  confirmFertilizer(loaded, record.id, revision); markFertilizerDone(loaded, record.id); markFertilizerDone(loaded, record.id);
  assert.deepEqual(ledger(loaded), before); assert.equal(loaded.fertilizer.records.length, 1);
  assert.equal(fertilizerRecord(loaded, record.id).workerReport.ownerAcceptedActual, false);
  assert.throws(() => dose(loaded, fertilizerRecord(loaded, record.id), '9'), /Подтверждённое/);
});

test('safe draft reopening is idempotent; new task after confirmation gets a new explicit identity', () => {
  const { state, record } = example(); assert.equal(startFertilizerDraft(state, 'kno3').id, record.id);
  dose(state, record, '1'); reviewed(state, record); confirmFertilizer(state, record.id, record.revision);
  const next = startFertilizerDraft(state, 'kno3'); assert.notEqual(next.id, record.id); assert.equal(next.status, 'draft');
});

test('calculation/render are pure; additions/removals stay in their group and values are escaped', () => {
  const { state, record } = example(); addThree(state, record); addFertilizerGroup(state, record.id);
  removeFertilizerComponent(state, record.id, 'mix-1', 'component-2'); assert.equal(record.groups[0].components.length, 2);
  removeFertilizerGroup(state, record.id, 'mix-2'); assert.equal(record.groups.length, 1);
  set(state, record, 'instruction', '<img src=x onerror=alert(1)>');
  const before = JSON.stringify(state); calculateFertilizer(state, record); fertilizerWorkerPacket(state, record);
  const html = renderFertilizer(state, record.id, 'review'); fertilizerTaskRows(state); assert.equal(JSON.stringify(state), before);
  assert.match(html, /&lt;img/); assert.doesNotMatch(html, /<img src=x/);
});

test('DOM event adapter uses the same model, preserves text focus on input, handles unrelated events and reports errors', () => {
  const state = fresh();
  const control = { dataset: { action: 'fertilizer:start:kno3' } };
  const click = { type: 'click', target: { closest: (selector) => selector === '[data-action]' ? control : null }, preventDefault() {} };
  const started = handleFertilizerEvent(state, click); assert.equal(started.route, 'fertilizer/fertilizer-1');
  const input = { type: 'text', value: '1.2', dataset: { fertilizerId: 'fertilizer-1', fertilizerField: 'dosePerDa', groupId: 'mix-1', componentId: 'component-1' } };
  const result = handleFertilizerEvent(state, { type: 'input', target: { closest: () => input } });
  assert.deepEqual(result, { handled: true, render: false, refresh: true }); assert.equal(state.fertilizer.records[0].groups[0].components[0].dosePerDa, '1.2');
  assert.deepEqual(handleFertilizerEvent(state, { type: 'click', target: { closest: () => null } }), { handled: false });
});

test('DOM test IDs are unique and compact builder puts the selected product before parameters', () => {
  const { state, record } = example(); addThree(state, record); set(state, record, 'allParts', true, { groupId: 'mix-1' });
  for (const stage of ['edit', 'review']) {
    const html = renderFertilizer(state, record.id, stage);
    const ids = [...html.matchAll(/data-testid="([^"]+)"/g)].map((match) => match[1]);
    assert.equal(new Set(ids).size, ids.length, `Unique actionable test IDs in ${stage}`);
  }
  const html = renderFertilizer(state, record.id);
  assert.ok(html.indexOf('fertilizer-dose-component-1') < html.indexOf('fertilizer-greenhouse'));
  assert.throws(() => addFertilizerComponent(state, record.id, 'mix-1', 'missing-stock-id'), /не найдено/);
});

test('known CURRENT area supports local per-part confirmation after UNKNOWN physical mapping acknowledgement', () => {
  const state = fresh(), before = ledger(state), record = startFertilizerDraft(state, 'kno3');
  dose(state, record, '1'); set(state, record, 'allParts', true, { groupId: 'mix-1' });
  const result = calculateFertilizer(state, record);
  assert.equal(result.calculable, true); assert.equal(result.totalAreaDa, '20');
  assert.equal(result.needs[0].quantity, '20'); assert.equal(result.groups[0].parts.length, 4);
  assert.match(result.warnings.join(' '), /объединение в бочку и способ выполнения не определены/);
  set(state, record, 'dari', 'چهار بخش؛ هر بخش 5 kg. بشکه و آبیاری نامعلوم است.');
  set(state, record, 'dariReviewed', true); set(state, record, 'ownerReviewed', true);
  assert.throws(() => confirmFertilizer(state, record.id, record.revision), /неизвестной схеме полива/);
  set(state, record, 'warningsReviewed', true); confirmFertilizer(state, record.id, record.revision);
  assert.equal(record.confirmedPacket.areaMode, 'reference'); assert.equal(record.status, 'confirmed');
  assert.ok(record.confirmedPacket.groups[0].parts.every((part) => part.barrelGroupId == null && part.irrigationGroupId == null));
  assert.deepEqual(ledger(state), before);
  assert.doesNotMatch(renderFertilizer(state, record.id, 'task'), /Учебный пример площадей/);
});
