import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {taskAudiencePayload,canClarifyTaskAudience,taskAudienceCommand,taskAudienceFields,taskAudienceSummary,taskPreparationDetails,workerSelectionPayload,proposalCard} from '../../src/greenhouse_owner_os/web/owner-r2.js';
import {Header,e,link} from '../../prototype/owner-r3/components.js';
const r2Source=fs.readFileSync(new URL('../../src/greenhouse_owner_os/web/owner-r2.js',import.meta.url),'utf8');
const r3Source=fs.readFileSync(new URL('../../prototype/owner-r3/connected.js',import.meta.url),'utf8');
const extract=(s,a,b)=>s.slice(s.indexOf(a),s.indexOf(b,s.indexOf(a)));
const candidate={title:'Проверить насос',original_instruction:'  Завтра проверить насос\nТеплица Восточная  ',category:'maintenance',planned_date:null,relative_time_context:'завтра',greenhouse_id:'greenhouse',worker_id:null,language:null,rendered_text:null,note:'Исходная заметка'};
const ambiguous=()=>({id:'origin',version:2,action_type:'UNRESOLVED',state:'NEEDS_INPUT',source:{id:'source',text:'  <Исходный текст>\nБез изменений  ',has_file:false},revision:{payload:{note:'Завтра проверить насос',task_candidates:structuredClone(candidate)},missing:['Для себя или работнику?'],basis:{},provenance:{task_candidates:{fields:{original_instruction:{category:'SOURCE'},planned_date:{category:'UNKNOWN'}}}}}});
const data={owner:{finance:{work_areas:[{id:'greenhouse',name:'Восточная',kind:'greenhouse'}]}},tasks:{workers:[]}};
const deferred=()=>{let resolve,reject;return {promise:new Promise((a,b)=>{resolve=a;reject=b;}),resolve,reject};};

test('ambiguous task has required blank audience without a personal fallback',()=>{
 const html=taskAudienceFields();assert.match(html,/<select name="audience" required\s*><option value="">Выберите явно<\/option>/);assert.match(html,/value="personal"\s*>Для себя/);assert.match(html,/value="worker"\s*>Работнику/);assert.doesNotMatch(html,/selected|назначить|отправить/i);
 for(const audience of ['',undefined,'PERSONAL_TASK','all'])assert.throws(()=>taskAudienceCommand(ambiguous(),audience),/Выберите/);
 for(const audience of ['personal','worker'])assert.deepEqual(taskAudienceCommand(ambiguous(),audience),{origin_proposal_id:'origin',expected_version:2,audience,reason:'Владелец явно уточнил адресата задачи'});
});

