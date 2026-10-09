import { e, Header, icon, btn, link, EmptyState } from './components.js';
import {
  fertilizerProducts, fertilizerParts, responsibleWorker, fertilizerRecord, startFertilizerDraft,
  calculateFertilizer, fertilizerWorkerPacket, displayDecimal, unitLabel, updateFertilizerField,
  addFertilizerComponent, removeFertilizerComponent, addFertilizerGroup, removeFertilizerGroup,
  confirmFertilizer, markFertilizerDone,
} from './fertilizer-demo.js';

const button = (text, action, cls = 'secondary', test = action, extra = '') => btn(text, action, cls, test, `type="button" ${extra}`);
const fact = (label, value) => `<div class="fact-row"><dt>${e(label)}</dt><dd>${e(value)}</dd></div>`;
const qty = (quantity, unit) => quantity == null ? 'Неизвестно' : `${displayDecimal(quantity)} ${unitLabel(unit)}`;
const attribute = (record, field, context = {}) => `data-fertilizer-id="${e(record.id)}" data-fertilizer-field="${e(field)}" ${Object.entries(context).map(([key, value]) => `data-${key}="${e(value)}"`).join(' ')}`;
const options = (rows, selected) => rows.map(([value, label]) => `<option value="${e(value)}" ${value === selected ? 'selected' : ''}>${e(label)}</option>`).join('');
function check(record, field, label, test, context = {}, checked = record[field]) {
  return `<label class="review-check"><input type="checkbox" ${attribute(record, field, context)} data-testid="${e(test)}" ${checked ? 'checked' : ''}><span>${e(label)}</span></label>`;
}
const greenhouseName = (state, id) => state.daily?.greenhouses?.find((greenhouse) => greenhouse.id === id)?.name || 'Теплица неизвестна';
function compactWarnings(warnings) {
  const suffix = ': схема полива / бочка неизвестны. Расчёт только по части; объединение в бочку и способ выполнения не определены.';
  const parts = [...new Set(warnings.filter(warning => warning.endsWith(suffix)).map(warning => warning.slice(0, -suffix.length)))];
  return [...warnings.filter(warning => !warning.endsWith(suffix)), ...(parts.length ? [`${parts.join(', ')}${suffix}`] : [])];
}
function alerts(result) {
  return `${result.issues.length ? `<div class="notice amber" data-testid="fertilizer-issues"><strong>Нужно уточнить</strong><ul>${result.issues.map((issue) => `<li>${e(issue)}</li>`).join('')}</ul><p>Черновик можно сохранить с неизвестными данными.</p></div>` : ''}${result.warnings.length ? `<div class="notice amber" data-testid="fertilizer-warnings"><strong>Проверьте ограничения</strong><ul>${compactWarnings(result.warnings).map((warning) => `<li>${e(warning)}</li>`).join('')}</ul><p>Дозы не изменены. Задание ничего не списывает.</p></div>` : ''}`;
}
function fullPartCards(groups, { worker = false } = {}) {
  return groups.map((group) => `<section class="calculation-group" data-testid="fertilizer-group-preview-${e(group.id)}"><h3>${e(group.label || 'Смесь без названия')} · отдельное приготовление</h3>${group.parts.length ? group.parts.map((part) => `<article class="part-calculation" data-testid="${worker ? 'worker' : 'fertilizer-calculation'}-part-${e(group.id)}-${e(part.id)}"><div class="summary-pair"><strong>${e(part.label)}</strong><span>${part.areaDa == null ? 'Площадь неизвестна' : `${displayDecimal(part.areaDa)} da · ${part.provenance === 'current-reference-design-2026-10-01' ? 'справочно' : 'DEMO'}`}</span></div><p class="note">${e(part.irrigationLabel || 'Полив неизвестен')} · ${e(part.barrelLabel || 'Бочка неизвестна')}</p>${part.components.map((item) => `<div class="calculation-line"><strong>${e(item.name)}${item.override ? ' · своя норма' : ''}</strong><span>${item.dosePerDa == null || part.areaDa == null ? 'Количество неизвестно' : `${displayDecimal(item.dosePerDa)} ${unitLabel(item.unit)}/da × ${displayDecimal(part.areaDa)} da =`} <b>${qty(item.quantity, item.unit)}</b></span><small>Источник: ${e(item.place || 'место неизвестно')}</small></div>`).join('')}</article>`).join('') : '<p class="note">Части пока не выбраны.</p>'}</section>`).join('');
}
function partCards(groups, { worker = false, compact = false } = {}) {
  if (!compact) return fullPartCards(groups, { worker });
  const sourceVariants = new Map();
  for (const group of groups) for (const part of group.parts) for (const item of part.components) {
    if (!sourceVariants.has(item.name)) sourceVariants.set(item.name, new Set());
    sourceVariants.get(item.name).add(item.stockId);
  }
  return groups.map((group) => `<section class="calculation-group" data-testid="fertilizer-group-preview-${e(group.id)}"><h3>${e(group.label || 'Смесь без названия')} · отдельное приготовление</h3>${group.parts.length ? group.parts.map((part) => `<article class="part-calculation" data-testid="${worker ? 'worker' : 'fertilizer-calculation'}-part-${e(group.id)}-${e(part.id)}"><div class="summary-pair"><strong>${e(part.label)}</strong><span>${part.areaDa == null ? 'Площадь неизвестна' : `${displayDecimal(part.areaDa)} da`}</span></div>${part.irrigationLabel || part.barrelLabel ? `<p class="note">${e(part.irrigationLabel || 'Полив неизвестен')} · ${e(part.barrelLabel || 'Бочка неизвестна')}</p>` : ''}${part.components.map((item) => `<div class="calculation-line"><strong>${e(item.name)}${sourceVariants.get(item.name).size > 1 ? `<small>Источник: ${e(item.place || 'место неизвестно')}</small>` : ''}${item.override ? '<small class="override-mark">Своя норма</small>' : ''}</strong><span><small>${item.dosePerDa == null || part.areaDa == null ? 'Доза / площадь неизвестна' : `${displayDecimal(item.dosePerDa)} ${unitLabel(item.unit)}/da × ${displayDecimal(part.areaDa)} da =`}</small><b>${qty(item.quantity, item.unit)}</b></span></div>`).join('')}</article>`).join('') : '<p class="note">Части пока не выбраны.</p>'}</section>`).join('');
}
function totals(result, { editor = false } = {}) {
  return `<section class="card fertilizer-totals" data-testid="fertilizer-totals"><h2>Всего по продуктам</h2>${result.needs.length ? result.needs.map((need) => `<div class="fertilizer-total-line" data-testid="fertilizer-total-${e(need.stockId)}"><div class="summary-pair"><strong>${e(need.name)}</strong><strong>${qty(need.quantity, need.unit)}</strong></div>${editor ? '' : `<p class="note">Источник: ${e(need.place || 'место неизвестно')} · запас ${qty(need.stock, need.unit)}</p>`}<span class="stock-status ${need.stockStatus === 'sufficient' ? '' : 'unknown-status'}">${({ sufficient: 'Хватает в DEMO', insufficient: `Не хватает · запас ${qty(need.stock, need.unit)}`, unknown: 'Достаточность неизвестна' })[need.stockStatus]}</span></div>`).join('') : '<p class="note">Выберите части для расчёта. Запас не заменяет дозу.</p>'}<p class="note totals-footnote">Площадь без повторов · кг и L отдельно</p></section>`;
}
function selectedScope(result) {
  const parts = [...new Map(result.groups.flatMap((group) => group.parts).map((part) => [part.id, part])).values()];
  return `<div class="selected-scope" data-testid="fertilizer-selected-scope"><strong>${parts.length ? `Выбрано частей: ${parts.length} · ${result.totalAreaDa == null ? 'площадь неизвестна' : `${displayDecimal(result.totalAreaDa)} da`}` : 'Выберите части'}</strong>${parts.length ? `<span>${e(parts.map((part) => part.label).join(', '))}</span>` : '<span>Склад задаёт только теплицу-кандидата.</span>'}</div>`;
}
function partSelector(record, group) {
  const parts = fertilizerParts(record.greenhouseId, record.areaMode);
  const all = parts.length > 0 && parts.every((part) => group.partIds.includes(part.id));
  return `<fieldset class="part-selector"><legend>${record.groups.length > 1 ? e(group.label) : 'Куда внести'}</legend><label class="part-chip all-parts"><input type="checkbox" ${attribute(record, 'allParts', { 'group-id': group.id })} ${all ? 'checked' : ''} data-testid="fertilizer-all-${group.id}"><span>Все части</span></label>${parts.map((part) => `<label class="part-chip ${group.partIds.includes(part.id) ? 'selected' : ''}"><input type="checkbox" ${attribute(record, 'part', { 'group-id': group.id, 'part-id': part.id })} ${group.partIds.includes(part.id) ? 'checked' : ''} data-testid="fertilizer-part-${group.id}-${part.id}"><span>${e(part.label)}<small>${part.areaDa == null ? 'Площадь неизвестна' : `${displayDecimal(part.areaDa)} da`}</small></span></label>`).join('')}</fieldset>`;
}
const overrideLabel = (component) => `Своя доза для части${Object.keys(component.overrides).length ? ` · изменено: ${Object.keys(component.overrides).length}` : ''}`;
function componentFields(state, record, group, component, index) {
  const products = fertilizerProducts(state), product = products.find((row) => row.id === component.stockId);
  const context = { 'group-id': group.id, 'component-id': component.id };
  const selected = fertilizerParts(record.greenhouseId, record.areaMode).filter((part) => group.partIds.includes(part.id));
  return `<div class="mix-component" data-testid="fertilizer-component-${e(component.id)}"><div class="component-main"><label class="field component-product"><span class="sr-only">Удобрение ${index + 1}</span><select class="input" ${attribute(record, 'stockId', context)} data-testid="fertilizer-product-${e(component.id)}">${options(products.map((row) => [row.id, products.some((other) => other.id !== row.id && other.name === row.name) ? `${row.name} · ${row.place}` : row.name]), component.stockId)}</select></label><p class="note component-stock">${e(product?.place || 'Место неизвестно')} · запас ${qty(product?.quantity, component.unit)}</p><label class="field dose-control"><span>На 1 da</span><div class="quantity-inline"><input class="input" type="text" inputmode="decimal" autocomplete="off" placeholder="Доза" value="${e(component.dosePerDa)}" ${attribute(record, 'dosePerDa', context)} data-testid="fertilizer-dose-${e(component.id)}"><span>${unitLabel(component.unit)}/da</span></div></label>${selected.length ? `<details class="source-details component-overrides" ${disclosure(record, 'edit', `override-${component.id}`)}><summary data-testid="fertilizer-override-state-${e(component.id)}">${e(overrideLabel(component))}</summary><p class="note">Пусто = общая доза. Своя норма меняет только эту часть.</p>${selected.map((part) => `<label class="field"><span>${e(part.label)} · ${unitLabel(component.unit)}/da</span><input class="input" type="text" inputmode="decimal" placeholder="Общая дозировка" value="${e(component.overrides[part.id]?.dosePerDa || '')}" ${attribute(record, 'override', { ...context, 'part-id': part.id })} data-testid="fertilizer-override-${e(component.id)}-${e(part.id)}"></label>`).join('')}</details>` : ''}</div>${button(icon('close'), `fertilizer:remove-component:${record.id}:${group.id}:${component.id}`, 'icon-button', `fertilizer-remove-${component.id}`, `aria-label="Удалить ${e(product?.name || 'компонент')}"`)}</div>`;
}
function mixEditor(state, record, group, index) {
  return `<section class="card mixture-card" data-testid="fertilizer-mix-${e(group.id)}"><div class="card-header"><h2>${e(group.label || `Смесь ${index + 1}`)}</h2>${record.groups.length > 1 ? button('Убрать', `fertilizer:remove-group:${record.id}:${group.id}`, 'text-button', `fertilizer-remove-group-${group.id}`) : ''}</div>${index > 0 ? `<label class="field"><span>Отдельное приготовление</span><input class="input" value="${e(group.label)}" ${attribute(record, 'groupLabel', { 'group-id': group.id })} data-testid="fertilizer-group-label-${e(group.id)}"></label>` : ''}<div class="fertilizer-components">${group.components.map((component, i) => componentFields(state, record, group, component, i)).join('')}</div>${button(`${icon('plus')} Добавить ещё удобрение`, `fertilizer:add-component:${record.id}:${group.id}`, 'secondary full', `fertilizer-add-component-${group.id}`)}</section>`;
}
function editor(state, record) {
  const result = calculateFertilizer(state, record), worker = result.worker;
  const greenhouses = state.daily?.greenhouses || [];
  const reset = record.history.some((entry) => ['greenhouseId', 'areaMode'].includes(entry.change.field)) && !record.groups.some((group) => group.partIds.length);
  const mode = record.areaMode === 'reference' ? 'CURRENT · справочно' : record.areaMode === 'synthetic' ? 'Учебные 5/5/10 · не теплица' : 'Площадь UNKNOWN';
  return `<div class="detail-layout fertilizer-builder" data-r3-form="fertilizer" data-fertilizer-view="${e(record.id)}:edit">${Header('Внесение удобрений', 'Состав и части → проверка поручения', true)}<div class="notice" data-testid="fertilizer-demo-label">DEMO · без списания и отправки</div><section class="card fertilizer-meta" data-testid="fertilizer-context"><div class="form-grid"><label class="field"><span>Теплица-кандидат</span><select class="input" ${attribute(record, 'greenhouseId')} data-testid="fertilizer-greenhouse">${options(greenhouses.map((greenhouse) => [greenhouse.id, greenhouse.name]), record.greenhouseId)}</select></label><label class="field"><span>Дата поручения</span><input class="input" type="date" value="${e(record.date)}" ${attribute(record, 'date')} data-testid="fertilizer-date"></label></div><p class="fertilizer-recipient">${record.workerOverrideId ? 'Исключение для поручения' : 'Ответственный по теплице'}: <strong data-testid="fertilizer-auto-worker">${e(worker?.name || 'Не установлен')}</strong><small>Кандидат · проверка перед подтверждением</small></p>${selectedScope(result)}${reset ? '<div class="notice amber" data-testid="fertilizer-context-reset">Теплица / библиотека изменена. Части, свои нормы и исключение получателя сброшены. Выберите части заново; общие дозы и склады сохранены.</div>' : ''}${record.groups.map((group) => partSelector(record, group)).join('')}<details class="source-details fertilizer-library" ${disclosure(record, 'edit', 'library')} data-testid="fertilizer-library-details"><summary data-testid="fertilizer-library-details-toggle">Библиотека: ${e(mode)}</summary><label class="field"><span>Библиотека частей и площадей</span><select class="input" ${attribute(record, 'areaMode')} data-testid="fertilizer-area-mode">${options([['reference', 'CURRENT · справочная топология частей'], ['synthetic', 'Отдельный учебный пример P1/P2/P3 · 5/5/10'], ['unknown', 'Непроверенная площадь · UNKNOWN']], record.areaMode)}</select></label><p class="note">CURRENT: справочные площади; ID только DEMO. Физические полив и бочки не подтверждены.</p></details>${record.areaMode === 'synthetic' ? '<div class="notice amber" data-testid="fertilizer-teaching-mode">P1/P2/P3 и бочки — отдельный синтетический пример, не топология выбранной теплицы.</div>' : ''}</section>${record.groups.map((group, index) => mixEditor(state, record, group, index)).join('')}<details class="source-details fertilizer-secondary" ${disclosure(record, 'edit', 'separate')} data-testid="fertilizer-separate-preparations"><summary data-testid="fertilizer-separate-preparations-toggle">Отдельная смесь / приготовление${record.groups.length > 1 ? ` · ${record.groups.length} групп` : ''}</summary><p class="note">Каждая группа сохраняет свои части и компоненты. Это не подтверждение физических бочек.</p>${button(`${icon('plus')} Отдельная смесь / бочка`, `fertilizer:add-group:${record.id}`, 'secondary full', 'fertilizer-add-group')}</details><p class="note fertilizer-compatibility">Совместимость препаратов и способ приготовления не определяются автоматически.</p><details class="source-details fertilizer-secondary" ${disclosure(record, 'edit', 'source')} data-testid="fertilizer-source-details"><summary data-testid="fertilizer-source-details-toggle">Исходная инструкция · <span data-fertilizer-instruction-state>${record.instruction ? 'добавлена' : 'способ не задан'}</span></summary><label class="field"><span>Исходная инструкция владельца</span><textarea class="input fertilizer-autogrow" rows="3" placeholder="Способ приготовления пока не задан" ${attribute(record, 'instruction')} data-testid="fertilizer-instruction">${e(record.instruction)}</textarea></label><p class="note">Источник входа: ${e(record.source.name)} · ${e(record.source.place)}. Его запас не задаёт дозу или конечные части.</p></details><div data-fertilizer-live="totals">${totals(result, { editor: true })}</div><div data-fertilizer-live="alerts">${alerts(result)}</div><details class="source-details fertilizer-secondary" ${disclosure(record, 'edit', 'parts')} data-testid="fertilizer-part-details"><summary data-testid="fertilizer-part-details-toggle">Расчёт по каждой части · формулы</summary><div data-fertilizer-live="parts">${partCards(result.groups)}</div></details><div class="fertilizer-actions">${button('Сохранить черновик', `fertilizer:save:${record.id}`, 'secondary full', 'fertilizer-save')}${button('Проверить поручение', `fertilizer:review:${record.id}`, 'primary full', 'fertilizer-review')}</div><p class="note">Черновик на этом устройстве. Склад, долг и деньги не меняются.</p></div>`;
}
function comparison(result, record) {
  // A nearby reference may group identical rows; the primary packet above still lists every part.
  const groups = result.groups.map((group) => {
    const equalRows = new Map();
    for (const part of group.parts) {
      const key = JSON.stringify([part.areaDa, part.components.map(({ stockId, dosePerDa, quantity, unit, override }) => [stockId, dosePerDa, quantity, unit, override])]);
      if (!equalRows.has(key)) equalRows.set(key, { part, labels: [] });
      equalRows.get(key).labels.push(part.label);
    }
    return `<div class="comparison-group"><strong>${e(group.label)} · отдельное приготовление</strong>${[...equalRows.values()].map(({ part, labels }) => `<div class="comparison-part"><span>${e(labels.join(', '))}</span><p>${labels.length > 1 ? 'На каждую указанную часть' : 'На эту часть'} · ${part.areaDa == null ? 'площадь неизвестна' : `${displayDecimal(part.areaDa)} da`}</p><p>${part.components.map((item) => `${e(item.name)} ${qty(item.quantity, item.unit)}${item.override ? ' (своя норма)' : ''}`).join(' · ')}</p></div>`).join('')}</div>`;
  }).join('');
  return `<details class="fertilizer-comparison" ${disclosure(record, 'review', 'comparison', true)} data-testid="fertilizer-comparison"><summary data-testid="fertilizer-comparison-toggle">Числа рядом для сверки дари</summary>${groups}</details>`;
}
function review(state, record) {
  const result = calculateFertilizer(state, record), packet = fertilizerWorkerPacket(state, record);
  return `<div class="detail-layout fertilizer-review" data-r3-form="fertilizer-review" data-fertilizer-view="${e(record.id)}:review">${Header('Проверка поручения', `${greenhouseName(state, record.greenhouseId)} · ${record.date}`, true)}<section class="card fertilizer-review-context"><span class="eyebrow">${record.workerOverrideId ? 'Исключение получателя' : 'Кандидат по теплице'}</span><h2 data-testid="fertilizer-review-worker">${e(result.worker?.name || 'Ответственный не установлен')}</h2>${selectedScope(result)}<p class="note">${record.areaMode === 'synthetic' ? 'Учебный пример, не топология теплицы.' : record.areaMode === 'reference' ? 'CURRENT справочно · ID только DEMO.' : 'Площади не подтверждены.'}</p><details class="source-details" ${disclosure(record, 'review', 'worker')} data-testid="fertilizer-worker-exception"><summary data-testid="fertilizer-worker-exception-toggle">Исключение: другой ответственный${record.workerOverrideId ? ' · изменён' : ''}</summary><p class="note">Только это поручение. Правило теплицы не меняется.</p><label class="field"><span>Получатель для исключения</span><select class="input" ${attribute(record, 'workerOverrideId')} data-testid="fertilizer-worker-override">${options([['', 'По правилу теплицы'], ...state.workers.map((worker) => [worker.id, worker.name])], record.workerOverrideId)}</select></label><label class="field"><span>Причина исключения</span><input class="input" value="${e(record.workerOverrideReason)}" ${attribute(record, 'workerOverrideReason')} data-testid="fertilizer-worker-reason"></label></details></section><section class="card fertilizer-packet"><h2>Каждая часть отдельно</h2><p class="note">Доза × площадь. Совместимость не проверена.</p>${partCards(packet.groups, { worker: true, compact: true })}</section><div data-fertilizer-live="totals">${totals(result)}</div><div data-fertilizer-live="alerts">${alerts(result)}</div><section class="card fertilizer-language"><h2>Исходная инструкция</h2><p class="original-text" data-testid="fertilizer-original-instruction">${e(packet.preparation)}</p><h2>Текст на дари</h2><p class="note">Автоперевода нет. Введите подготовленный текст и сверьте получателя, дату, части, числа и единицы.</p>${comparison(result, record)}<label class="field"><span>Проверяемая версия для работника</span><textarea class="input translation fertilizer-autogrow" rows="3" lang="fa" dir="rtl" ${attribute(record, 'dari')} data-testid="fertilizer-dari">${e(record.dari)}</textarea></label><p class="note review-invalidated" data-testid="fertilizer-review-invalidated" ${record.dariReviewed && record.ownerReviewed ? 'hidden' : ''}>После правок нужна новая ручная проверка.</p>${check(record, 'dariReviewed', 'Я вручную проверил текст на дари, части, числа и единицы', 'fertilizer-dari-reviewed')}${check(record, 'ownerReviewed', 'Проверил получателя, дату, части и дозы на 1 da', 'fertilizer-owner-reviewed')}${result.warnings.length ? check(record, 'warningsReviewed', 'Вижу ограничения запаса, источников и полива / бочек; сохраняю только DEMO', 'fertilizer-warnings-reviewed') : ''}</section><div class="notice">Подтверждение создаёт только поручение DEMO. Без Telegram, списания и финансовых изменений.</div><div class="fertilizer-actions">${link('Изменить состав и части', `fertilizer/${record.id}`, 'btn secondary full', 'fertilizer-edit')}${button('Подтвердить поручение DEMO', `fertilizer:confirm:${record.id}`, 'primary full', 'fertilizer-confirm', `data-revision="${record.revision}"`)}</div></div>`;
}
function confirmed(state, record) {
  const packet = record.confirmedPacket;
  return `<div class="detail-layout fertilizer-review">${Header('Поручение на внесение', `${greenhouseName(state, record.greenhouseId)} · ${record.date}`, true)}<div class="notice" data-testid="fertilizer-confirmed">${record.status === 'done' ? 'DEMO: работник отметил DONE. Факт внесения владельцем не подтверждён.' : 'Поручение создано в DEMO. Работнику не отправлено.'}</div>${alerts({ issues: [], warnings: packet.warnings || [] })}<section class="card"><h2>${e(state.workers.find((worker) => worker.id === packet.recipientId)?.name || 'Получатель неизвестен')}</h2><p class="note">${record.areaMode === 'synthetic' ? 'Учебный пример площадей, не топология теплицы.' : 'Части CURRENT справочно; их ID только DEMO. Физические бочки и полив не подтверждены.'} Запас и финансы не изменены.</p>${partCards(packet.groups, { worker: true })}<h3>Исходная инструкция</h3><p class="original-text">${e(packet.preparation)}</p><h3>Вручную проверенный текст на дари</h3><div class="translation" lang="fa" dir="rtl">${e(packet.dari)}</div></section>${record.status !== 'done' ? button('DEMO: работник отметил DONE', `fertilizer:done:${record.id}`, 'secondary full', 'fertilizer-done') : '<p class="note" data-testid="fertilizer-done-status">DONE ≠ подтверждённое фактическое внесение. Движений склада не создано.</p>'}${link('Вернуться к товару', `stock/${record.sourceStockId}`, 'btn primary full', 'fertilizer-stock-return')}</div>`;
}
// Presentation state is separate from task data and never creates a draft or a revision.
// The host replaces its page on select/checkbox changes; retain the local working position.
const viewStorageKey = 'r3-fertilizer-view-v1';
let viewStates = {}, restoringView = false, viewFrame = 0, viewInteraction = 0;
if (typeof window !== 'undefined') {
  try {
    const saved = JSON.parse(window.sessionStorage.getItem(viewStorageKey) || '{}');
    if (saved && typeof saved === 'object' && !Array.isArray(saved)) viewStates = saved;
  } catch { /* UI persistence is optional. */ }
}
// Restore disclosure state in the HTML itself. A replacement native select must
// never spend a frame inside a temporarily closed details element.
function disclosure(record, stage, name, defaultOpen = false) {
  const saved = viewStates[`${record.id}:${stage}`]?.open;
  const open = saved && Object.hasOwn(saved, name) ? saved[name] : defaultOpen;
  return `data-fertilizer-disclosure="${e(name)}"${open ? ' open' : ''}`;
}
const textMeasurements = new WeakMap();
let observedTextRoot = null, textResizeObserver = null, textStyleObserver = null, textGrowthFrame = 0;
function growFertilizerText(root = document, force = false) {
  const scroll = window.scrollY;
  for (const textarea of root.querySelectorAll('.fertilizer-autogrow')) {
    if (!textarea.getClientRects().length) continue;
    const style = getComputedStyle(textarea);
    // Height is deliberately excluded: our own height writes must not create an observer loop.
    const metrics = [textarea.value, textarea.clientWidth, style.fontFamily, style.fontSize,
      style.fontWeight, style.fontStyle, style.lineHeight, style.letterSpacing, style.wordSpacing,
      style.paddingTop, style.paddingBottom, style.paddingLeft, style.paddingRight,
      style.borderTopWidth, style.borderBottomWidth, style.whiteSpace, style.wordBreak].join('|');
    if (!force && textMeasurements.get(textarea) === metrics && textarea.scrollHeight <= textarea.clientHeight) continue;
    textarea.style.height = 'auto';
    const border = parseFloat(style.borderTopWidth) + parseFloat(style.borderBottomWidth);
    textarea.style.height = `${Math.ceil(textarea.scrollHeight + border)}px`;
    // Guard fractional layout rounding without masking a hidden textarea tail.
    if (textarea.scrollHeight > textarea.clientHeight) {
      textarea.style.height = `${Math.ceil(textarea.getBoundingClientRect().height + textarea.scrollHeight - textarea.clientHeight)}px`;
    }
    textMeasurements.set(textarea, metrics);
  }
  if (window.scrollY !== scroll) window.scrollTo(0, scroll);
}
function stopFertilizerTextObservation() {
  textResizeObserver?.disconnect(); textStyleObserver?.disconnect();
  if (textGrowthFrame) cancelAnimationFrame(textGrowthFrame);
  observedTextRoot = null; textGrowthFrame = 0;
}
function scheduleFertilizerTextGrowth() {
  if (!observedTextRoot?.isConnected) { stopFertilizerTextObservation(); return; }
  if (textGrowthFrame) return;
  textGrowthFrame = requestAnimationFrame(() => {
    textGrowthFrame = 0;
    if (observedTextRoot?.isConnected) growFertilizerText(observedTextRoot);
    else stopFertilizerTextObservation();
  });
}
function observeFertilizerText(root) {
  stopFertilizerTextObservation();
  observedTextRoot = root;
  if (typeof ResizeObserver !== 'undefined') {
    textResizeObserver = new ResizeObserver(scheduleFertilizerTextGrowth);
    textResizeObserver.observe(root);
    root.querySelectorAll('.fertilizer-autogrow').forEach((textarea) => textResizeObserver.observe(textarea));
  }
  if (typeof MutationObserver !== 'undefined') {
    textStyleObserver = new MutationObserver(scheduleFertilizerTextGrowth);
    textStyleObserver.observe(root, { attributes: true, subtree: true, attributeFilter: ['style', 'class', 'dir', 'rows'] });
    // Inherited text scaling may be set on a page ancestor rather than the textarea itself.
    for (let parent = root.parentElement; parent; parent = parent.parentElement) {
      textStyleObserver.observe(parent, { attributes: true, attributeFilter: ['style', 'class', 'dir'] });
    }
  }
  document.fonts?.ready.then(() => {
    if (root.isConnected) growFertilizerText(root, true);
  });
}
function rememberFertilizerView() {
  if (typeof document === 'undefined') return;
  const root = document.querySelector('[data-fertilizer-view]');
  if (!root) return;
  const key = root.dataset.fertilizerView, previous = viewStates[key] || {};
  const active = document.activeElement, focused = root.contains(active) && active.dataset.testid;
  const selection = focused && typeof active.selectionStart === 'number' ? [active.selectionStart, active.selectionEnd] : null;
  viewStates[key] = {
    open: Object.fromEntries([...root.querySelectorAll('[data-fertilizer-disclosure]')].map((item) => [item.dataset.fertilizerDisclosure, item.open])),
    scroll: window.scrollY,
    focus: focused || previous.focus || null,
    selection: focused ? selection : previous.selection || null,
    fallback: active?.dataset?.action?.startsWith('fertilizer:remove-component:') ? `fertilizer-add-component-${active.dataset.action.split(':')[3]}` : null,
  };
  try { window.sessionStorage.setItem(viewStorageKey, JSON.stringify(viewStates)); } catch { /* Keep the in-memory fallback. */ }
}
function restoreFertilizerPosition(root, saved) {
  if (!saved) return;
  const targets = [...root.querySelectorAll('[data-testid]')];
  let target = targets.find((item) => item.dataset.testid === saved.focus);
  if (!target && saved.focus?.startsWith('fertilizer-remove-')) {
    target = targets.find((item) => item.dataset.testid === saved.fallback) || root.querySelector('[data-testid^="fertilizer-add-component-"]');
  }
  if (target?.getClientRects().length) {
    // The host already focuses changed controls. Do not disturb a native select
    // that is still processing its keyboard interaction by focusing it again.
    if (document.activeElement !== target) target.focus({ preventScroll: true });
    if (Array.isArray(saved.selection) && saved.selection.length === 2 && typeof target.setSelectionRange === 'function' && !['date', 'checkbox'].includes(target.type)) target.setSelectionRange(...saved.selection);
  }
  window.scrollTo(0, Number.isFinite(saved.scroll) ? saved.scroll : 0);
}
function prepareFertilizerView(id, stage) {
  if (typeof document === 'undefined' || typeof requestAnimationFrame === 'undefined') return;
  rememberFertilizerView();
  const key = `${id}:${stage}`, saved = viewStates[key], frame = ++viewFrame;
  const sameView = document.querySelector('[data-fertilizer-view]')?.dataset.fertilizerView === key;
  let routePosition = saved, routeInteraction = viewInteraction, root;
  restoringView = true;
  if (!sameView) {
    // This callback precedes the host's route rAF. Capture any newer user
    // position before the host can replace it with its own saved position.
    requestAnimationFrame(() => {
      if (frame !== viewFrame || !root?.isConnected) return;
      if (routeInteraction !== viewInteraction) {
        rememberFertilizerView();
        routePosition = viewStates[key];
        routeInteraction = viewInteraction;
      }
    });
  }
  // The host installs our markup synchronously after renderFertilizer returns.
  // A microtask restores the same-view working position before the next input,
  // rather than leaving a two-frame window for a newer action to be overwritten.
  queueMicrotask(() => {
    if (frame !== viewFrame) return;
    root = document.querySelector('[data-fertilizer-view]');
    if (!root || root.dataset.fertilizerView !== key) { restoringView = false; return; }
    growFertilizerText(root);
    observeFertilizerText(root);
    restoreFertilizerPosition(root, saved);
    restoringView = false;
    if (sameView) return;
    // Register after the host's route rAF. Both callbacks run in the same frame;
    // the most recent user position wins over either saved restoration.
    requestAnimationFrame(() => {
      if (frame !== viewFrame || !root.isConnected || viewInteraction !== routeInteraction) return;
      restoringView = true;
      restoreFertilizerPosition(root, routePosition);
      restoringView = false;
      rememberFertilizerView();
    });
  });
}
if (typeof document !== 'undefined' && typeof window !== 'undefined') {
  for (const type of ['pointerdown', 'keydown', 'wheel', 'touchstart']) {
    document.addEventListener(type, () => {
      if (document.querySelector('[data-fertilizer-view]')) viewInteraction++;
    }, { capture: true, passive: true });
  }
  document.addEventListener('toggle', (event) => {
    if (!event.target.matches?.('[data-fertilizer-disclosure]')) return;
    growFertilizerText(event.target);
    if (!restoringView) rememberFertilizerView();
  }, true);
  document.addEventListener('focusin', (event) => {
    if (!restoringView && event.target.closest?.('[data-fertilizer-view]')) rememberFertilizerView();
  });
  window.addEventListener('scroll', () => { if (!restoringView) rememberFertilizerView(); }, { passive: true });
  window.addEventListener('pagehide', rememberFertilizerView);
  window.addEventListener('resize', scheduleFertilizerTextGrowth);
  window.addEventListener('hashchange', stopFertilizerTextObservation);
  document.fonts?.addEventListener('loadingdone', () => growFertilizerText(document, true));
}

