import {escape as e} from './core.js';

// One registry owns every working owner destination. No future-module stubs.
export const routes=Object.freeze({
  home:{label:'Главная',group:'home',icon:'home'},
  review:{label:'Проверка',group:'review',icon:'check'},
  overview:{label:'Финансы',group:'overview',icon:'wallet'},
  more:{label:'Ещё',group:'more',icon:'grid'},
  debts:{label:'Долги',group:'overview',description:'Обязательства и авансы',icon:'wallet'},
  operations:{label:'Операции',group:'overview',description:'Черновики, движения и история',icon:'activity'},
  harvest:{label:'Урожай / Депо',group:'more',description:'Сезон, срез и расчёты депо',icon:'leaf'},
  inventory:{label:'Удобрения / Запасы',group:'more',description:'Остатки, приходы и внесения',icon:'box'},
  tasks:{label:'Работники / Задачи',group:'more',description:'Поручения, отчёты и исключения',icon:'people'},
  inbox:{label:'Входящие',group:'more',description:'Ранее сохранённые документы и черновики',icon:'inbox'},
  parties:{label:'Контрагенты',group:'more',description:'Поставщики, клиенты и депо',icon:'people'},
  personal_tasks:{label:'Личные задачи',group:'more',description:'Активные дела и история выполнения',icon:'check'}
});
export const primaryRoutes=Object.freeze(['home','review','overview','more']);
export const financeRoutes=Object.freeze(['overview','debts','operations']);
export const moreRoutes=Object.freeze(['harvest','inventory','tasks','inbox','parties','personal_tasks']);
const paths={
  home:'M3 10 12 3l9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1Z',
  check:'M9 4H6a2 2 0 0 0-2 2v14h16V6a2 2 0 0 0-2-2h-3M9 3h6v4H9ZM8 13l3 3 5-6',
  wallet:'M20 7H5a2 2 0 0 1 0-4h13v4M4 5v14a2 2 0 0 0 2 2h14V7M15 11h7v6h-7ZM18 14h.01',
  grid:'M3 3h7v7H3ZM14 3h7v7h-7ZM3 14h7v7H3ZM14 14h7v7h-7Z',
  activity:'M3 12h4l3-8 4 16 3-8h4',
  leaf:'M20 3C8 2 3 8 6 15s13 5 14-12ZM4 21l12-13',
  box:'m12 3 9 5-9 5-9-5Zm-9 5v10l9 5 9-5V8M12 13v10M7 5l9 5',
  people:'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M16 4a4 4 0 0 1 0 8M22 21v-2a4 4 0 0 0-3-4M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0',
  inbox:'M4 3h16v18H4ZM4 14h5l1 3h4l1-3h5M8 7h8M8 10h6'
};
export const icon=name=>`<svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${paths[name]||paths.grid}"/></svg>`;
export function resolveRoute(hash=''){const key=hash.replace(/^#/,'');return Object.hasOwn(routes,key)?key:'home';}
export function primaryNavigation(current,count=0){
  const group=routes[current]?.group||'home';
  return primaryRoutes.map(key=>`<button data-page="${key}" class="${key===group?'active':''}" ${key===group?'aria-current="page"':''}>${icon(routes[key].icon)}<span>${routes[key].label}</span>${key==='review'&&count>0?`<span class="nav-count" aria-label="${e(count)} ждут проверки">${e(count>99?'99+':count)}</span>`:''}</button>`).join('');
}
export function financeNavigation(current){return `<div class="section-tabs" aria-label="Разделы финансов">${financeRoutes.map(key=>`<button data-go="${key}" class="${key===current?'active':''}" aria-pressed="${key===current}">${key==='overview'?'Обзор':routes[key].label}</button>`).join('')}</div>`;}
export function moreNavigation(){return `<div class="destination-grid">${moreRoutes.map(key=>`<button class="destination-card" data-go="${key}"><span class="destination-icon">${icon(routes[key].icon)}</span><span><strong>${routes[key].label}</strong><small>${routes[key].description}</small></span><span class="destination-arrow" aria-hidden="true">→</span></button>`).join('')}</div>`;}
