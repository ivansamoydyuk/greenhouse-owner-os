import {escape as e} from './core.js';

// The handoff response may be an old idempotency replay. Never edit or confirm
// it directly: resolve the stable target, then fetch its current exact preview.
export async function loadExpenseRecovery(api,root,sourceId,proposal,isActive=()=>true){
  if(!isActive())return null;
  if(!sourceId)throw Error('Источник не найден. Обновите карточку.');
  let target=proposal?.recovery?.recovery_proposal_id;
  if(!target){
    const body=proposal?{origin_proposal_id:proposal.id,expected_version:proposal.version}:{};
    const saved=await api(`${root}/captures/${encodeURIComponent(sourceId)}/direct-expense-recovery`,body);
    if(!isActive())return null;
    target=saved.id;
  }
  const current=await api(`${root}/confirmations/${encodeURIComponent(target)}/preview`);
  if(!isActive())return null;
  if(current.id!==target||current.source?.id!==sourceId||current.action_type!=='DIRECT_EXPENSE')throw Error('Связь расхода с источником не подтверждена. Обновите карточку.');
  return current;
}

export const expenseIsReadOnly=p=>['POSTED','CONFIRMED_NONPOSTING','REJECTED','FAILED'].includes(p.state)||!!p.current_effect;
export function expenseEffectText(p){
  if(p.current_effect?.status==='reversed')return 'Расход отменён. История подтверждения и оригинал сохранены; повторного списания нет.';
  if(p.current_effect?.status==='posted')return 'Расход подтверждён. Деньги списаны с выбранного счёта; долг и аванс не созданы.';
  return null;
}

export function expenseContext(p){
  if(p.action_type!=='DIRECT_EXPENSE')return '';
  const f=p.revision.payload,provenance=p.revision.provenance||{};
  const fields={amount:'Сумма',currency:'Валюта',event_date:'Дата',money_account_id:'Счёт',description:'За что',counterparty_id:'Контрагент'};
  const categories={'SOURCE FACT':'Из источника','OWNER INPUT':'Ввод владельца',UNKNOWN:'Не установлено',INFERENCE:'Предположение подготовки','LEARNED DEFAULT':'Сохранённое правило'};
  const items=Object.entries(fields).map(([key,label])=>{
    const category=key==='description'&&f[key]&&p.expense_context?.description_kind!=='OWNER INPUT'?'Сохранённый контекст, проверьте назначение':f[key]==null?key==='counterparty_id'?'Не указан · необязательно':'Не установлено':categories[provenance[key]?.category]||'Происхождение требует проверки';
    const saved=f[key]!=null&&['amount','currency','event_date','description'].includes(key)?` · ${f[key]}`:'';
    return `<li>${e(label)}: ${e(category)}${e(saved)}</li>`;
  }).join('');
  const origin=p.recovery_origin_context;
  return `<section class="owner-expense-context"><p class="note">Исходный текст сохранён отдельно. Он не означает, что сумма, валюта или назначение распознаны.</p><p>Сохранённые факты карточки и их происхождение:</p><ul>${items}</ul>${origin?`<details data-origin-proposal="${e(origin.proposal_id)}"><summary>Контекст исходного предложения · версия ${e(origin.version)}</summary><p class="source-text">${e(origin.note??'Уточнение не указано')}</p><small>${e(categories[origin.provenance?.category]||'Происхождение требует проверки')} · сохранённый контекст, не поля расхода</small></details>`:''}${p.recovery?`<small>Ручное завершение связано с исходным ${p.recovery.origin_proposal_id?'предложением':'источником'}. Повторный переход открывает этот же расход.</small>`:''}</section>`;
}
