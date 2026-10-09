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
function alerts(result) {
  return `${result.issues.length ? `<div class="notice amber" data-testid="fertilizer-issues"><strong>Нужно уточнить</strong><ul>${result.issues.map((issue) => `<li>${e(issue)}</li>`).join('')}</ul><p>Черновик можно сохранить с неизвестными данными.</p></div>` : ''}${result.warnings.length ? `<div class="notice amber" data-testid="fertilizer-warnings"><strong>Проверьте ограничения</strong><ul>${result.warnings.map((warning) => `<li>${e(warning)}</li>`).join('')}</ul><p>Дозы не изменены. Задание ничего не списывает.</p></div>` : ''}`;
}
function partCards(groups, { worker = false } = {}) {
  return groups.map((group) => `<section class="calculation-group" data-testid="fertilizer-group-preview-${e(group.id)}"><h3>${e(group.label || 'Смесь без названия')} · отдельное приготовление</h3>${group.parts.length ? group.parts.map((part) => `<article class="part-calculation" data-testid="${worker ? 'worker' : 'fertilizer-calculation'}-part-${e(group.id)}-${e(part.id)}"><div class="summary-pair"><strong>${e(part.label)}</strong><span>${part.areaDa == null ? 'Площадь неизвестна' : `${displayDecimal(part.areaDa)} da · ${part.provenance === 'current-reference-design-2026-10-01' ? 'справочно' : 'DEMO'}`}</span></div><p class="note">${e(part.irrigationLabel || 'Полив неизвестен')} · ${e(part.barrelLabel || 'Бочка неизвестна')}</p>${part.components.map((item) => `<div class="calculation-line"><strong>${e(item.name)}${item.override ? ' · своя норма' : ''}</strong><span>${item.dosePerDa == null || part.areaDa == null ? 'Количество неизвестно' : `${displayDecimal(item.dosePerDa)} ${unitLabel(item.unit)}/da × ${displayDecimal(part.areaDa)} da =`} <b>${qty(item.quantity, item.unit)}</b></span><small>Источник: ${e(item.place || 'место неизвестно')}</small></div>`).join('')}</article>`).join('') : '<p class="note">Части пока не выбраны.</p>'}</section>`).join('');
}
function totals(result) {
  return `<section class="card fertilizer-totals"><span class="eyebrow">Потребность для владельца</span><h2>${result.totalAreaDa == null ? 'Площадь неизвестна' : `${displayDecimal(result.totalAreaDa)} da · DEMO`}</h2><p class="note">Площадь каждой части учтена один раз. Килограммы и литры не складываются.</p>${result.needs.map((need) => `<div class="depot-line" data-testid="fertilizer-total-${e(need.stockId)}"><div class="summary-pair"><strong>${e(need.name)}</strong><strong>${qty(need.quantity, need.unit)}</strong></div><p class="note">${e(need.place || 'Место неизвестно')} · запас ${qty(need.stock, need.unit)}</p><span class="tag ${need.stockStatus === 'sufficient' ? '' : 'amber'}">${({ sufficient: 'Хватает в DEMO', insufficient: 'Не хватает', unknown: 'Достаточность неизвестна' })[need.stockStatus]}</span></div>`).join('')}</section>`;
}
function componentFields(state, record, group, component, index) {
  const products = fertilizerProducts(state), product = products.find((row) => row.id === component.stockId);
  const context = { 'group-id': group.id, 'component-id': component.id };
  const selected = fertilizerParts(record.greenhouseId, record.areaMode).filter((part) => group.partIds.includes(part.id));
  return `<div class="mix-component" data-testid="fertilizer-component-${e(component.id)}"><span class="component-number" aria-hidden="true">${index + 1}</span><div class="component-main"><label class="field"><span>Удобрение ${index + 1}</span><select class="input" ${attribute(record, 'stockId', context)} data-testid="fertilizer-product-${e(component.id)}">${options(products.map((row) => [row.id, `${row.name} · ${row.place}`]), component.stockId)}</select></label><p class="note">${e(product?.place || 'Место неизвестно')} · запас ${qty(product?.quantity, component.unit)}</p><label class="field dose-control"><span>Доза на 1 da</span><div class="quantity-inline"><input class="input" type="text" inputmode="decimal" autocomplete="off" placeholder="Например, 10" value="${e(component.dosePerDa)}" ${attribute(record, 'dosePerDa', context)} data-testid="fertilizer-dose-${e(component.id)}"><span>${unitLabel(component.unit)}/da</span></div></label>${selected.length ? `<details class="source-details"><summary>Своя дозировка для части</summary><p class="note">Пустое поле использует общую дозу. Изменение относится только к указанной части.</p>${selected.map((part) => `<label class="field"><span>${e(part.label)} · ${unitLabel(component.unit)}/da</span><input class="input" type="text" inputmode="decimal" placeholder="Общая дозировка" value="${e(component.overrides[part.id]?.dosePerDa || '')}" ${attribute(record, 'override', { ...context, 'part-id': part.id })} data-testid="fertilizer-override-${e(component.id)}-${e(part.id)}"></label>`).join('')}</details>` : ''}</div>${button(icon('close'), `fertilizer:remove-component:${record.id}:${group.id}:${component.id}`, 'icon-button', `fertilizer-remove-${component.id}`, `aria-label="Удалить ${e(product?.name || 'компонент')}"`)}</div>`;
}
function mixEditor(state, record, group, index) {
  const parts = fertilizerParts(record.greenhouseId, record.areaMode);
  const all = parts.length > 0 && parts.every((part) => group.partIds.includes(part.id));
  return `<section class="card mixture-card" data-testid="fertilizer-mix-${e(group.id)}"><div class="card-header"><h2>${e(group.label || `Смесь ${index + 1}`)}</h2>${record.groups.length > 1 ? button('Убрать', `fertilizer:remove-group:${record.id}:${group.id}`, 'text-button', `fertilizer-remove-group-${group.id}`) : ''}</div>${index > 0 ? `<label class="field"><span>Название отдельной смеси / приготовления</span><input class="input" value="${e(group.label)}" ${attribute(record, 'groupLabel', { 'group-id': group.id })}></label>` : ''}<div class="fertilizer-components">${group.components.map((component, i) => componentFields(state, record, group, component, i)).join('')}</div>${button(`${icon('plus')} Добавить ещё удобрение`, `fertilizer:add-component:${record.id}:${group.id}`, 'secondary full', `fertilizer-add-component-${group.id}`)}<fieldset class="part-selector"><legend>Куда внести эту смесь</legend><label class="part-chip all-parts"><input type="checkbox" ${attribute(record, 'allParts', { 'group-id': group.id })} ${all ? 'checked' : ''} data-testid="fertilizer-all-${group.id}"><span>Все части</span></label>${parts.map((part) => `<label class="part-chip ${group.partIds.includes(part.id) ? 'selected' : ''}"><input type="checkbox" ${attribute(record, 'part', { 'group-id': group.id, 'part-id': part.id })} ${group.partIds.includes(part.id) ? 'checked' : ''} data-testid="fertilizer-part-${group.id}-${part.id}"><span>${e(part.label)}<small>${part.areaDa == null ? 'Площадь неизвестна' : `${displayDecimal(part.areaDa)} da · ${part.provenance === 'current-reference-design-2026-10-01' ? 'справочно' : 'DEMO'}`}</small></span></label>`).join('')}</fieldset><p class="note">Полив и бочки берутся из выбранной библиотеки. Состав списка не подтверждает совместимость препаратов.</p></section>`;
}
function editor(state, record) {
  const result = calculateFertilizer(state, record), worker = responsibleWorker(state, record.greenhouseId);
  const greenhouses = state.daily?.greenhouses || [];
  return `<div class="detail-layout fertilizer-builder" data-r3-form="fertilizer">${Header('Внесение удобрений', 'Состав → части → проверка поручения', true)}<div class="notice" data-testid="fertilizer-demo-label">DEMO · без списания и отправки. Площади и части проверяются ниже.</div>${record.groups.map((group, index) => mixEditor(state, record, group, index)).join('')}${button(`${icon('plus')} Отдельная смесь / бочка`, `fertilizer:add-group:${record.id}`, 'secondary full', 'fertilizer-add-group')}<section class="card fertilizer-meta"><h2>Параметры поручения</h2><p class="note">Части CURRENT показаны справочно. Их ID только DEMO; реальные бочки и полив не подтверждены. Учебный пример выбирается отдельно.</p><div class="form-grid"><label class="field"><span>Теплица</span><select class="input" ${attribute(record, 'greenhouseId')} data-testid="fertilizer-greenhouse">${options(greenhouses.map((greenhouse) => [greenhouse.id, greenhouse.name]), record.greenhouseId)}</select></label><label class="field"><span>Дата поручения</span><input class="input" type="date" value="${e(record.date)}" ${attribute(record, 'date')} data-testid="fertilizer-date"></label></div><label class="field"><span>Библиотека частей и площадей</span><select class="input" ${attribute(record, 'areaMode')} data-testid="fertilizer-area-mode">${options([['reference', 'CURRENT · справочная топология частей'], ['synthetic', 'Отдельный учебный пример P1/P2/P3 · 5/5/10'], ['unknown', 'Непроверенная площадь · UNKNOWN']], record.areaMode)}</select></label>${record.areaMode === 'synthetic' ? '<div class="notice amber" data-testid="fertilizer-teaching-mode">P1/P2/P3 и бочки ниже — отдельный синтетический пример. Это не топология выбранной теплицы и не реальное поручение.</div>' : ''}<p class="note">Ответственный по теплице: <strong data-testid="fertilizer-auto-worker">${e(worker?.name || 'Не установлен')}</strong>. Назначение проверяется перед подтверждением.</p></section><section class="card"><label class="field"><span>Исходная инструкция владельца</span><textarea class="input" rows="3" placeholder="Способ приготовления пока не задан" ${attribute(record, 'instruction')} data-testid="fertilizer-instruction">${e(record.instruction)}</textarea></label><p class="note">Совместимость и способ приготовления не определяются автоматически. Никакого перевода или внешней отправки.</p></section><section class="card"><h2>Расчёт по каждой части</h2><p class="note">Доза на 1 da × площадь части = количество для этой части.</p><div data-fertilizer-live="parts">${partCards(result.groups)}</div></section><div data-fertilizer-live="totals">${totals(result)}</div><div data-fertilizer-live="alerts">${alerts(result)}</div><div class="fertilizer-actions">${button('Сохранить черновик', `fertilizer:save:${record.id}`, 'secondary full', 'fertilizer-save')}${button('Проверить поручение', `fertilizer:review:${record.id}`, 'primary full', 'fertilizer-review')}</div><p class="note">Черновик хранится на этом устройстве. Склад, долг и деньги не меняются.</p></div>`;
}
function review(state, record) {
  const result = calculateFertilizer(state, record), packet = fertilizerWorkerPacket(state, record);
  return `<div class="detail-layout fertilizer-review" data-r3-form="fertilizer-review">${Header('Проверка поручения', `${greenhouseName(state, record.greenhouseId)} · ${record.date}`, true)}<section class="card"><span class="eyebrow">Получатель определён по теплице</span><h2 data-testid="fertilizer-review-worker">${e(result.worker?.name || 'Ответственный не установлен')}</h2><dl class="fact-list">${fact('Теплица', greenhouseName(state, record.greenhouseId))}${fact('Дата', record.date)}${fact('Площади', record.areaMode === 'synthetic' ? 'Учебный пример, не топология теплицы' : record.areaMode === 'reference' ? 'CURRENT справочно; ID/бочки не подтверждены' : 'Не подтверждены')}</dl><details class="source-details"><summary>Исключение: другой ответственный</summary><p class="note">Только для этого поручения. Правило ответственности теплицы не изменяется.</p><label class="field"><span>Получатель для исключения</span><select class="input" ${attribute(record, 'workerOverrideId')} data-testid="fertilizer-worker-override">${options([['', 'По правилу теплицы'], ...state.workers.map((worker) => [worker.id, worker.name])], record.workerOverrideId)}</select></label><label class="field"><span>Причина исключения</span><input class="input" value="${e(record.workerOverrideReason)}" ${attribute(record, 'workerOverrideReason')} data-testid="fertilizer-worker-reason"></label></details></section><section class="card"><span class="eyebrow">Работнику · разбивка по частям</span><h2>Каждая часть отдельно</h2>${partCards(packet.groups, { worker: true })}<h3>Исходная инструкция</h3><p class="original-text" data-testid="fertilizer-original-instruction">${e(packet.preparation)}</p><p class="note">Совместимость препаратов не подтверждена автоматически.</p></section><div data-fertilizer-live="totals">${totals(result)}</div><div data-fertilizer-live="alerts">${alerts(result)}</div><section class="card"><h2>Текст на дари</h2><p class="note">Автоперевода нет. Введите отдельно подготовленный текст и вручную сверьте получателя, дату, каждую часть, единицы и числа.</p><label class="field"><span>Проверяемая версия для работника</span><textarea class="input translation" rows="5" lang="fa" dir="rtl" ${attribute(record, 'dari')} data-testid="fertilizer-dari">${e(record.dari)}</textarea></label>${check(record, 'dariReviewed', 'Я вручную проверил текст на дари, части, числа и единицы', 'fertilizer-dari-reviewed')}${check(record, 'ownerReviewed', 'Проверил получателя, дату, части и дозы на 1 da', 'fertilizer-owner-reviewed')}${result.warnings.length ? check(record, 'warningsReviewed', 'Вижу предупреждения о запасе, месте хранения и неизвестных поливе / бочках; сохраняю только поручение DEMO', 'fertilizer-warnings-reviewed') : ''}</section><div class="notice">Одно подтверждение создаёт только поручение DEMO. Telegram не отправляется. Склад и финансы не меняются.</div><div class="fertilizer-actions">${link('Изменить состав и части', `fertilizer/${record.id}`, 'btn secondary full', 'fertilizer-edit')}${button('Подтвердить поручение DEMO', `fertilizer:confirm:${record.id}`, 'primary full', 'fertilizer-confirm', `data-revision="${record.revision}"`)}</div></div>`;
}
function confirmed(state, record) {
  const packet = record.confirmedPacket;
  return `<div class="detail-layout fertilizer-review">${Header('Поручение на внесение', `${greenhouseName(state, record.greenhouseId)} · ${record.date}`, true)}<div class="notice" data-testid="fertilizer-confirmed">${record.status === 'done' ? 'DEMO: работник отметил DONE. Факт внесения владельцем не подтверждён.' : 'Поручение создано в DEMO. Работнику не отправлено.'}</div>${alerts({ issues: [], warnings: packet.warnings || [] })}<section class="card"><h2>${e(state.workers.find((worker) => worker.id === packet.recipientId)?.name || 'Получатель неизвестен')}</h2><p class="note">${record.areaMode === 'synthetic' ? 'Учебный пример площадей, не топология теплицы.' : 'Части CURRENT справочно; их ID только DEMO. Физические бочки и полив не подтверждены.'} Запас и финансы не изменены.</p>${partCards(packet.groups, { worker: true })}<h3>Исходная инструкция</h3><p class="original-text">${e(packet.preparation)}</p><h3>Вручную проверенный текст на дари</h3><div class="translation" lang="fa" dir="rtl">${e(packet.dari)}</div></section>${record.status !== 'done' ? button('DEMO: работник отметил DONE', `fertilizer:done:${record.id}`, 'secondary full', 'fertilizer-done') : '<p class="note" data-testid="fertilizer-done-status">DONE ≠ подтверждённое фактическое внесение. Движений склада не создано.</p>'}${link('Вернуться к товару', `stock/${record.sourceStockId}`, 'btn primary full', 'fertilizer-stock-return')}</div>`;
}
export function renderFertilizer(state, id, stage = 'edit') {
  const record = fertilizerRecord(state, id);
  if (!record) return EmptyState('Поручение не найдено', 'Откройте удобрение на складе и создайте черновик.', link('На склад', 'inventory', 'btn primary'));
  if (record.status === 'confirmed' || record.status === 'done') return confirmed(state, record);
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
  const updates = { parts: partCards(result.groups), totals: totals(result), alerts: alerts(result) };
  for (const [name, html] of Object.entries(updates)) {
    const target = root.querySelector(`[data-fertilizer-live="${name}"]`);
    if (target) target.innerHTML = html;
  }
  for (const fieldName of ['dariReviewed', 'ownerReviewed', 'warningsReviewed']) {
    const checkbox = root.querySelector(`[data-fertilizer-field="${fieldName}"]`);
    if (checkbox) checkbox.checked = record[fieldName] === true;
  }
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
