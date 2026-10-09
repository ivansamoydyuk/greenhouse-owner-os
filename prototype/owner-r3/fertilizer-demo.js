// Isolated owner-selected B storyboard. Synthetic fixtures only; no ledger or network writes.
import { DEMO_TODAY } from './daily-demo.js';

export const FERTILIZER_VERSION = 1;
export const RESPONSIBLE_WORKERS = Object.freeze({ imamli: 'muhammed', yesiloz: 'umar', ozvadi: 'inayat' });
const SOURCE_GREENHOUSES = Object.freeze({ kno3: 'imamli', 'kno3-sun': 'ozvadi', calcium: 'yesiloz' });
const clone = (value) => structuredClone(value);
const clean = (value) => String(value ?? '').trim();
const error = (message) => { throw new Error(message); };

// Decimal strings are persisted, never binary floating-point products or rounded totals.
export function decimal(raw, { optional = false, positive = false } = {}) {
  const value = clean(raw).replace(',', '.');
  if (!value && optional) return null;
  if (!/^\d{1,9}(?:\.\d{1,6})?$/.test(value)) error('Введите число: до 6 знаков после запятой, без отрицательных значений.');
  const [whole, fraction = ''] = value.split('.');
  const result = `${BigInt(whole)}${fraction.replace(/0+$/, '') ? '.' + fraction.replace(/0+$/, '') : ''}`;
  if (positive && result === '0') error('Дозировка и площадь должны быть больше нуля.');
  return result;
}
function decimalParts(raw) {
  const [whole, fraction = ''] = String(raw).split('.');
  return [BigInt(whole + fraction), fraction.length];
}
function fromParts(value, scale) {
  const digits = value.toString().padStart(scale + 1, '0');
  if (!scale) return digits;
  return `${digits.slice(0, -scale)}.${digits.slice(-scale)}`.replace(/\.?0+$/, '');
}
export function multiplyDecimals(left, right) {
  const [a, as] = decimalParts(left), [b, bs] = decimalParts(right);
  return fromParts(a * b, as + bs);
}
export function sumDecimals(values) {
  if (!values.length) return '0';
  const parts = values.map(decimalParts), scale = Math.max(...parts.map(([, s]) => s));
  return fromParts(parts.reduce((sum, [value, s]) => sum + value * 10n ** BigInt(scale - s), 0n), scale);
}
function compareDecimals(left, right) {
  const [a, as] = decimalParts(left), [b, bs] = decimalParts(right), scale = Math.max(as, bs);
  const x = a * 10n ** BigInt(scale - as), y = b * 10n ** BigInt(scale - bs);
  return x === y ? 0 : x < y ? -1 : 1;
}
export const displayDecimal = (value) => value == null ? 'Неизвестно' : String(value).replace('.', ',');
export const unitLabel = (unit) => ({ kg: 'кг', L: 'L' })[unit] || 'Единица неизвестна';
function canonicalUnit(unit) { return ({ 'кг': 'kg', kg: 'kg', 'л': 'L', 'Л': 'L', l: 'L', L: 'L' })[unit] || null; }