export function renderFertilizer(state, id, stage = 'edit') {
  const record = fertilizerRecord(state, id);
  if (!record) return EmptyState('Поручение не найдено', 'Откройте удобрение на складе и создайте черновик.', link('На склад', 'inventory', 'btn primary'));
  if (record.status === 'confirmed' || record.status === 'done') return confirmed(state, record);
  prepareFertilizerView(id, stage);
  return stage === 'review' ? review(state, record) : editor(state, record);
}
export function fertilizerTaskRows(state, stockId = null) {
  const records = (state.fertilizer?.records || []).filter((record) => !stockId || record.sourceStockId === stockId);
  if (!records.length) return '';
  return `<section class="card list-card fertilizer-task-list"><h2>Внесение · DEMO</h2>${records.map((record) => link(`<span class="task-icon">${icon('leaf')}</span><div><strong>${e(record.groups.map((group) => group.label).join(' · '))}</strong><p>${e(greenhouseName(state, record.greenhouseId))} · ${e(record.date)}</p><span class="tag ${record.status === 'draft' ? 'amber' : ''}">${({ draft: 'Черновик', confirmed: 'Создано · не отправлено', done: 'DONE · не факт внесения' })[record.status]}</span></div>${icon('arrow')}`, `fertilizer/${record.id}${record.status === 'draft' ? '' : '/task'}`, 'task-row', `fertilizer-task-${record.id}`)).join('')}</section>`;
}

