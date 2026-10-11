/** Read-only presentation of authoritative Harvest metrics. No browser ledger. */
import { e, link, Header } from './components.js';
import { exactDecimalString } from './connected-adapter.js';

export const harvestMetrics = {branches: 'Ветки', kg: 'Килограммы', revenue: 'Выручка'};
const months = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
export function harvestSelection(route, today) {
  const [, greenhouse='all', year='', metric='branches', currency='TRY', month='all',allocation='all'] = route.split('/');
  const [y,m] = today.split('-').map(Number);
  return {greenhouse_id: greenhouse === 'all' ? null : greenhouse,
    season_start_year: /^\d{4}$/.test(year) ? Number(year) : y - (m < 10 ? 1 : 0),
    metric: Object.hasOwn(harvestMetrics,metric) ? metric : 'branches',
    currency: ['TRY','USD','EUR','UAH'].includes(currency) ? currency : 'TRY',
    month: /^\d{4}-\d{2}$/.test(month) ? month : null,allocation:['all','exact','estimated'].includes(allocation)?allocation:'all'};
}
export function harvestRoute(f, changes={}) {
  const x={...f,...changes};
  return `harvest/${x.greenhouse_id || 'all'}/${x.season_start_year}/${x.metric}/${x.currency}/${x.month || 'all'}/${x.allocation || 'all'}`;
}
export function harvestQuery(f) {
  const p=new URLSearchParams({season_start_year:String(f.season_start_year),metric:f.metric,currency:f.currency,allocation:f.allocation || 'all'});
  if(f.greenhouse_id) p.set('greenhouse_id',f.greenhouse_id);
  return p.toString();
}
export function harvestNumber(value) {
  const text=exactDecimalString(value);
  if(text==null) return 'Неизвестно';
  const [integer,fraction]=text.split('.');
  return integer.replace(/\B(?=(\d{3})+(?!\d))/g,' ') + (fraction?.replace(/0+$/,'') ? ','+fraction.replace(/0+$/,'') : '');
}
function scaled(value) {
  const s=exactDecimalString(value);
  if(s==null || s.startsWith('-')) return null;
  const [a,b='']=s.split('.');
  return BigInt(a)*1000n+BigInt((b+'000').slice(0,3));
}
export function chartHeight(value, maximum) {
  const v=scaled(value), max=scaled(maximum);
  return v==null || max==null || max===0n ? 0 : Number(v*10000n/max)/100;
}
const card=body=>`<section class="card harvest-connected-card">${body}</section>`;
function coverageLabel(c={}) {
  return c.total === 0 ? 'Нет записей' : c.complete ? 'По всем записям выбранного периода' : `Полных записей ${c.known ?? 0} из ${c.total ?? 0} · есть неполные данные`;
}
function eventRows(events, unit) {
  return events.map(v=>link(`<div><strong>${e(v.name || v.label || (v.kind==='report' ? 'Полевой срез' : 'Документ депо'))}</strong><small>${e(String(v.event_date).split('-').reverse().join('.'))} · ${e(v.kind==='report' ? 'Поле' : 'Депо')}${v.estimated_value && scaled(v.estimated_value)>0n ? ' · Оценка' : ''}</small></div><span class="harvest-event-value">${e(harvestNumber(v.value))} ${e(unit)}<span aria-hidden="true"> ›</span></span>`,`${v.kind==='report' || v.kind==='field_report' ? 'report' : 'depot'}/${v.id}`,'list-row harvest-event')).join('') || '<p class="note">За этот месяц записей нет. Это не подтверждённый нулевой урожай.</p>';
}
export function monthlyHarvestView(result, selection) {
  const f=selection, greenhouse=(result.greenhouses || []).find(x=>x.id===f.greenhouse_id);
  const unit=f.metric==='branches' ? 'веток' : f.metric==='kg' ? 'кг' : f.currency;
  const selected=result.months.find(x=>x.month===f.month), summary=selected || result.total;
  const known=result.months.map(x=>x.value).filter(x=>x!=null);
  const maximum=known.reduce((a,b)=>scaled(a)>scaled(b)?a:b,'0');
  const events=selected?.events || result.months.flatMap(x=>x.events || []);
  return Header(greenhouse?.name || 'Урожай',`Сезон ${f.season_start_year}/${String(f.season_start_year+1).slice(2)} · 1 окт — 30 сен`,true)+
    `<div class="page-actions">${link('Записать срез','quick/harvest','btn primary')}${link('Добавить чек','attach-depot/new','btn secondary')}</div>`+
    `<nav class="harvest-metric-tabs" aria-label="Показатель урожая">${Object.entries(harvestMetrics).map(([k,n])=>link(e(n),harvestRoute(f,{metric:k}),`harvest-metric ${f.metric===k?'active':''}`,`harvest-metric-${k}`)).join('')}</nav>`+
    `<form data-form="harvest-filters" class="harvest-filters"><label>Сезон<select name="season_start_year">${[f.season_start_year-1,f.season_start_year,f.season_start_year+1].map(y=>`<option value="${y}" ${y===f.season_start_year?'selected':''}>${y}/${String(y+1).slice(2)}</option>`).join('')}</select></label><label>Теплица<select name="greenhouse_id"><option value="">Все теплицы</option>${(result.greenhouses||[]).map(g=>`<option value="${e(g.id)}" ${g.id===f.greenhouse_id?'selected':''}>${e(g.name)}</option>`).join('')}</select></label>${f.metric==='revenue'?`<label>Валюта<select name="currency">${['TRY','USD','EUR','UAH'].map(c=>`<option ${c===f.currency?'selected':''}>${c}</option>`).join('')}</select></label>`:''}<label>Основание<select name="allocation">${[['all','Все данные'],['exact','Точные'],['estimated','Оценки']].map(([v,n])=>`<option value="${v}" ${(f.allocation || 'all')===v?'selected':''}>${n}</option>`).join('')}</select></label><button class="btn secondary" type="submit">Показать</button></form>`+
    card(`<h2>По месяцам</h2>${f.allocation==='estimated'?'<p class="note">Распределение исходного итога, не дополнительный урожай.</p>':''}<div class="harvest-month-chart" role="group" aria-label="Месяцы сезона">${result.months.map(m=>`<a href="#${harvestRoute(f,{month:m.month})}" class="harvest-month ${f.month===m.month?'selected':''} ${m.value==null?'unknown':''}" aria-label="${e(m.month)}: ${e(m.value==null ? coverageLabel(m.coverage) : harvestNumber(m.value)+' '+unit)}"><span class="harvest-bar-track"><span class="harvest-bar bar-h-${Math.round(chartHeight(m.value,maximum))}"></span>${m.value==null?'<span class="harvest-no-data">—</span>':''}</span><span>${months[Number(m.month.slice(5))-1]}</span></a>`).join('')}</div><p class="note">Нажмите месяц, чтобы увидеть его записи и оригиналы. «—» означает отсутствие известного значения.</p>`)+
    card(`<div class="card-header"><span class="eyebrow">${selected?e(selected.month):'Всего за сезон'}</span>${selected?link('Весь сезон',harvestRoute(f,{month:null}),'section-link'):''}</div><strong class="harvest-total exact-value">${e(summary.coverage?.total===0?'Нет записей':harvestNumber(summary.value))}</strong><span class="label">${e(unit)}</span><p class="note">${e(coverageLabel(summary.coverage))}</p>${scaled(summary.estimated_value)>0n?`<p class="harvest-estimated">В том числе оценка: ${e(harvestNumber(summary.estimated_value))} ${e(unit)} · по подтверждённому основанию</p>`:''}${scaled(summary.unallocated_value)>0n?`<p class="note">${greenhouse?'Нераспределённый остаток документов в выборке':'Без распределения по теплицам'}: ${e(harvestNumber(summary.unallocated_value))} ${e(unit)}${greenhouse?' · не включён в итог теплицы':''}</p>`:''}<p class="note">${f.metric==='branches'?'Количество веток из полевых отчётов. ADET депо — отдельное исходное значение.':f.metric==='kg'?'Подтверждённый вес документов депо. Полевой вес не подставляется.':'Подтверждённая выручка. Получение денег учитывается отдельно.'}</p>`)+
    card(`<div class="card-header"><h2>${selected?'Записи месяца':'История сезона'}</h2><span class="tag">${events.length}</span></div>${eventRows(events,unit)}`)+
    (!greenhouse?card(`<h2>По теплицам</h2>${(result.greenhouses||[]).map(g=>link(`<strong>${e(g.name)}</strong><small>Месяцы, срезы и документы ›</small>`,harvestRoute(f,{greenhouse_id:g.id}),'list-row')).join('')}`):'');
}