export function fertilizerProducts(state) {
  const inventory = state.daily?.inventory || state.stock || [];
  return [
    ...inventory.filter((row) => row.category === 'Удобрения').map((row) => ({
      id: row.id, productId: row.id === 'kno3-sun' ? 'kno3' : row.id,
      name: row.name, unit: canonicalUnit(row.unit), stockLocationId: `stock-location-${SOURCE_GREENHOUSES[row.id] || row.id}`,
      place: row.place, greenhouseId: SOURCE_GREENHOUSES[row.id] || null,
      quantity: row.quantity == null ? null : String(row.quantity), synthetic: true, provenance: 'existing-demo-stock',
    })),
    { id: 'demo-multilex', productId: 'demo-multilex', name: 'Multilex', unit: 'L', stockLocationId: 'demo-central', place: 'DEMO центральный склад', greenhouseId: null, quantity: '125', synthetic: true, provenance: 'synthetic-fixture' },
    { id: 'demo-atocrop', productId: 'demo-atocrop', name: 'Atocrop', unit: 'kg', stockLocationId: 'demo-central', place: 'DEMO центральный склад', greenhouseId: null, quantity: '50', synthetic: true, provenance: 'synthetic-fixture' },
  ];
}
// CURRENT dictionary reference design, effective 2026-10-01. Local IDs are explicitly DEMO;
// canonical text contains no provisioned part IDs or physical irrigation/barrel mappings.
export const REFERENCE_TOPOLOGY = Object.freeze({
  imamli: [['1', 'Часть 1', '5'], ['2', 'Часть 2', '5'], ['3', 'Часть 3', '5'], ['4', 'Часть 4', '5']],
  yesiloz: [['1', 'Часть 1', '3'], ['2-1', 'Часть 2-1', '5'], ['2-2', 'Часть 2-2', '5'], ['3-1', 'Часть 3-1', '3.5'], ['3-2', 'Часть 3-2', '3.5']],
  ozvadi: [['gazipasa-1', 'Gazipaşa 1', '7'], ['gazipasa-2', 'Gazipaşa 2', '7'], ['alanya-1', 'Alanya 1 / upper', '6'], ['alanya-2', 'Alanya 2 / middle', '6'], ['alanya-3', 'Alanya 3 / lower', '4']],
});
export function fertilizerParts(greenhouseId, areaMode = 'reference') {
  if (!Object.hasOwn(RESPONSIBLE_WORKERS, greenhouseId)) return [];
  if (areaMode === 'reference') return REFERENCE_TOPOLOGY[greenhouseId].map(([localId, label, areaDa]) => ({
    id: `demo-reference-${greenhouseId}-${localId}`, label, areaDa, irrigationGroupId: null, barrelGroupId: null,
    provenance: 'current-reference-design-2026-10-01', synthetic: true, operationalIdentityVerified: false,
    source: 'https://docs.google.com/document/d/1sqHDsOCk4_HlyYMXkae_WALpW0WUOkyJRFoPnHDVeOs',
  }));
  if (areaMode !== 'synthetic') return [{ id: 'unknown-area', label: 'Части не подтверждены', areaDa: null, irrigationGroupId: null, barrelGroupId: null, provenance: 'unknown', synthetic: false }];
  return ['5', '5', '10'].map((areaDa, index) => ({
    id: `p${index + 1}`, label: `Учебная P${index + 1}`, areaDa,
    irrigationGroupId: `demo-irrigation-p${index + 1}`, barrelGroupId: `demo-barrel-p${index + 1}`,
    irrigationLabel: `Полив ${index + 1}`, barrelLabel: `Бочка ${index + 1}`,
    provenance: 'verified-synthetic-fixture-v1', synthetic: true,
  }));
}
export function responsibleWorker(state, greenhouseId, override = null) {
  const id = override || RESPONSIBLE_WORKERS[greenhouseId];
  return state.workers?.find((worker) => worker.id === id) || null;
}
export function ensureFertilizer(state) {
  // Additive namespace: never replace an old ledger or a captured source.
  state.fertilizer ??= { version: FERTILIZER_VERSION, nextId: 1, records: [] };
  state.fertilizer.records ??= [];
  state.fertilizer.nextId ??= 1;
  return state.fertilizer;
}
export function fertilizerRecord(state, id) { return state.fertilizer?.records?.find((record) => record.id === id) || null; }
function makeComponent(product, id) {
  return { id, stockId: product.id, productId: product.productId, unit: product.unit, stockLocationId: product.stockLocationId, dosePerDa: '', overrides: {}, original: clone(product) };
}
export function startFertilizerDraft(state, stockId) {
  const store = ensureFertilizer(state);
  const product = fertilizerProducts(state).find((row) => row.id === stockId);
  if (!product) error('Удобрение не найдено в DEMO библиотеке.');
  const existing = store.records.find((row) => row.status === 'draft' && row.sourceStockId === stockId);
  if (existing) return existing;
  const record = {
    id: `fertilizer-${store.nextId++}`, sourceStockId: stockId, source: clone(product), synthetic: true,
    status: 'draft', revision: 1, nextGroupId: 2, nextComponentId: 2,
    greenhouseId: product.greenhouseId || 'imamli', areaMode: 'reference', date: DEMO_TODAY,
    groups: [{ id: 'mix-1', label: 'Смесь 1', partIds: [], components: [makeComponent(product, 'component-1')] }],
    instruction: '', dari: '', dariReviewed: false, ownerReviewed: false, warningsReviewed: false,
    workerOverrideId: '', workerOverrideReason: '', history: [], confirmedPacket: null,
  };
  store.records.unshift(record);
  return record;
}
function draft(state, id) {
  const record = fertilizerRecord(state, id);
  if (!record) error('Черновик не найден.');
  if (record.status !== 'draft') error('Подтверждённое поручение сохранено. Для изменений создайте новый черновик.');
  return record;
}
function touch(record, change, invalidateDari = true) {
  record.revision++;
  record.ownerReviewed = false;
  record.warningsReviewed = false;
  if (invalidateDari) record.dariReviewed = false;
  record.history.push({ revision: record.revision, change, provenance: 'owner-correction-demo' });
}
export function updateFertilizerField(state, id, field, value, context = {}) {
  const record = draft(state, id), group = record.groups.find((row) => row.id === context.groupId);
  const component = group?.components.find((row) => row.id === context.componentId);
  const string = clean(value);
  if (['ownerReviewed', 'dariReviewed', 'warningsReviewed'].includes(field)) {
    const checked = value === true;
    if (record[field] !== checked) { record[field] = checked; record.revision++; }
    return record;
  }
  if (field === 'dosePerDa') {
    if (!component) error('Компонент не найден.');
    if (component.dosePerDa === string) return record;
    component.dosePerDa = string;
  } else if (field === 'override') {
    if (!component || !fertilizerParts(record.greenhouseId, record.areaMode).some((part) => part.id === context.partId)) error('Часть не найдена.');
    if ((component.overrides[context.partId]?.dosePerDa || '') === string) return record;
    if (string) component.overrides[context.partId] = { dosePerDa: string, provenance: 'explicit-owner-override' };
    else delete component.overrides[context.partId];
  } else if (field === 'stockId') {
    if (!component) error('Компонент не найден.');
    const product = fertilizerProducts(state).find((row) => row.id === string);
    if (!product) error('Удобрение не найдено в библиотеке.');
    if (component.stockId === string) return record;
    Object.assign(component, makeComponent(product, component.id));
  } else if (field === 'part') {
    if (!group) error('Смесь не найдена.');
    if (!fertilizerParts(record.greenhouseId, record.areaMode).some((part) => part.id === context.partId)) error('Часть не найдена.');
    const set = new Set(group.partIds); value ? set.add(context.partId) : set.delete(context.partId);
    const next = [...set]; if (JSON.stringify(next) === JSON.stringify(group.partIds)) return record;
    group.partIds = next;
  } else if (field === 'allParts') {
    if (!group) error('Смесь не найдена.');
    const next = value ? fertilizerParts(record.greenhouseId, record.areaMode).map((part) => part.id) : [];
    if (JSON.stringify(next) === JSON.stringify(group.partIds)) return record;
    group.partIds = next;
  } else if (field === 'groupLabel') {
    if (!group) error('Смесь не найдена.');
    if (group.label === string) return record;
    group.label = string;
  } else if (['greenhouseId', 'areaMode', 'date', 'instruction', 'dari', 'workerOverrideId', 'workerOverrideReason'].includes(field)) {
    if (record[field] === string) return record;
    if (field === 'areaMode' && !['reference', 'synthetic', 'unknown'].includes(string)) error('Библиотека площадей не найдена.');
    if (field === 'workerOverrideId' && string && !state.workers.some((worker) => worker.id === string)) error('Работник не найден.');
    record[field] = string;
    if (field === 'greenhouseId' || field === 'areaMode') {
      record.groups.forEach((mix) => { mix.partIds = []; mix.components.forEach((item) => { item.overrides = {}; }); });
      record.workerOverrideId = ''; record.workerOverrideReason = '';
    }
  } else error('Поле поручения не найдено.');
  touch(record, { field, ...context });
  return record;
}
export function addFertilizerComponent(state, id, groupId, stockId = null) {
  const record = draft(state, id), group = record.groups.find((row) => row.id === groupId);
  if (!group) error('Смесь не найдена.');
  const products = fertilizerProducts(state);
  if (stockId && !products.some((row) => row.id === stockId)) error('Удобрение не найдено в библиотеке.');
  const product = products.find((row) => row.id === stockId) || [...products].sort((a, b) => Number(b.provenance === 'synthetic-fixture') - Number(a.provenance === 'synthetic-fixture')).find((row) => !group.components.some((item) => item.stockId === row.id));
  if (!product) error('Все товары уже добавлены. Для отдельного приготовления добавьте другую смесь.');
  group.components.push(makeComponent(product, `component-${record.nextComponentId++}`));
  touch(record, { action: 'add-component', groupId, stockId: product.id });
  return record;
}
export function removeFertilizerComponent(state, id, groupId, componentId) {
  const record = draft(state, id), group = record.groups.find((row) => row.id === groupId);
  if (!group) error('Смесь не найдена.');
  group.components = group.components.filter((row) => row.id !== componentId);
  touch(record, { action: 'remove-component', groupId, componentId });
  return record;
}
export function addFertilizerGroup(state, id) {
  const record = draft(state, id), index = record.nextGroupId++;
  record.groups.push({ id: `mix-${index}`, label: `Отдельная смесь ${index}`, partIds: [], components: [] });
  touch(record, { action: 'add-group' });
  return record;
}
export function removeFertilizerGroup(state, id, groupId) {
  const record = draft(state, id);
  if (record.groups.length < 2) error('В поручении должна остаться хотя бы одна смесь.');
  record.groups = record.groups.filter((row) => row.id !== groupId);
  touch(record, { action: 'remove-group', groupId });
  return record;
}
function readPositive(raw, issues, label) {
  try { const result = decimal(raw, { optional: true, positive: true }); if (result == null) issues.push(`${label}: дозировка неизвестна.`); return result; }
  catch (cause) { issues.push(`${label}: ${cause.message}`); return null; }
}
export function calculateFertilizer(state, recordOrId) {
  const record = typeof recordOrId === 'string' ? fertilizerRecord(state, recordOrId) : recordOrId;
  if (!record) error('Поручение не найдено.');
  const products = fertilizerProducts(state), parts = fertilizerParts(record.greenhouseId, record.areaMode);
  const issues = [], warnings = [], totals = new Map(), productParts = new Set(), selectedParts = new Map();
  let overlapping = false;
  const groups = record.groups.map((group) => {
    if (!group.components.length) issues.push(`${group.label}: добавьте удобрение.`);
    if (!group.partIds.length) issues.push(`${group.label}: выберите части.`);
    if (new Set(group.partIds).size !== group.partIds.length) { issues.push(`${group.label}: часть выбрана дважды.`); overlapping = true; }
    const selected = [...new Set(group.partIds)].map((id) => parts.find((part) => part.id === id)).filter(Boolean);
    if (selected.length !== new Set(group.partIds).size) issues.push(`${group.label}: часть отсутствует в библиотеке.`);
    const lines = selected.map((part) => {
      selectedParts.set(part.id, part);
      let area = null;
      if (part.areaDa == null) issues.push(`${part.label}: площадь неизвестна; итог не вычислен.`);
      else { try { area = decimal(part.areaDa, { positive: true }); } catch { issues.push(`${part.label}: площадь не подтверждена.`); } }
      if (!part.irrigationGroupId || !part.barrelGroupId) warnings.push(`${part.label}: схема полива / бочка неизвестны. Расчёт только по части; объединение в бочку и способ выполнения не определены.`);
      const components = group.components.map((component) => {
        const product = products.find((row) => row.id === component.stockId);
        const name = product?.name || component.original?.name || 'Товар неизвестен';
        const key = `${component.productId}|${part.id}`;
        if (productParts.has(key)) { issues.push(`${name} · ${part.label}: повтор в нескольких строках / смесях. Уточните отдельные внесения.`); overlapping = true; }
        productParts.add(key);
        const override = component.overrides[part.id];
        const dose = readPositive(override?.dosePerDa ?? component.dosePerDa, issues, `${name} · ${part.label}`);
        const unit = product?.unit;
        const validIdentity = !!product && !!unit && unit === component.unit && product.productId === component.productId && product.stockLocationId === component.stockLocationId;
        if (!validIdentity) issues.push(`${name}: товар, единица или склад не подтверждены. Конверсия не выполнялась.`);
        const quantity = area != null && dose != null && validIdentity ? multiplyDecimals(dose, area) : null;
        const totalKey = `${component.stockId}|${component.unit}|${component.stockLocationId}`;
        const total = totals.get(totalKey) || { stockId: component.stockId, productId: component.productId, name, unit: component.unit, place: product?.place || component.original?.place, quantity: '0', stock: product?.quantity ?? null, unknown: false };
        if (quantity == null) total.unknown = true; else total.quantity = sumDecimals([total.quantity, quantity]);
        totals.set(totalKey, total);
        if (product?.greenhouseId && product.greenhouseId !== record.greenhouseId) warnings.push(`${name}: источник ${product.place}; доставка в выбранную теплицу не подтверждена.`);
        return { componentId: component.id, stockId: component.stockId, name, unit: component.unit, place: product?.place || component.original?.place, dosePerDa: dose, quantity, override: !!override, provenance: override?.provenance || 'owner-dose-per-da' };
      });
      return { ...part, areaDa: area, components };
    });
    return { id: group.id, label: group.label, parts: lines };
  });
  const needs = [...totals.values()].map((total) => {
    if (overlapping || total.unknown) total.quantity = null;
    let stockStatus = 'unknown';
    if (total.stock != null && total.quantity != null) {
      try { stockStatus = compareDecimals(decimal(total.stock), total.quantity) < 0 ? 'insufficient' : 'sufficient'; }
      catch { total.stock = null; }
    }
    if (total.stock == null) warnings.push(`${total.name}: запас неизвестен.`);
    if (stockStatus === 'insufficient') warnings.push(`${total.name}: требуется ${displayDecimal(total.quantity)} ${unitLabel(total.unit)}, в наличии ${displayDecimal(total.stock)} ${unitLabel(total.unit)}. Не хватает запаса.`);
    return { ...total, stockStatus };
  });
  const worker = responsibleWorker(state, record.greenhouseId, record.workerOverrideId);
  if (!worker) issues.push('Ответственный не установлен. Проверьте mapping теплицы.');
  if (record.workerOverrideId && !record.workerOverrideReason.trim()) issues.push('Укажите причину исключения для другого ответственного.');
  if (!/^\d{4}-\d{2}-\d{2}$/.test(record.date) || Number.isNaN(Date.parse(record.date)) || new Date(record.date).toISOString().slice(0, 10) !== record.date) issues.push('Укажите корректную дату поручения.');
  const selected = [...selectedParts.values()];
  const totalAreaDa = selected.length && selected.every((part) => part.areaDa != null) ? sumDecimals(selected.map((part) => part.areaDa)) : null;
  return { groups, needs, worker, totalAreaDa, issues: [...new Set(issues)], warnings: [...new Set(warnings)], calculable: !issues.length, overlapping };
}
export function fertilizerWorkerPacket(state, recordOrId) {
  const record = typeof recordOrId === 'string' ? fertilizerRecord(state, recordOrId) : recordOrId;
  const result = calculateFertilizer(state, record);
  return {
    synthetic: true, areaMode: record.areaMode, greenhouseId: record.greenhouseId, date: record.date, recipientId: result.worker?.id || null,
    groups: clone(result.groups), needs: clone(result.needs), warnings: clone(result.warnings), warningsReviewed: record.warningsReviewed === true, originalInstruction: record.instruction,
    preparation: record.instruction || 'Способ приготовления не задан. Совместимость препаратов не проверена.',
    dari: record.dari, dariReviewed: record.dariReviewed === true, dispatched: false,
    stockEffect: false, financeEffect: false,
  };
}
export function confirmFertilizer(state, id, expectedRevision) {
  const record = fertilizerRecord(state, id);
  if (!record) error('Поручение не найдено.');
  if (record.status === 'confirmed' || record.status === 'done') return record; // retry/reload: one durable identity
  if (record.revision !== expectedRevision) error('Карточка изменилась. Проверьте актуальные расчёты ещё раз.');
  const result = calculateFertilizer(state, record);
  if (result.issues.length) error(result.issues[0]);
  if (!record.dari.trim() || !record.dariReviewed) error('Добавьте отдельно подготовленный текст на дари и подтвердите его ручную проверку.');
  if (!record.ownerReviewed) error('Проверьте получателя, части и дозировки.');
  if (result.warnings.length && !record.warningsReviewed) error('Подтвердите, что видите предупреждения о запасе, месте хранения и неизвестной схеме полива / бочках.');
  record.confirmedPacket = fertilizerWorkerPacket(state, record);
  record.confirmedRevision = record.revision;
  record.status = 'confirmed';
  record.history.push({ revision: record.revision, change: { action: 'confirm-demo' }, provenance: 'owner-reviewed-demo' });
  return record;
}
export function markFertilizerDone(state, id) {
  const record = fertilizerRecord(state, id);
  if (!record || !['confirmed', 'done'].includes(record.status)) error('Сначала подтвердите поручение DEMO.');
  record.status = 'done';
  record.workerReport = { status: 'done', synthetic: true, ownerAcceptedActual: false, stockEffect: false };
  return record;
}
