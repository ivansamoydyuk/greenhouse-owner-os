import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {daySource,dayStored,dayAllocations,dayLabels} from '../../src/greenhouse_owner_os/web/day-workers-core.js';
import {workerDayForm,workerDayDetail,workerDayList} from '../../src/greenhouse_owner_os/web/worker-day-workers.js';
import {dayRequestView,dayDecisionView,dayActualView,dayActualFormView,dayAccrualView,dayAccrualPreview,dayScopesView} from '../../prototype/owner-r3/day-workers-connected.js';
const id='10000000-0000-0000-0000-000000000001',gid='10000000-0000-0000-0000-000000000002',other='10000000-0000-0000-0000-000000000003';
const draft={id,captured_at:'2026-10-12T10:00:00Z'};
const request={id,greenhouse_id:gid,greenhouse_name:'Теплица 1',worker_name:'Асыф',version:1,state:'PENDING',original:{...draft,fields:{people:'5',days:'2',work:'Листья',start_date:'',note:''}},original_plan:{people:5,days:2,work:'Листья',start_date:null,note:''},current_plan:{people:5,days:2,work:'Листья',start_date:null,note:''},requested_people_days:10,planned_people_days:null,actual_people_days:0,unresolved_day_actual_ids:[],actuals:[],decisions:[],clarifications:[]};
const actual={id,greenhouse_id:gid,greenhouse_name:'Теплица 1',event_date:'2026-10-12',people:4,work:'Листья',full_day:'true',unallocated_people:0,current:true,allocations:[{greenhouse_id:gid,greenhouse_name:'Теплица 1',people:2},{greenhouse_id:other,greenhouse_name:'Теплица 2',people:2}],original:{fields:{people:'4',work:'Листья',event_date:'2026-10-12',full_day:'true',note:'',allocations:'[]'}}};
const refs={greenhouses:[{id:gid,name:'Теплица 1'},{id:other,name:'Теплица 2'}],references:{workers:[{id,name:'Работник'}],counterparties:[{id,name:'Старший'}],expense_accounts:[{id,name:'Дневные рабочие',currency:'TRY'}],work_areas:[{id:'part',name:'Верхняя часть',parent_id:gid}],scopes:[]}};
test('original three fields remain exact and unknown date never becomes today',()=>{const value=daySource('day_request',draft,{people:'05',days:'2',work:'  کار  ',note:'<text>'});assert.deepEqual(value.fields,{people:'05',days:'2',work:'  کار  ',start_date:'',note:'<text>'});assert.equal(value.timezone,'Europe/Istanbul');assert.equal(value.form_id,id);});
test('source ids detect stored request, decision and actual without mutable UUID guessing',()=>{assert.equal(dayStored({decisions:[{id}]},id),true);assert.equal(dayStored({actuals:[{original:{form_id:id}}]},id),true);assert.equal(dayStored({id:other},id),false);});
test('worker request has exactly three primary fields and optional undated detail',()=>{const html=workerDayForm('request',{greenhouse_name:'Теплица'},draft,'fa');const main=html.match(/class="day-primary-fields">([\s\S]*?)<\/div>/)[1];assert.equal((main.match(/name="/g)||[]).length,3);for(const field of ['people','days','work'])assert.match(main,new RegExp(`name="${field}"`));assert.match(html,/name="start_date" value=""/);assert.match(html,/زبان مادری/);assert.doesNotMatch(main,/greenhouse_id|2500|currency/);});
test('explicit allocations count one crew and preserve a reliable part',()=>{assert.deepEqual(dayAllocations({people:'4',allocation_mode:'split',['allocation_'+gid]:'2',['allocation_'+other]:'2',['part_id_'+gid]:'part'},gid,refs.greenhouses,refs.references.work_areas),[{greenhouse_id:gid,work_area_id:'part',people:2},{greenhouse_id:other,work_area_id:null,people:2}]);assert.deepEqual(dayAllocations({people:'4',allocation_mode:'unknown'},gid,refs.greenhouses),[]);assert.throws(()=>dayAllocations({people:'4',allocation_mode:'split',['allocation_'+gid]:'4',['allocation_'+other]:'4'},gid,refs.greenhouses));assert.throws(()=>dayAllocations({people:'4',allocation_mode:'same',['part_id_'+gid]:'other-part'},gid,refs.greenhouses,refs.references.work_areas));});
test('owner request distinguishes requested10 from unapproved plan, actual7 and incomplete days',()=>{const html=dayRequestView({...request,actual_people_days:7,unresolved_day_actual_ids:['unknown']});assert.match(html,/Запрошено, человеко-дней/);assert.match(html,/Ещё не одобрено/);assert.match(html,/Известно фактически/);assert.match(html,/неполной или неизвестной/);assert.match(html,/>7</);});
test('approved plan stays editable without inviting second original approval',()=>{const html=dayRequestView({...request,state:'APPROVED',planned_people_days:10});assert.match(html,/day-decision\/.+\/REVISE_APPROVE/);assert.doesNotMatch(html,/\/APPROVE"/);});
test('stale owner decision requires explicit visible current-review acknowledgement',()=>{const html=dayDecisionView(request,'APPROVE',{...draft,version_conflict:true});assert.match(html,/Заявка изменилась/);assert.match(html,/name="reviewed_current" required/);assert.match(html,/Текущая работа/);});
test('actual form date and full-day/allocation evidence are not invented',()=>{const html=dayActualFormView(draft,refs);assert.match(html,/name="event_date" type="date" value=""/);assert.match(html,/name="full_day"/);assert.match(html,/name="allocation_mode"/);assert.match(html,/Верхняя часть/);assert.match(html,/Без плановой заявки/);});
test('accrual has no default rate and exact total review across splitcrew',()=>{const empty=dayAccrualView(actual,refs,draft);assert.match(empty,/name="daily_rate" type="text" value=""/);assert.match(empty,/Сумма начисления пока неизвестна/);assert.doesNotMatch(empty,/2500/);const preview=dayAccrualPreview(actual,refs,{daily_rate:'100.15',currency:'TRY',counterparty_id:id,expense_account_id:id});assert.match(preview,/400,60 TRY/);assert.equal((preview.match(/200,30 TRY/g)||[]).length,2);assert.match(preview,/Сейчас выплачено: 0/);});
test('partial/unknown and replaced actuals block accrual independently',()=>{for(const delta of [{full_day:'false'},{full_day:'unknown'},{current:false},{unallocated_people:1}]){const html=dayAccrualView({...actual,...delta},refs,draft);assert.match(html,/Начисление заблокировано/);assert.doesNotMatch(html,/data-form="day-accrual"/);}});
test('reversed debt cannot masquerade as live successful accrual',()=>{const html=dayActualView({...actual,accrual:{obligation_id:id,obligation_status:'reversed',correction_required:true,currency:'TRY',original_amount:'400.00'}});assert.match(html,/Связанное начисление отменено/);assert.match(html,/money-document/);assert.doesNotMatch(html,/href="#day-accrual\//);});
test('worker views do not serialize owner-only financial data even if DTO grows',()=>{const secret={daily_rate:'9182.45',counterparty_name:'Private creditor',currency:'SECRET',amount:'73628.18',obligation_id:'private-obligation',rate_basis:'private rate'};const item={...actual,accrual:secret};for(const html of [workerDayList({...refs,requests:[request],actuals:[item]},'fa'),workerDayDetail({...request,actuals:[item],accrual:secret},'fa')])for(const value of Object.values(secret))assert.ok(!html.includes(value));});
test('names and originals are escaped in both audiences',()=>{const bad='<img src=x onerror=alert(1)>',html=dayRequestView({...request,greenhouse_name:bad,current_plan:{...request.current_plan,work:bad}})+workerDayDetail({...request,current_plan:{...request.current_plan,work:bad}},'fa');assert.doesNotMatch(html,/<img/);assert.match(html,/&lt;img/);});
test('no verified worker scope gives explanatory no-action state',()=>{const html=workerDayList({greenhouses:[],requests:[],actuals:[]},'fa');assert.match(html,new RegExp(dayLabels.fa.noScope));assert.doesNotMatch(html,/data-day-action="new"|data-day-action="actual"/);});
test('owner scope is explicit, worker has no arbitrary greenhouse input',()=>{assert.match(dayScopesView(refs,draft),/data-form="day-scope"/);assert.doesNotMatch(workerDayForm('request',{greenhouse_name:'A'},draft,'fa'),/name="greenhouse_id"/);});
test('integration preserves encrypted drafts, immutable retries and generation fence',async()=>{const s=await fs.readFile(new URL('../../src/greenhouse_owner_os/web/worker.js',import.meta.url),'utf8');assert.match(s,/EncryptedWorkerDraftStore/);assert.match(s,/stableWorkerIntent\(operationStore,key\+':pending'/);assert.match(s,/stamp!==generation\|\|operationStore!==workerStore/);assert.match(s,/err.status===409&&kind==='clarification'/);const owner=await fs.readFile(new URL('../../prototype/owner-r3/connected.js',import.meta.url),'utf8');assert.match(owner,/draft\?\.day_submission&&!draft\?\.version_conflict/);assert.match(owner,/current.request.original_plan\|\|current.request.current_plan/);});

test('freshly reviewed stale decision uses visible edits; uncertain replay keeps frozen values',async()=>{
 const source=await fs.readFile(new URL('../../prototype/owner-r3/connected.js',import.meta.url),'utf8');
 const expression=source.match(/v = (draft\?\.day_submission[\s\S]*?),\n    savedRoute = route;/)?.[1];assert.ok(expression,'actual submission field selection remains testable');
 const choose=new Function('draft','snapshot','return '+expression);
 const pending={day_submission:{body:{source:{fields:{decision:'REVISE_APPROVE',people:'5',work:'Old'}},confirmed:true}}};
 const snapshot={fields:{decision:'REVISE_APPROVE',people:'4',work:'Reviewed current',reviewed_current:'on'}};
 assert.equal(choose(pending,snapshot).people,'5');assert.equal(choose(pending,snapshot).work,'Old');
 assert.equal(choose({...pending,version_conflict:true},snapshot).people,'4');assert.equal(choose({...pending,version_conflict:true},snapshot).work,'Reviewed current');
});

test('explicit no-work zero has no allocation or accrual and remains distinct from unknown',()=>{
 assert.deepEqual(dayAllocations({people:'0',allocation_mode:'same'},gid,refs.greenhouses),[]);
 assert.deepEqual(dayAllocations({people:'0'},gid,refs.greenhouses),[]);
 assert.throws(()=>dayAllocations({people:'',allocation_mode:'same'},gid,refs.greenhouses));
 assert.doesNotMatch(dayAccrualView({...actual,people:0,allocations:[]},refs,draft),/data-form="day-accrual"/);
 assert.equal(daySource('day_actual',draft,{people:'0',allocations:'[]'}).fields.people,'0');
});

import {dayWorkChoices} from '../../src/greenhouse_owner_os/web/day-workers-core.js';
test('work suggestions reuse only short accessible same-greenhouse text and preserve free custom work',()=>{
 const choices=dayWorkChoices([{greenhouse_id:gid,current_plan:{work:'  کار اصلی  '}},{greenhouse_id:other,current_plan:{work:'Other greenhouse private work'}},{greenhouse_id:gid,current_plan:{work:'  کار اصلی  '}},{greenhouse_id:gid,current_plan:{work:'x'.repeat(121)}},{greenhouse_id:gid,original:{fields:{work:'Листья'}}}],gid);
 assert.deepEqual(choices,['  کار اصلی  ','Листья']);
 const html=workerDayForm('request',{greenhouse_name:'Теплица',work_choices:choices},{...draft,work:'Моя новая работа <своими словами>'},'fa');
 assert.match(html,/list="worker-day-work-choices"/);assert.match(html,/<option value="  کار اصلی  ">/);assert.doesNotMatch(html,/Other greenhouse private work/);
 assert.match(html,/value="Моя новая работа &lt;своими словами&gt;"/);assert.match(html,/یا کار دیگری بنویسید/);
 const main=html.match(/class="day-primary-fields">([\s\S]*?)<\/div>/)[1];assert.equal((main.match(/name="/g)||[]).length,3);
 assert.equal(daySource('day_request',draft,{people:'5',days:'2',work:'Моя новая работа <своими словами>'}).fields.work,'Моя новая работа <своими словами>');
 assert.deepEqual(dayWorkChoices([],gid),[]);
});

test('pre-existing multiline work draft is not silently flattened by suggestion input',()=>{
 const work='Снять листья\nСобрать в проходе';
 assert.deepEqual(dayWorkChoices([{greenhouse_id:gid,current_plan:{work}}],gid),[]);
 const worker=workerDayForm('request',{greenhouse_name:'Теплица'}, {...draft,work},'fa');
 const owner=dayDecisionView({...request,current_plan:{...request.current_plan,work}},'REVISE_APPROVE',{...draft,work});
 for(const html of [worker,owner]){assert.match(html,/<textarea name="work"/);assert.ok(html.includes(work));}
 assert.equal(daySource('day_request',draft,{people:'5',days:'2',work}).fields.work,work);
});