test('known task facts, relative date and full escaped original survive clarification presentation',()=>{
 const p=ambiguous(),before=structuredClone(p),html=taskAudienceSummary(p,data);
 assert.ok(html.includes(e(candidate.original_instruction)));assert.match(html,/Восточная/);assert.match(html,/завтра/);assert.doesNotMatch(html,/value="2026-|Дата.*2026-/);assert.deepEqual(p,before);
 assert.ok(proposalCard(p,data).includes('Завтра проверить насос'));
 const long='Полная инструкция '.repeat(300);p.revision.payload.task_candidates.original_instruction=long;assert.ok(taskAudienceSummary(p,data).includes(long));
});

test('explicit personal remains personal and confirmed or linked tasks cannot create another audience effect',()=>{
 const personal={...ambiguous(),action_type:'PERSONAL_TASK',revision:{payload:{...candidate},missing:[]}};
 assert.equal(taskAudiencePayload(personal),personal.revision.payload);assert.equal(canClarifyTaskAudience(personal),true);assert.equal(personal.action_type,'PERSONAL_TASK');
 for(const state of ['POSTED','CONFIRMED_NONPOSTING','REJECTED','FAILED','PREPARING']){const p={...personal,state};assert.equal(canClarifyTaskAudience(p),false);assert.throws(()=>taskAudienceCommand(p,'worker'));}
 const linked={...personal,recovery:{recovery_proposal_id:'target'}};assert.equal(canClarifyTaskAudience(linked),false);assert.throws(()=>taskAudienceCommand(linked,'worker'));
 assert.equal(taskAudiencePayload({...ambiguous(),revision:{payload:{money_candidates:{amount:'500'}}}}),undefined);
});

test('missing performer stays unset; only explicit current worker derives language and resets translation',()=>{
 const before={...candidate,worker_id:'old',language:'ru',rendered_text:'Old translated text'},workers=[{id:'new',language:'fa',active:true},{id:'off',language:'tr',active:false}];
 const result=workerSelectionPayload(before,'new',workers);assert.equal(result.worker_id,'new');assert.equal(result.language,'fa');assert.equal(result.rendered_text,null);assert.equal(result.original_instruction,before.original_instruction);assert.equal(before.rendered_text,'Old translated text');
 assert.equal(workerSelectionPayload({...before,worker_id:'new'},'new',workers).rendered_text,null,'Changed reference language invalidates an old translation even for the same worker');
 assert.equal(workerSelectionPayload(before,null,workers).worker_id,null);assert.throws(()=>workerSelectionPayload(before,'off',workers));assert.throws(()=>workerSelectionPayload(before,'unknown',workers));
 const fn=new Function(extract(r3Source,'function nextMissingFact(', 'function moneyIntentReview(')+'return nextMissingFact;')();
 assert.equal(fn({action_type:'WORKER_TASK',revision:{missing:['Работник'],payload:{worker_id:null}}}),'worker_id');assert.equal(fn({action_type:'PERSONAL_TASK',revision:{missing:[],payload:{worker_id:null}}}),null);
});

function r3Submit(overrides={}){
 const writes=[],finishes=[],restored=[],saves=[],draft={id:'intent-draft'},api={scope:'owner',generation:1,draft:async(route,value)=>saves.push({route,value:structuredClone(value)}),entityPost:async(path,body,key)=>{writes.push({path,body,key});return{id:'target'};},...overrides.api};
 const deps={api,epoch:1,restoreTaskAudienceReview:(...args)=>restored.push(args),clearDraft:async()=>{},assertSubmissionScope:s=>{if(s.scope!==api.scope||s.generation!==api.generation)throw Error('Session changed');},saveDraft:async()=>{},taskAudienceCommand,finish:async(...args)=>finishes.push(args)};
 const submit=new Function(...Object.keys(deps),extract(r3Source,'async function submitForm(', 'function openSheet(')+'return submitForm;')(...Object.values(deps));
 return{api,writes,finishes,restored,saves,draft,run:(audience='worker')=>submit({dataset:{form:'task-audience'}},{scope:'owner',generation:1,draft,current:ambiguous(),route:'review/origin',fields:{audience}})};
}
test('actual R3 submission prepares a source-linked target only then opens existing review',async()=>{
 const h=r3Submit();await h.run();assert.equal(h.writes.length,1);assert.equal(h.writes[0].path,'/owner/captures/source/task-recovery');assert.equal(h.writes[0].body.audience,'worker');assert.equal(h.writes[0].key,'origin:task-audience:2:worker');assert.equal(h.finishes[0][0],'review/target');assert.doesNotMatch(h.writes[0].path,/confirm|assign|dispatch/);
 const empty=r3Submit();await assert.rejects(empty.run(''),/Выберите/);assert.equal(empty.writes.length,0);
});
test('R3 stale or expired response never navigates or confirms a target',async()=>{
 const rejection=r3Submit({api:{entityPost:async()=>{throw Object.assign(Error('Conflict'),{status:409});}}});await assert.rejects(rejection.run(),/Conflict/);assert.deepEqual(rejection.finishes,[]);
 const gate=deferred(),late=r3Submit({api:{entityPost:()=>gate.promise}}),run=late.run();late.api.generation++;gate.resolve({id:'private-old-target'});await assert.rejects(run,/Session changed/);assert.deepEqual(late.finishes,[]);
});

test('actual R3 ambiguous and previously linked views show original without expense or confirm controls',()=>{
 const form=(name,body,label)=>`<form data-form="${name}">${body}<button>${label}</button></form>`;
 const deps={Header,e,link,draft:null,btn:(label)=>label,data:{home:data.owner},panel:x=>x,entityPath:x=>'/v1/entities/e'+x,taskAudienceSummary,canClarifyTaskAudience,taskAudienceFields,form,notice:x=>x};
 const view=new Function(...Object.keys(deps),extract(r3Source,'function taskAudienceReview(', 'function proposalDetail(')+'return taskAudienceReview;')(...Object.values(deps));
 let html=view(ambiguous());assert.match(html,/data-form="task-audience"/);assert.ok(html.includes(e(ambiguous().source.text)));assert.doesNotMatch(html,/confirm-proposal|recover:|direct-expense|Назначить|Отправить/);
 html=view({...ambiguous(),recovery:{recovery_proposal_id:'target'}});assert.match(html,/#review\/target/);assert.doesNotMatch(html,/data-form="task-audience"/);
});

function r2Clarification({handler}={}){
 const calls=[],reviews=[],notices=[],form={elements:{audience:{value:'',disabled:false}},querySelector:()=>button},button={disabled:false,textContent:'Открыть проверку'},status={textContent:''},refresh={disabled:false};let live=true,html='';
 const nodes={'#r2-task-audience-form':form,'#r2-task-audience-status':status,'#r2-task-audience-refresh':refresh};
 const deps={begin:()=>({scope:'owner',root:'/v1/entities/e/owner'}),active:()=>live,canClarifyTaskAudience,taskAudienceSummary,taskAudienceFields,taskAudienceCommand,getData:()=>data,e,show:(_t,_title,markup)=>{html=markup;return true;},$:id=>nodes[id],guard:fn=>fn,api:async(path,body,options)=>{calls.push({path,body:structuredClone(body),options});return handler?handler(path,body):{id:'target'};},reload:async()=>{},review:async id=>reviews.push(id)};
 const open=new Function(...Object.keys(deps),extract(r2Source,'  async function clarifyTaskAudience(', '  async function review(')+'return clarifyTaskAudience;')(...Object.values(deps));
 return{calls,reviews,notices,form,button,status,refresh,open:()=>open(ambiguous()),html:()=>html,leave:()=>live=false,submit:()=>form.onsubmit({preventDefault(){}})};
}
test('R2 explicit audience opens only returned review and ignores double submit',async()=>{
 const gate=deferred(),h=r2Clarification({handler:()=>gate.promise});await h.open();assert.match(h.html(),/r2-task-audience-form/);assert.equal(h.form.elements.audience.value,'');h.form.elements.audience.value='worker';const run=h.submit();await h.submit();assert.equal(h.calls.length,1);assert.equal(h.calls[0].body.audience,'worker');assert.equal(h.form.elements.audience.disabled,true);gate.resolve({id:'worker-review'});await run;assert.deepEqual(h.reviews,['worker-review']);assert.ok(h.calls[0].options.isCurrent());
});
test('R2 uncertain reply keeps original audience immutable and same command on retry',async()=>{
 let count=0;const h=r2Clarification({handler:async()=>{if(++count===1)throw Object.assign(Error('Lost response'),{status:0});return{id:'target'};}});await h.open();h.form.elements.audience.value='worker';await h.submit();assert.equal(h.form.elements.audience.disabled,true);assert.match(h.button.textContent,/Повторить/);h.form.elements.audience.value='personal';await h.submit();assert.equal(h.calls.length,2);assert.deepEqual(h.calls[0].body,h.calls[1].body);assert.deepEqual(h.reviews,['target']);
});
test('R2 stale rejection permits explicit refresh and late old-owner target is fenced',async()=>{
 const rejected=r2Clarification({handler:async()=>{throw Object.assign(Error('Stale'),{status:409});}});await rejected.open();rejected.form.elements.audience.value='worker';await rejected.submit();assert.equal(rejected.form.elements.audience.disabled,false);await rejected.refresh.onclick();assert.deepEqual(rejected.reviews,['origin']);
 const gate=deferred(),late=r2Clarification({handler:()=>gate.promise});await late.open();late.form.elements.audience.value='personal';const run=late.submit();late.leave();gate.resolve({id:'private-target'});await run;assert.deepEqual(late.reviews,[]);assert.equal(late.calls[0].options.isCurrent(),false);
});


test('source-linked task target reaches ordinary review, not a self-link clarification loop',()=>{
 const firstBranch=extract(r3Source,'function proposalDetail(p) {',"  if(p.action_type==='UNRESOLVED' && p.revision.payload.money_candidates");
 const choose=new Function('draft','taskAudienceReview',firstBranch+"return 'ordinary';}return proposalDetail;")(null,()=> 'audience');
 const target={...ambiguous(),id:'target',action_type:'WORKER_TASK',recovery:{origin_proposal_id:'origin',recovery_proposal_id:'target'},revision:{payload:{...candidate},missing:['Работник']}};
 assert.equal(choose(target),'ordinary');assert.equal(choose({...target,id:'origin'}),'audience');
});

test('task provenance and source-linked original clarification remain readable on target review',()=>{
 const p=ambiguous();assert.match(taskPreparationDetails(p),/Формулировка: Из источника/);assert.match(taskPreparationDetails(p),/Дата: Не установлено/);
 const target={...p,action_type:'WORKER_TASK',revision:{...p.revision,payload:{...candidate},provenance:{worker_id:{category:'OWNER INPUT'},planned_date:{category:'SOURCE FACT'}}},recovery_origin_context:{version:2,note:'  Сохранённое <уточнение>  '}};
 const html=taskPreparationDetails(target);assert.match(html,/Работник: Ввод владельца/);assert.match(html,/Дата: Из источника/);assert.ok(html.includes('  Сохранённое &lt;уточнение&gt;  '));assert.equal(target.recovery_origin_context.version,2);
});


test('R3 uncertain task audience retries the encrypted saved original despite changed visible fields',async()=>{
 let attempts=0;const calls=[];const h=r3Submit({api:{entityPost:async(path,body,key)=>{calls.push({path,body:structuredClone(body),key});if(++attempts===1)throw Object.assign(Error('Lost reply'),{status:0});return{id:'target'};}}});
 await assert.rejects(h.run('worker'),/Lost reply/);assert.equal(h.saves.length,1);assert.equal(h.draft.task_audience_submission.body.audience,'worker');assert.equal(h.restored.length,1);assert.deepEqual(h.finishes,[]);
 await h.run('personal');assert.equal(calls.length,2);assert.deepEqual(calls[0],calls[1]);assert.equal(h.finishes[0][0],'review/target');
 const html=taskAudienceFields(h.draft.task_audience_submission.body.audience);assert.match(html,/<select[^>]+disabled/);assert.match(html,/value="worker" selected/);assert.doesNotMatch(html,/value="personal" selected/);assert.match(html,/Повтор сохранит этот же выбор/);
});

test('R3 known rejection clears pending lock but cannot silently update expected version',async()=>{
 const h=r3Submit({api:{entityPost:async()=>{throw Object.assign(Error('Conflict'),{status:409});}}});await assert.rejects(h.run(),/Conflict/);assert.equal(h.draft.task_audience_submission,undefined);assert.equal(h.saves.length,2);assert.equal(h.restored.length,1);assert.deepEqual(h.finishes,[]);
 assert.equal(h.restored[0][0].current.version,2);
});

test('R3 pending review restoration is fenced by route, render epoch and live participant',()=>{
 const code=extract(r3Source,'function restoreTaskAudienceReview(', 'async function submitForm(');
 for(const boundary of ['valid','route','epoch','scope','generation','not-ready']){
  const state={ready:boundary!=='not-ready',route:boundary==='route'?'home':'review/origin',epoch:boundary==='epoch'?2:1,scope:boundary==='scope'?'other':'owner',generation:boundary==='generation'?2:1},renders=[],main={dataset:{}};
  const factory=new Function('state','renders','main',`let ready=state.ready,route=state.route,epoch=state.epoch,draft=null,current=null;const api={scope:state.scope,generation:state.generation},shell=value=>renders.push(value),taskAudienceReview=p=>'audience:'+p.id,document={querySelector:()=>main},showError=()=>{};${code};return restoreTaskAudienceReview;`);
  factory(state,renders,main)({scope:'owner',generation:1,route:'review/origin',draft:{task_audience_submission:{body:{audience:'worker'}}},current:{id:'origin'}},1,Error('Lost reply'));
  assert.deepEqual(renders,boundary==='valid'?['audience:origin']:[],boundary);assert.equal(main.dataset.renderedRoute,boundary==='valid'?'review/origin':undefined);
 }
});

test('R3 review loads encrypted task draft and pending known-task intent hides old confirmation controls',()=>{
 assert.match(r3Source,/canClarifyTaskAudience\(current\)\)\{\s*const saved=await getDraft\(route\);if\(token!==epoch\)return;draft=saved;/);
 const branch=extract(r3Source,'function proposalDetail(p) {',"  if(p.action_type==='UNRESOLVED' && p.revision.payload.money_candidates");
 const choose=new Function('draft','taskAudienceReview',branch+"return 'ordinary';}return proposalDetail;")({task_audience_submission:{body:{audience:'worker'}}},()=> 'locked-audience');
 assert.equal(choose({...ambiguous(),action_type:'PERSONAL_TASK'}),'locked-audience');
});

test('actual source history labels task and finance recovery neutrally while retaining target links and original',async()=>{
 const rendered=[];
 const deps={begin:()=>({scope:'owner',root:'/owner'}),api:async()=>({items:[{id:'source',text:'Исходная запись',captured_at:'2026-10-12',state:'NEEDS_REVIEW',proposals:[{id:'task-origin',action_type:'UNRESOLVED',state:'NEEDS_INPUT',recovery_proposal_id:'task-target'},{id:'expense-origin',action_type:'UNRESOLVED',state:'NEEDS_INPUT',recovery_proposal_id:'expense-target'},{id:'ordinary',state:'READY'}]}]}),show:(_t,_title,html)=>{rendered.push(html);return false;},e,displayDate:value=>value,statuses:{READY:'Готово к подтверждению'}};
 const sources=new Function(...Object.keys(deps),extract(r2Source,'  async function sources(', '  function sync(')+'return sources;')(...Object.values(deps));
 await sources();const html=rendered[0];
 assert.equal(html.match(/Открыть связанную проверку/g).length,2);assert.doesNotMatch(html,/Открыть связанный расход/);
 assert.match(html,/data-source-proposal="task-target"/);assert.match(html,/data-source-proposal="expense-target"/);assert.match(html,/data-source-proposal="ordinary"/);assert.match(html,/Исходная запись/);assert.match(html,/captures\/source\/original/);
});


test('ambiguous queue card shows retained nested known date and greenhouse with original provenance',()=>{
 const p=ambiguous();p.revision.payload.task_candidates.planned_date='2026-10-12';p.revision.payload.task_candidates.relative_time_context='сегодня';p.revision.payload.task_candidates.category='irrigation';
 p.revision.provenance.task_candidates.fields.planned_date={category:'SOURCE FACT'};p.revision.provenance.task_candidates.fields.greenhouse_id={category:'INFERENCE'};
 const before=structuredClone(p),html=proposalCard(p,data);
 assert.match(html,/12 окт\. 2026/);assert.match(html,/Восточная/);assert.match(html,/Полив/);assert.match(html,/В источнике: сегодня/);
 assert.match(html,/Дата: Из источника/);assert.match(html,/Теплица: Предположение подготовки/);
 assert.doesNotMatch(html,/Дата не выбрана|id="r2-confirm"|data-effect="PERSONAL_TASK"/);assert.match(html,/Для себя или работнику\?/);assert.match(html,/data-effect="UNRESOLVED"/);assert.deepEqual(p,before);
});

test('ambiguous queue card keeps relative or unknown date honest without a current-day fallback',()=>{
 const p=ambiguous();p.revision.payload.task_candidates.relative_time_context='следующая неделя';
 const relative=proposalCard(p,data);assert.match(relative,/следующая неделя/);assert.match(relative,/точный день не выбран/);assert.doesNotMatch(relative,/2026|Дата не выбрана/);
 p.revision.payload.task_candidates.relative_time_context=null;p.revision.payload.task_candidates.greenhouse_id=null;
 const unknown=proposalCard(p,data);assert.match(unknown,/Дата не выбрана/);assert.doesNotMatch(unknown.match(/<p class="queue-card-context">([^<]*)<\/p>/)[1],/Восточная|2026/);assert.match(unknown,/Для себя или работнику\?/);
});