// Update derived previews without replacing the active text field or swallowing a following click.
export function refreshFertilizerView(state, root = document) {
  const field = root.querySelector('[data-fertilizer-id]');
  const record = field && fertilizerRecord(state, field.dataset.fertilizerId);
  if (!record || record.status !== 'draft') return;
  const result = calculateFertilizer(state, record);
  const updates = { parts: partCards(result.groups), totals: totals(result, { editor: !!root.querySelector('.fertilizer-builder') }), alerts: alerts(result) };
  for (const [name, html] of Object.entries(updates)) {
    const target = root.querySelector(`[data-fertilizer-live="${name}"]`);
    if (target) target.innerHTML = html;
  }
  for (const fieldName of ['dariReviewed', 'ownerReviewed', 'warningsReviewed']) {
    const checkbox = root.querySelector(`[data-fertilizer-field="${fieldName}"]`);
    if (checkbox) checkbox.checked = record[fieldName] === true;
  }
  for (const group of record.groups) for (const component of group.components) {
    const indicator = root.querySelector(`[data-testid="fertilizer-override-state-${component.id}"]`);
    if (indicator) indicator.textContent = overrideLabel(component);
  }
  const instructionState = root.querySelector('[data-fertilizer-instruction-state]');
  if (instructionState) instructionState.textContent = record.instruction ? 'добавлена' : 'способ не задан';
  const invalidated = root.querySelector('[data-testid="fertilizer-review-invalidated"]');
  if (invalidated) invalidated.hidden = record.dariReviewed && record.ownerReviewed;
  growFertilizerText(root);
  rememberFertilizerView();
  const confirm = root.querySelector('[data-testid="fertilizer-confirm"]');
  if (confirm) confirm.dataset.revision = String(record.revision);
}

