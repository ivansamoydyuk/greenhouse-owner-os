/** Read-only money presentation. Every authoritative amount comes from TEST API. */
import { e, link, Header } from './components.js';
import { harvestNumber } from './harvest-connected.js';

export const financeTabs = {overview:'Обзор',cash:'Деньги',result:'Результат',debts:'Долги',areas:'Теплицы'};
const iso = value => /^\d{4}-\d{2}-\d{2}$/.test(value || '') && !Number.isNaN(Date.parse(value+'T12:00:00Z')) && new Date(value+'T12:00:00Z').toISOString().slice(0,10)===value;
export function financeSelection(route,today) {
 const [,tab='overview',from='',to='',currency='TRY',greenhouse='all',account='all']=route.split('/');
 const [year,month]=today.split('-').map(Number);
 return {tab:Object.hasOwn(financeTabs,tab)?tab:'overview',date_from:iso(from)?from:`${year-(month<10?1:0)}-10-01`,date_to:iso(to)?to:today,currency:['TRY','USD','EUR','UAH'].includes(currency)?currency:'TRY',greenhouse_id:greenhouse==='all'?null:greenhouse,money_account_id:account==='all'?null:account};
}
export function financeRoute(f,changes={}) {
 const x={...f,...changes};
 return `money/${x.tab}/${x.date_from}/${x.date_to}/${x.currency}/${x.greenhouse_id || 'all'}/${x.money_account_id || 'all'}`;
}
export function financeQuery(f) {
 const p=new URLSearchParams({date_from:f.date_from,date_to:f.date_to,currency:f.currency});
 if(f.greenhouse_id)p.set('greenhouse_id',f.greenhouse_id);
 if(f.money_account_id)p.set('money_account_id',f.money_account_id);
 return p.toString();
}
export const financeNumber = harvestNumber;
export function financeAmount(value,currency) {return value==null?'Неизвестно':`${financeNumber(value)} ${currency || ''}`.trim();}
export function financeDate(value) {return value?String(value).slice(0,10).split('-').reverse().join('.'):'Не указана';}
const card=(body,extra='')=>`<section class="card money-card ${extra}">${body}</section>`;
const note=text=>`<p class="note">${e(text)}</p>`;
const row=(label,value,sub='')=>`<div class="money-fact"><div><span>${e(label)}</span>${sub?`<small>${e(sub)}</small>`:''}</div><strong class="exact-value">${e(value)}</strong></div>`;
const sourceNames={obligation:'Документ',payment:'Платёж',direct_expense:'Прямой расход',transfer:'Перевод между счетами',owner_funding:'Взнос владельца',advance_application:'Зачёт аванса',direct_settlement:'Прямой расчёт',opening_payable:'Начальный долг'};
export function financeSourceLinks(source,backRoute='money') {
 if(!source)return '';
 const links=[];
 if(source.day_actual_id)links.push(link('Фактический рабочий день','day-actual-detail/'+source.day_actual_id+'/'+encodeURIComponent(backRoute),'btn secondary'));
 if(source.day_request_id)links.push(link('Исходная заявка на работников','day-request/'+source.day_request_id+'/'+encodeURIComponent(backRoute),'btn secondary'));
 if(source.kind==='payment')links.push(link('Платёж','payment/'+source.id,'btn secondary'));
 if(source.kind==='obligation')links.push(link('Документ','money-document/'+source.id+'/'+encodeURIComponent(backRoute),'btn secondary'));
 if(source.harvest_settlement_id)links.push(link('Документ депо','depot/'+source.harvest_settlement_id,'btn secondary'));
 if(source.proposal_id)links.push(link('Проверенные факты','review/'+source.proposal_id,'btn secondary'));
 if(source.original_id)links.push(link('Оригинал','source/'+source.original_id,'btn secondary'));
 return `<div class="page-actions">${links.join('')}</div>${source.original_text || source.source_reference?`<details><summary>Сохранённое основание</summary>${source.original_text?`<p class="source-text">${e(source.original_text)}</p>`:''}${source.source_reference?`<p class="note source-text">${e(source.source_reference)}</p>`:''}</details>`:''}`;
}
function filters(r,f) {
 return `<form data-form="finance-filters" class="money-filters"><label>С даты<input type="date" name="date_from" value="${e(f.date_from)}" required></label><label>По дату включительно<input type="date" name="date_to" value="${e(f.date_to)}" required></label><label>Валюта<select name="currency">${['TRY','USD','EUR','UAH'].map(c=>`<option ${c===f.currency?'selected':''}>${c}</option>`).join('')}</select></label><label>Теплица · результат<select name="greenhouse_id"><option value="">Все теплицы</option>${r.areas.map(g=>`<option value="${e(g.greenhouse_id)}" ${g.greenhouse_id===f.greenhouse_id?'selected':''}>${e(g.name)}</option>`).join('')}</select></label>${f.tab==='cash'?`<label>Счёт · деньги<select name="money_account_id"><option value="">Все счета</option>${(r.available_accounts || r.accounts).map(a=>`<option value="${e(a.money_account_id)}" ${a.money_account_id===f.money_account_id?'selected':''}>${e(a.name)}</option>`).join('')}</select></label>`:''}<button type="submit" class="btn secondary">Показать</button><div class="form-error" role="alert"></div></form>`;
}
function summaryCard(label,value,currency,to,sub) {
 return link(`<span class="label">${e(label)}</span><strong class="money-kpi exact-value">${e(financeAmount(value,currency))}</strong><small>${e(sub)}</small>`,to,'card money-summary');
}
function accountCards(r,f) {
 return r.accounts.map(a=>card(`<h2>${e(a.name)}</h2>${row('На начало периода',financeAmount(a.opening_balance,a.currency))}${row('Поступления',financeAmount(a.incoming,a.currency))}${row('Выплаты',financeAmount(a.outgoing,a.currency))}${row('Изменение',financeAmount(a.net_change,a.currency))}${row('На конец периода',financeAmount(a.closing_balance,a.currency))}<details><summary>Основание остатка</summary>${note(a.opening_basis==='UNKNOWN_BEFORE_CUTOVER'?'Начало периода предшествует подтверждённой начальной позиции. Остаток на эту дату неизвестен; движения показаны отдельно.':`Подтверждённый нулевой остаток при начале учёта ${financeDate(r.cutover_date)} плюс проводки. Это учётный остаток, не автоматическая сверка банковской выписки.`)}${row('Текущий учётный остаток',financeAmount(a.current_balance,a.currency))}</details>`)).join('') || card(note('Нет доступных счетов этой валюты. Остаток не подставлен как ноль.'));
}
function movementRows(r,f) {
 return card(`<h2>Денежные движения периода</h2>${note('Переводы и взносы видны отдельными операциями. Они не становятся выручкой. Остатки счетов не распределяются по теплицам.')}${r.movements.map(m=>`<article class="money-event"><div class="card-header"><strong>${e(m.source.description || sourceNames[m.source.kind] || 'Денежное событие')}</strong><span class="exact-value">${e(financeAmount(m.signed_amount,m.currency))}</span></div><p>${e(financeDate(m.event_date))} · ${e(m.account_name)}${m.reversal_of_id?' · Обратная проводка':''}</p>${financeSourceLinks(m.source,financeRoute(f))}<details><summary>Проводка и источник</summary>${note(`Проводка ${m.posting_id} · ${sourceNames[m.source.kind] || m.source.kind} ${m.source.id}`)}</details></article>`).join('') || note('За выбранный период денежных движений нет.')}`);
}
export function visibleEconomicEvents(events,greenhouseId) {
 return greenhouseId?events.filter(v=>!/^[-+]?0*(?:\.0*)?$/.test(String(v.selected_amount)) || v.allocations.some(a=>a.greenhouse_id===greenhouseId)):events;
}
export function denominatorCoverage(d) {
 const labels={EXACT:'Точный вес документов',ESTIMATED:'Есть оценочное распределение',PARTIAL:'Покрытие веса неполное',UNAVAILABLE:'Нет подтверждённого знаменателя'};
 return note(labels[d.classification] || 'Полнота веса не установлена')+((d.unallocated_kg && !/^0*(?:\.0*)?$/.test(d.unallocated_kg))?note('Без распределения в выбранном охвате: '+financeNumber(d.unallocated_kg)+' кг. Это не вес выбранной теплицы.'):'')+(d.basis_incomplete?note('В основании распределения есть неизвестная доля; округлённый остаток 0 не делает покрытие полным.'):'');
}
function economicsCards(r,f) {
 const x=r.economics;
 return card(`<h2>Результат по учтённым затратам</h2>${row('Выручка',financeAmount(x.revenue,r.currency))}${row('Учтённые операционные затраты',financeAmount(x.operating_cost,r.currency))}${row('Результат',financeAmount(x.operating_result,r.currency))}${note('Подтверждённые действующие экономические события периода. Оплаты не добавляются повторно. Исторический срез «как было известно на дату» пока недоступен.')}${row('Виртуальный GENERAL в затратах',financeAmount(x.virtual_general_cost,r.currency))}${row('Неразрешённая принадлежность затрат',financeAmount(x.unresolved_cost,r.currency))}${row('Выручка без распределения',financeAmount(x.unallocated_revenue,r.currency))}`)+
 card(`<h2>Учтённые затраты на кг</h2>${row('Известная часть',financeAmount(r.cost_per_kg.known_finance_cost_per_kg,r.currency+'/кг'))}${row('Знаменатель · подтверждённое депо',r.harvest_denominator.confirmed_kg==null?'Неизвестно':financeNumber(r.harvest_denominator.confirmed_kg)+' кг')}${row('В том числе оценка',financeNumber(r.harvest_denominator.estimated_kg)+' кг')}${denominatorCoverage(r.harvest_denominator)}${r.cost_per_kg.classification==='PARTIAL'?note('Расчёт на кг недоступен: неполный знаменатель или неразрешённая принадлежность затрат.'):r.cost_per_kg.classification==='ESTIMATED_DENOMINATOR'?note('Расчёт использует оценочный вес; это оценочный показатель.'):r.cost_per_kg.classification==='UNAVAILABLE'?note('Расчёт на кг недоступен без положительного подтверждённого веса.'):''}${note('Это не полная себестоимость. Неизвестные оценки материалов не заменены нулём.')}${r.harvest_denominator.stale_estimate_count?note(`Неактуальных распределений: ${r.harvest_denominator.stale_estimate_count}. Они не подменяют текущий знаменатель.`):''}<details><summary>Исходные документы знаменателя</summary>${r.harvest_denominator.sources.map(s=>link(`${e(financeDate(s.event_date))} · ${e(financeNumber(s.exact_kg))} кг точно / ${e(financeNumber(s.estimated_kg))} кг оценка`,'depot/'+s.settlement_id,'list-row')).join('') || note('Подтверждённых документов нет.')}</details>`)+
 card(`<h2>Из чего складывается результат</h2>${visibleEconomicEvents(r.economic_events,f.greenhouse_id).map(v=>`<article class="money-event"><span class="tag">${v.classification==='REVENUE'?'Выручка':'Затраты'}</span><h3>${e(v.source.description || v.ledger_account_name)}</h3>${row(financeDate(v.event_date),financeAmount(v.selected_amount,v.currency))}${financeSourceLinks(v.source,financeRoute(f))}<details><summary>Принадлежность и основание</summary>${v.allocations.map(a=>row(a.name || 'Не распределено',financeAmount(a.amount,v.currency),a.classification==='GENERAL_VIRTUAL'?'GENERAL · виртуально, ledger не меняется':a.classification==='ESTIMATED'?'Оценка по подтверждённому основанию':a.classification==='UNRESOLVED'?'Требуется уточнение':'Прямое распределение')).join('')}${note('Проводка '+v.posting_id)}</details></article>`).join('') || note('В выбранном периоде и охвате нет признанных экономических событий.')}${f.greenhouse_id?link('Все основания и нераспределённая часть',financeRoute(f,{greenhouse_id:null}),'btn secondary'):''}`);
}
function debtCards(r,f) {
 return note('Текущие долги по всем датам в выбранной валюте. Фильтр периода относится к движениям и результату; он не превращает эти остатки в исторический срез.')+
 (r.debts.parties.map(p=>card(`<h2>${e(p.name)}</h2>${row('Мне должны',financeAmount(p.receivable,r.currency))}${row('Я должен',financeAmount(p.payable,r.currency))}${r.debts.documents.filter(d=>d.counterparty_id===p.counterparty_id).map(d=>link(`<strong>${e(d.source.description || 'Документ')}</strong><small>${e(financeDate(d.event_date))} · ${e(d.direction==='receivable'?'Мне должны':'Я должен')} · ${e(financeAmount(d.remaining_amount,d.currency))}${d.overdue?' · Просрочен':!d.due_date?' · Без срока':''}</small>`,'money-document/'+d.obligation_id+'/'+encodeURIComponent(financeRoute(f)),'list-row')).join('')}`)).join('') || card(note('Документов этой валюты нет.')));
}
function areaCards(r,f) {
 return note('Одинаковые период, валюта и экономическое основание для всех теплиц. Показана только отнесённая известная часть; 0 не доказывает отсутствие нераспределённых затрат или выручки. Деньги на банковских счетах здесь не распределяются.')+
 r.areas.map(a=>card(`<h2>${e(a.name)}</h2>${row('Выручка',financeAmount(a.revenue,r.currency))}${row('Учтённые затраты',financeAmount(a.operating_cost,r.currency))}${row('Результат',financeAmount(a.operating_result,r.currency))}${row('GENERAL · аналитическая доля',financeAmount(a.virtual_general_cost,r.currency))}${row('Выручка с оценочным распределением',financeAmount(a.estimated_revenue,r.currency))}${row('Подтверждённые кг',a.harvest_denominator.confirmed_kg==null?'Неизвестно':financeNumber(a.harvest_denominator.confirmed_kg)+' кг')}${denominatorCoverage(a.harvest_denominator)}${link('Открыть основания',financeRoute(f,{tab:'result',greenhouse_id:a.greenhouse_id}),'btn secondary')}`)).join('');
}
function coverage(r) {
 return card(`<details><summary>Полнота и правила расчёта</summary>${note('Полная себестоимость не подтверждена. Покупка и потребление одного материала не добавляются дважды. Выбрано экономическое основание Finance; отдельные складские оценки показаны для сверки.')}${r.coverage.inventory_cost_rows.map(v=>`<div class="list-row"><strong>${e(v.product_name)}</strong>${row('Количество',financeNumber(v.quantity)+' '+v.base_unit)}${row('Без подтверждённой стоимости',financeNumber(v.uncosted_quantity)+' '+v.base_unit)}${note(v.recognition==='FINANCE_BASIS_ONLY'?'Стоимость признана по связанному финансовому основанию.':'Связь или момент признания требует уточнения.')}${link('Складской источник','receipt/'+v.event_id,'btn secondary')}</div>`).join('')}${r.coverage.unclassified_obligation_ids.length?note(`Документов без определённого экономического класса: ${r.coverage.unclassified_obligation_ids.length}. Не объявлены операционным расходом.`):''}<h3>GENERAL</h3>${note(`Политика ADR-033, версия ${r.allocation_policy.version}, с ${financeDate(r.allocation_policy.effective_from)}. Прямое распределение имеет приоритет. Виртуальная доля не изменяет исходную принадлежность.`)}${r.allocation_policy.targets.map(t=>row(t.name,`${t.numerator}/${t.denominator}`,t.greenhouse_id?'Подтверждённый справочник':'Ссылка справочника не установлена')).join('')}${r.allocation_policy.status!=='AVAILABLE'?note('Распределение GENERAL ограничено: не все ссылки на теплицы подтверждены.'):''}</details>`);
}
export function financeAnalyticsView(r,f) {
 const tabs=`<nav class="money-tabs" aria-label="Раздел финансов">${Object.entries(financeTabs).map(([k,label])=>link(e(label),financeRoute(f,{tab:k}),`money-tab ${k===f.tab?'active':''}`)).join('')}</nav>`;
 let body='';
 if(f.tab==='overview')body=`<div class="money-summary-grid">${summaryCard('Мне должны сейчас',r.debts.receivable,r.currency,financeRoute(f,{tab:'debts'}),'Документы всех дат')}${summaryCard('Я должен сейчас',r.debts.payable,r.currency,financeRoute(f,{tab:'debts'}),'Документы всех дат')}${summaryCard('Движение денег периода',r.cash_totals.net_change,r.currency,financeRoute(f,{tab:'cash'}),'Без подмены остатком счёта')}${summaryCard('Результат известной части',r.economics.operating_result,r.currency,financeRoute(f,{tab:'result'}),'Выручка минус учтённые затраты')}</div>`+card(`<h2>Счета сейчас</h2>${r.accounts.map(a=>row(a.name,financeAmount(a.current_balance,a.currency))).join('') || note('Нет счетов выбранной валюты.')}${link('Период и основания',financeRoute(f,{tab:'cash'}),'btn secondary')}`);
 if(f.tab==='cash')body=note('Деньги по хозяйству и выбранному счёту. Теплица не распределяет банковский остаток.')+accountCards(r,f)+movementRows(r,f);
 if(f.tab==='result')body=economicsCards(r,f);
 if(f.tab==='debts')body=debtCards(r,f);
 if(f.tab==='areas')body=areaCards(r,f);
 return Header('Финансы',`${financeDate(f.date_from)} — ${financeDate(f.date_to)} · ${f.currency}`,true)+`<div class="page-actions">${link('Записать расход','capture/expense','btn primary')}${link('Погашение долга','finance','btn secondary')}</div>`+tabs+filters(r,f)+(f.money_account_id?note('Деньги и остатки: только счёт «'+((r.available_accounts || r.accounts).find(a=>a.money_account_id===f.money_account_id)?.name || 'выбранный счёт')+'». Этот фильтр не ограничивает выручку, затраты и долги.'):note('Деньги и остатки: все счета выбранной валюты.'))+(r.coverage.economic_event_count===0&&r.coverage.movement_count===0?note('В выбранном периоде нет подтверждённых денежных или экономических событий. Текущие долги и остатки показаны отдельно.'):'')+body+coverage(r);
}
export function financeDocumentView(d,backRoute) {
 return Header(d.counterparty_name,d.direction==='receivable'?'Мне должны · документ':'Я должен · документ',true)+
 `<div class="page-actions">${d.status==='posted'&&d.settlement_status!=='settled'?link(d.direction==='receivable'?'Записать поступление':'Записать оплату',`settle/${d.direction}/${d.counterparty_id}/${d.currency}`,'btn primary'):''}${link(backRoute.startsWith('purchase/')?'Назад к поставке':backRoute.startsWith('invoice-source/')?'Назад к счёту':'Назад к финансам',backRoute,'btn secondary')}</div>`+
 card(`<h2>${e(d.source.description || 'Подтверждённый документ')}</h2>${row('Первоначальная сумма',financeAmount(d.original_amount,d.currency))}${row('Погашено',financeAmount(d.allocated_amount,d.currency))}${row('Текущий остаток',financeAmount(d.remaining_amount,d.currency))}${row('Дата события',financeDate(d.event_date))}${row('Срок',financeDate(d.due_date))}${note(d.status==='reversed'?'Документ отменён обратным событием. История сохранена.':'Текущее состояние документа; не исторический остаток на выбранную дату.')}${financeSourceLinks({...d.source,kind:d.source.kind==='obligation'?'document':d.source.kind},backRoute)}`)+
 card(`<h2>Оплаты и распределения</h2>${d.history.map(h=>`<article class="money-event">${row(h.kind==='payment'?'Платёж':h.kind==='advance_application'?'Зачёт аванса':'Прямой расчёт',financeAmount(h.amount,h.currency))}${note(financeDate(h.event_date)+(h.status==='reversed'?' · Отменён обратной проводкой':h.status==='posted'?' · Подтверждён':' · Черновик'))}${h.kind==='payment'?link('Открыть платёж','payment/'+h.id,'btn secondary'):''}${h.source?`<details><summary>Основание</summary><p class="source-text">${e(h.source)}</p></details>`:''}</article>`).join('') || note('Погашений пока нет.')}`);
}

export function financeBackRoute(encoded,fallback='money'){
 try{const candidate=decodeURIComponent(encoded||'');if(candidate.length<=1500&&(/^(money\/|greenhouses\/(?:all|[0-9a-f-]{36})\/(?:overview|harvest|finance|work)\/|day-actual-detail\/[0-9a-f-]{36}(?:\/|$)|day-request\/[0-9a-f-]{36}(?:\/|$)|purchase\/[0-9a-f-]{36}(?:\/|$)|invoice-source\/[0-9a-f-]{36}\/[0-9a-f-]{36}(?:\/|$))/.test(candidate)))return candidate;}catch{}
 return fallback;
}
