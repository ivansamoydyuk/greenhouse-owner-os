import test from 'node:test';
import assert from 'node:assert/strict';
import {loadExpenseRecovery,expenseIsReadOnly,expenseEffectText,expenseContext} from '../../src/greenhouse_owner_os/web/owner-expense-recovery.js';
import {proposalSummary} from '../../src/greenhouse_owner_os/web/owner-r2.js';

const root='/v1/entities/entity/owner';
const proposal=()=>({id:'target',action_type:'DIRECT_EXPENSE',state:'NEEDS_INPUT',source:{id:'source'},revision:{payload:{amount:null,currency:null,event_date:null,money_account_id:null,description:null,counterparty_id:null,attributions:[]},missing:['Сумма','Валюта'],provenance:{},basis:{}}});

test('handoff body is only the immutable origin selection; returned draft is never current truth',async()=>{
  const calls=[],current=proposal();current.state='REJECTED';current.version=4;
  const api=async(path,body)=>{calls.push({path,body});return calls.length===1?{id:'target',version:1,state:'NEEDS_INPUT'}:current;};
  assert.equal(await loadExpenseRecovery(api,root,'source',{id:'origin',version:2,revision:{payload:{note:'Do not parse'}}}),current);
  assert.deepEqual(calls,[{path:root+'/captures/source/direct-expense-recovery',body:{origin_proposal_id:'origin',expected_version:2}},{path:root+'/confirmations/target/preview',body:undefined}]);
  assert.equal(expenseIsReadOnly(current),true);
});
test('saved link uses target identity, not the now-retired origin version or a new POST',async()=>{
  const calls=[],current={...proposal(),state:'POSTED',current_effect:{status:'reversed'}};
  const api=async(...args)=>{calls.push(args);return current;};
  assert.equal(await loadExpenseRecovery(api,root,'source',{id:'origin',version:9,recovery:{origin_version:1,recovery_proposal_id:'target'}}),current);
  assert.deepEqual(calls,[[root+'/confirmations/target/preview']]);
  assert.equal(expenseIsReadOnly(current),true);
  assert.match(expenseEffectText(current),/отменён/);
});
for(const mismatch of [{id:'other'},{source:{id:'other'}},{action_type:'PAYMENT'}])test(`fresh target fails closed for mismatched ${Object.keys(mismatch)[0]}`,async()=>{
  let calls=0;const api=async()=>++calls===1?{id:'target'}:{...proposal(),...mismatch};
  await assert.rejects(loadExpenseRecovery(api,root,'source',null),/Связь расхода/);
});
test('cancelled view makes no request; missing source never starts an unlinked operation',async()=>{
  let calls=0;const api=async()=>{calls++;};
  assert.equal(await loadExpenseRecovery(api,root,'source',null,()=>false),null);
  await assert.rejects(loadExpenseRecovery(api,root,null,null),/Источник не найден/);
  assert.equal(calls,0);
});
test('unknown amount/currency/purpose remain explicit and the counterparty stays optional',()=>{
  const p=proposal(),markup=proposalSummary(p,{},false);
  assert.match(markup,/Сумма: Не установлено/);assert.match(markup,/Валюта: Не установлено/);
  assert.match(markup,/За что: Не установлено/);assert.match(markup,/Контрагент: Не указан · необязательно/);
  assert.doesNotMatch(markup,/0,00|>TRY</);
});
test('structured known facts retain server provenance, while full source description is retained context',()=>{
  const p=proposal();Object.assign(p.revision.payload,{amount:'12.30',currency:'TRY',description:'Full raw source'});
  p.revision.provenance={amount:{category:'SOURCE FACT'},currency:{category:'INFERENCE'},description:{category:'SOURCE FACT'}};
  p.expense_context={description_kind:'RETAINED CONTEXT'};
  const before=JSON.stringify(p),markup=expenseContext(p);
  assert.match(markup,/Сумма: Из источника/);assert.match(markup,/Валюта: Предположение подготовки/);
  assert.match(markup,/За что: Сохранённый контекст/);assert.equal(JSON.stringify(p),before);
  p.expense_context.description_kind='OWNER INPUT';p.revision.provenance.description.category='OWNER INPUT';
  assert.match(expenseContext(p),/За что: Ввод владельца/);
});
test('current posted/reversed truth replaces prospective effect text without inferring from history alone',()=>{
  const p=proposal();p.state='POSTED';p.current_effect={status:'reversed'};
  assert.match(proposalSummary(p,{},false),/Расход отменён/);
  assert.doesNotMatch(proposalSummary(p,{},false),/После подтверждения:/);
  p.current_effect.status='posted';assert.match(expenseEffectText(p),/Деньги списаны/);
  p.current_effect=null;assert.equal(expenseEffectText(p),null);
});

test('selected owner-corrected origin note remains separate retained context, with exact origin/version',()=>{
  const p=proposal();p.source.text='RAW original 777 TRY';
  p.recovery_origin_context={proposal_id:'origin',version:2,note:'Owner clarified <purpose>, no amount yet',provenance:{category:'OWNER INPUT'}};
  const markup=expenseContext(p);
  assert.match(markup,/data-origin-proposal="origin"/);assert.match(markup,/исходного предложения · версия 2/);
  assert.match(markup,/Owner clarified &lt;purpose&gt;, no amount yet/);assert.match(markup,/сохранённый контекст, не поля расхода/);
  assert.equal(p.revision.payload.description,null);assert.equal(p.revision.payload.amount,null);assert.equal(p.revision.payload.currency,null);
});

for(const [status,label] of [['posted','Подтверждено'],['reversed','Отменён']])test(`DirectExpense top status shows current ${status} truth, not just historical proposal state`,()=>{
  const p=proposal();p.state='POSTED';p.current_effect={status};const markup=proposalSummary(p,{},false);
  assert.ok(markup.includes(`<small>Расход · ${label}</small>`));
  if(status==='reversed'){assert.doesNotMatch(markup,/<small>Расход · Подтверждено<\/small>/);assert.match(markup,/История подтверждения и оригинал сохранены/);}
});