// Small DOM-event adapter: the host owns persistence, navigation, rendering and messages.
// Call for click/input/change before generic app listeners. No network or DOM mutations here.
export function handleFertilizerEvent(state, event) {
  const target = event.target;
  const input = target?.closest?.('[data-fertilizer-field]');
  if ((event.type === 'input' || event.type === 'change') && input) {
    // Checkboxes/selects are handled on change; avoids duplicate updates from browser input.
    if (event.type === 'input' && (input.type === 'checkbox' || input.tagName === 'SELECT')) return { handled: true, render: false };
    try {
      const value = input.type === 'checkbox' ? input.checked : input.value;
      updateFertilizerField(state, input.dataset.fertilizerId, input.dataset.fertilizerField, value, {
        groupId: input.dataset.groupId, componentId: input.dataset.componentId, partId: input.dataset.partId,
      });
      return { handled: true, render: event.type === 'change' && (input.type === 'checkbox' || input.tagName === 'SELECT'), refresh: input.type !== 'checkbox' && input.tagName !== 'SELECT' };
    } catch (cause) { return { handled: true, render: event.type === 'change', error: cause.message }; }
  }
  const control = target?.closest?.('[data-action]');
  if (event.type !== 'click' || !control?.dataset.action?.startsWith('fertilizer:')) return { handled: false };
  event.preventDefault?.();
  const [, action, id, groupId, componentId] = control.dataset.action.split(':');
  try {
    if (action === 'start') { const record = startFertilizerDraft(state, id); return { handled: true, route: `fertilizer/${record.id}` }; }
    if (action === 'add-component') addFertilizerComponent(state, id, groupId);
    else if (action === 'remove-component') removeFertilizerComponent(state, id, groupId, componentId);
    else if (action === 'add-group') addFertilizerGroup(state, id);
    else if (action === 'remove-group') removeFertilizerGroup(state, id, groupId);
    else if (action === 'save') return { handled: true, render: false, notice: 'Черновик DEMO сохранён на этом устройстве.' };
    else if (action === 'review') return { handled: true, route: `fertilizer/${id}/review` };
    else if (action === 'confirm') { confirmFertilizer(state, id, Number(control.dataset.revision)); return { handled: true, route: `fertilizer/${id}/task`, notice: 'Создано только в DEMO. Внешней отправки нет.' }; }
    else if (action === 'done') markFertilizerDone(state, id);
    else return { handled: false };
    return { handled: true, render: true };
  } catch (cause) { return { handled: true, error: cause.message, render: true }; }
}
