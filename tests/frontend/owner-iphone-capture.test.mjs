import test from 'node:test';
import assert from 'node:assert/strict';
import {ownerR2,proposalCard,proposalSummary,conciseProposalText,workerTranslationIssue} from '../../src/greenhouse_owner_os/web/owner-r2.js';

const tick=()=>new Promise(resolve=>setImmediate(resolve));
const proposal=(payload={},overrides={})=>({id:'proposal',version:7,state:'READY',action_type:'PAYABLE',source:{id:'source',text:'Original evidence'},revision:{payload:{amount:'1250.25',currency:'TRY',counterparty_id:'party',description:'Ремонт',event_date:'2026-10-08',...payload},basis:{},missing:[],preview_digest:'d'.repeat(64)},...overrides});
const ownerData=()=>({preview:{},owner:{owner_id:'owner-a',finance:{entity_name:'TEST',as_of_date:'2026-10-08',balances:{TRY:{receivable:'9007199254740993.12'}},business_balances:{TRY:{operational_payable:'1250.25'}},references:[{id:'party',original_name:'Серкан'},{id:'cash',original_name:'Cash TR'}],work_areas:[],obligations:[]},season_start:'2026-10-01',season_end:'2027-10-01',season:[{currency:'TRY',kg:'600',count_covered_kg:'400',count:15,count_coverage_complete:false,average_bunch_weight:'26.666666666666667',average_price:'28.875'},{currency:'EUR',kg:'20',average_price:'2.5'}],tasks:[1,2,3,4].map(id=>({id:String(id),title:'Задача '+id,version:1,planned_date:'2026-10-'+String(id+8).padStart(2,'0')})),task_count:4,confirmations:{n:2,overdue:1},worker_exceptions:[{title:'Нужна помощь'}]},inventory:{products:[],locations:[]},tasks:{workers:[]}});

// Small deterministic DOM harness: real callbacks execute, reads/API responses can
// finish out of order, and each dialog render has a distinct identity.
function harness({data=ownerData(),items=[proposal()],navigate,online=false,dock=true}={}){
 const previous=new Map(['document','window','navigator','indexedDB','FileReader'].map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 const nodes=new Map(),groups=new Map(),readers=[],requests=[],calls=[],notices=[],records=new Map(),windowEvents=new Map(),documentEvents=new Map();let html='',currentData=data;
 const makeNode=(id='')=>({id,dataset:{},attributes:{},events:{},children:[],open:false,hidden:false,disabled:false,textContent:'',value:'',files:[],firstElementChild:null,elements:{},classList:{toggle(){},remove(){}},setAttribute(key,value){this.attributes[key]=value;},removeAttribute(key){delete this.attributes[key];},focus(){this.focused=true;},addEventListener(key,fn){this.events[key]=fn;},append(child){this.children.push(child);nodes.set('#'+child.id,child);},querySelector(query){if(query==='[type=submit]')return this.submit;if(query==='[data-r2-mode]')return this.buttons?.[0]||null;return null;},querySelectorAll(){return this.buttons||[];},click(){return this.onclick?.()??this.events.click?.();},close(){this.open=false;this.events.close?.();},set innerHTML(value){this._html=value;if(this.id==='owner-capture-bar')this.buttons=[...value.matchAll(/data-r2-mode="([^"]+)"/g)].map(m=>{const b=makeNode();b.dataset.r2Mode=m[1];return b;});},get innerHTML(){return this._html||'';}});
 for(const id of ['sheet','sheet-content','owner-queue','owner-local','owner-sources','owner-pending-state','owner-advanced'])nodes.set('#'+id,makeNode(id));
 if(dock)nodes.set('#owner-dock',makeNode('owner-dock'));
 const body=makeNode('body');
 globalThis.document={querySelector:s=>nodes.get(s)||null,querySelectorAll:s=>groups.get(s)||[],addEventListener:(key,fn)=>documentEvents.set(key,fn),body,createElement:()=>makeNode()};
 globalThis.window={addEventListener:(key,fn)=>windowEvents.set(key,fn),confirm:()=>true};
 Object.defineProperty(globalThis,'navigator',{configurable:true,value:{onLine:online}});
 globalThis.FileReader=class{constructor(){this.readyState=0;readers.push(this);}readAsDataURL(file){this.file=file;this.readyState=1;}abort(){this.readyState=2;this.aborted=true;}finish(url){this.result=url;this.readyState=2;this.onload?.();}};
 globalThis.indexedDB={open(){const r={};queueMicrotask(()=>{r.result={close(){},transaction(){const tx={};tx.objectStore=()=>({getAll(){const result={result:[...records.values()]};queueMicrotask(()=>{result.onsuccess?.();tx.oncomplete?.();});return result;},put(record){records.set(record.key,record);queueMicrotask(()=>tx.oncomplete?.());},delete(key){records.delete(key);queueMicrotask(()=>tx.oncomplete?.());}});return tx;}};r.onsuccess();});return r;}};
 const api=(path,body)=>{calls.push({path,body});if(path.includes('/confirmations?'))return Promise.resolve({items,preparing:0,next_offset:null});return new Promise((resolve,reject)=>requests.push({path,body,resolve,reject}));};
 const openSheet=(title,markup)=>{html=title+markup;nodes.get('#sheet-content').firstElementChild={};nodes.get('#sheet').open=true;for(const key of nodes.keys())if(key.startsWith('#r2-'))nodes.delete(key);groups.clear();for(const match of markup.matchAll(/\bid="([^"]+)"/g))nodes.set('#'+match[1],makeNode(match[1]));for(const attr of ['r2-open','r2-edit','r2-later','r2-evidence','capture-mode']){const datasetKey=attr.replace(/-([a-z])/g,(_,x)=>x.toUpperCase());groups.set(`[data-${attr}]`,[...markup.matchAll(new RegExp(`data-${attr}="([^"]+)"`,'g'))].map(m=>{const b=makeNode();b.dataset[datasetKey]=m[1];return b;}));}for(const formId of ['r2-source-form','r2-edit-form']){const form=nodes.get('#'+formId);if(form){form.submit=makeNode();for(const match of markup.matchAll(/<(?:input|select|textarea)[^>]*name="([^"]+)"/g))form.elements[match[1]]=makeNode();}}};
 const ui=ownerR2({api,openSheet,reload:async()=>{},getData:()=>currentData,getEntity:()=> 'entity',notice:message=>notices.push(message),advanced(){},navigate});
 const cleanup=()=>{ui.logout();for(const[key,descriptor]of previous)if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];};
 return {ui,nodes,groups,readers,requests,calls,records,notices,body,windowEvents,documentEvents,cleanup,html:()=>html,changeOwner(){currentData={...currentData,owner:{...currentData.owner,owner_id:'owner-b'}};},async clickOpen(){const pending=groups.get('[data-r2-open]')[0].click();await tick();return {pending,request:requests.at(-1)};}};
}

function speechMock({fail=false}={}){
 const runs=[];let gesture=false;
 window.SpeechRecognition=class{
  constructor(){runs.push(this);}
  start(){assert.equal(gesture,true,'SpeechRecognition.start must share the original user gesture');this.starts=(this.starts||0)+1;if(fail)throw Error('NotAllowedError');}
  stop(){this.stops=(this.stops||0)+1;}
  abort(){this.aborts=(this.aborts||0)+1;}
 };
 return {runs,tap(fn){gesture=true;try{return fn();}finally{gesture=false;}}};
}
const resultEvent=(...parts)=>({resultIndex:0,results:parts.map(part=>{const [transcript,isFinal]=Array.isArray(part)?part:[part,true];return Object.assign([{transcript}],{isFinal});})});

test('initial Voice and a Voice tab switch activate immediately, with truthful start/listening states',()=>{
 const h=harness();try{
  const speech=speechMock();speech.tap(()=>h.ui.capture('voice_transcript'));
  assert.equal(speech.runs.length,1);assert.equal(speech.runs[0].starts,1);assert.equal(speech.runs[0].interimResults,true);assert.equal(h.nodes.get('#r2-voice-state').dataset.state,'starting');
  speech.runs[0].onstart();assert.equal(h.nodes.get('#r2-voice-state').dataset.state,'listening');
  h.nodes.get('#r2-dictate').click();assert.equal(speech.runs.length,1,'no second dictation start while active');
  h.groups.get('[data-capture-mode]')[2].click();assert.equal(speech.runs[0].aborts,1);
  speech.tap(()=>h.groups.get('[data-capture-mode]')[1].click());assert.equal(speech.runs.length,2);assert.equal(speech.runs[1].starts,1);
 }finally{h.cleanup();}
});

test('cumulative interim/final speech replacement preserves baseline and legitimate repeated words',()=>{
 const h=harness();try{
  const speech=speechMock();h.ui.capture('text');const input=h.nodes.get('#r2-source-form').elements.text;input.value='Своё примечание: ';
  speech.tap(()=>h.groups.get('[data-capture-mode]')[1].click());const run=speech.runs[0];
  for(const [parts,expected]of [[[["Полить",false]],'Полить'],[[["Полить теплицу",false]],'Полить теплицу'],[[["Полить теплицу",true]],'Полить теплицу'],[[["Полить теплицу",true]],'Полить теплицу'],[[["Полить теплицу",true],["потом склад",false]],'Полить теплицу потом склад'],[[["Полить теплицу",true]],'Полить теплицу'],[[["да да воду воду",true]],'да да воду воду']]){
   run.onresult(resultEvent(...parts));assert.equal(input.value,'Своё примечание: '+expected);
  }
 }finally{h.cleanup();}
});

test('Stop collects the final result; only end unlocks saving and fences callbacks for a restart',()=>{
 const h=harness();try{
  const speech=speechMock();speech.tap(()=>h.ui.capture('voice_transcript'));const run=speech.runs[0],late=run.onresult,form=h.nodes.get('#r2-source-form');
  run.onstart();run.onresult(resultEvent(['Полить',false]));h.nodes.get('#r2-voice-stop').click();assert.equal(run.stops,1);assert.equal(run.aborts,undefined);assert.equal(form.submit.disabled,true);assert.equal(h.nodes.get('#r2-voice-state').dataset.state,'finishing');
  run.onresult(resultEvent('Полить теплицу'));assert.equal(form.elements.text.value,'Полить теплицу');run.onend();assert.equal(form.submit.disabled,false);
  speech.tap(()=>h.nodes.get('#r2-dictate').click());const newer=speech.runs[1];late(resultEvent('PRIVATE OLD'));assert.equal(form.elements.text.value,'Полить теплицу');
  newer.onresult(resultEvent('и склад'));assert.equal(form.elements.text.value,'Полить теплицу и склад');h.nodes.get('#r2-voice-done').click();newer.onend();assert.equal(form.elements.text.focused,true);
 }finally{h.cleanup();}
});

test('manual transcript edit, close and mode changes abort and reject queued speech callbacks',()=>{
 const h=harness();try{
  const speech=speechMock();speech.tap(()=>h.ui.capture('voice_transcript'));let run=speech.runs[0],late=run.onresult;const form=h.nodes.get('#r2-source-form');run.onresult(resultEvent('Полить'));form.elements.text.value='Полить 10 литров';form.elements.text.oninput();assert.equal(run.aborts,1);late(resultEvent('PRIVATE'));assert.equal(form.elements.text.value,'Полить 10 литров');
  speech.tap(()=>h.nodes.get('#r2-dictate').click());run=speech.runs[1];late=run.onresult;h.groups.get('[data-capture-mode]')[2].click();late(resultEvent('PRIVATE'));assert.equal(form.elements.text.value,'Полить 10 литров');
  speech.tap(()=>h.groups.get('[data-capture-mode]')[1].click());run=speech.runs[2];late=run.onresult;h.nodes.get('#sheet').close();h.ui.capture('text');late(resultEvent('PRIVATE'));assert.equal(h.nodes.get('#r2-source-form').elements.text.value,'');
 }finally{h.cleanup();}
});

test('retained lifecycle callbacks after mode switch or Close cannot touch the next recognition run',()=>{
 for(const interruptedBy of ['mode switch','Close']){
  const h=harness();try{
   const speech=speechMock();speech.tap(()=>h.ui.capture('voice_transcript'));const old=speech.runs[0],retained=Object.fromEntries(['onstart','onaudiostart','onerror','onend','onresult'].map(name=>[name,old[name]]));
   old.onresult(resultEvent(['Предыдущий текст',false]));
   if(interruptedBy==='Close'){h.nodes.get('#sheet').close();speech.tap(()=>h.ui.capture('voice_transcript'));}
   else{h.groups.get('[data-capture-mode]')[2].click();speech.tap(()=>h.groups.get('[data-capture-mode]')[1].click());}
   assert.equal(old.aborts,1);const next=speech.runs[1],form=h.nodes.get('#r2-source-form');
   const snapshot=()=>({text:form.elements.text.value,focused:form.elements.text.focused,disabled:form.submit.disabled,voiceState:h.nodes.get('#r2-voice-state').dataset.state,voiceText:h.nodes.get('#r2-voice-state').textContent,help:h.nodes.get('#r2-input-help').textContent,sourceStatus:h.nodes.get('#r2-source-status').textContent,nextAborts:next.aborts});
   const before=snapshot();
   for(const [name,callback]of Object.entries(retained)){
    callback(name==='onresult'?resultEvent(['PRIVATE STALE',true]):name==='onerror'?{error:'not-allowed'}:{});
    assert.deepEqual(snapshot(),before,`${interruptedBy}: retained ${name} changed the active capture`);
   }
   next.onstart();assert.equal(h.nodes.get('#r2-voice-state').dataset.state,'listening');next.onresult(resultEvent(['Новая диктовка',true]));assert.equal(form.elements.text.value,(interruptedBy==='Close'?'':'Предыдущий текст ')+'Новая диктовка');
  }finally{h.cleanup();}
 }
});

test('unsupported, denied and throwing recognition immediately focus the text fallback with iPhone guidance',()=>{
 for(const failure of ['unsupported','denied','throw']){
  const h=harness();try{
   if(failure==='unsupported')h.ui.capture('voice_transcript');
   else{const speech=speechMock({fail:failure==='throw'});speech.tap(()=>h.ui.capture('voice_transcript'));if(failure==='denied')speech.runs[0].onerror({error:'not-allowed'});}
   assert.equal(h.nodes.get('#r2-source-form').elements.text.focused,true,failure);assert.equal(h.nodes.get('#r2-voice-state').dataset.state,'fallback');assert.match(h.nodes.get('#r2-input-help').textContent,/iPhone.*микрофон.*клавиатуре/);assert.ok(h.html().includes('Сохраняется только текст, не аудиозапись.'));
  }finally{h.cleanup();}
 }
});

test('capture modes expose only the focused entry and retain optional evidence and text',()=>{
 const h=harness();try{
  h.ui.capture('photo');assert.match(h.html(),/id="r2-file-section"[^>]* open/);assert.match(h.html(),/id="r2-text-details"[^>]*>(?:<summary)/);assert.equal((h.html().match(/name="text"/g)||[]).length,1);assert.equal((h.html().match(/id="r2-file"/g)||[]).length,1);
  const form=h.nodes.get('#r2-source-form'),file=h.nodes.get('#r2-file');const evidence={name:'TEST.png',type:'image/png',size:5};file.files=[evidence];form.elements.text.value='Важная подпись';
  h.groups.get('[data-capture-mode]')[2].click();assert.equal(h.nodes.get('#r2-file-section').open,false);assert.equal(h.nodes.get('#r2-text-details').open,true);assert.equal(h.nodes.get('#r2-voice-tools').hidden,true);
  h.groups.get('[data-capture-mode]')[1].click();assert.equal(h.nodes.get('#r2-voice-tools').hidden,false);assert.equal(form.elements.text.value,'Важная подпись');assert.equal(file.files[0],evidence);
  h.groups.get('[data-capture-mode]')[0].click();assert.equal(h.nodes.get('#r2-file-section').open,true);assert.equal(h.nodes.get('#r2-text-details').open,false);assert.equal(file.files[0],evidence);
 }finally{h.cleanup();}
});

test('card headings collapse duplicated captured phrases only in presentation and use Russian dates',()=>{
 const text='Заплатил Серкану за ремонт. Заплатил Серкану за ремонт.',p=proposal({description:text});p.source.text=text;const before=JSON.stringify(p);
 const card=proposalCard(p,ownerData()),review=proposalSummary(p,ownerData(),false);
 assert.match(card,/<h2>Заплатил Серкану за ремонт\.<\/h2>/);assert.match(review,/<h2>Заплатил Серкану за ремонт\.<\/h2>/);assert.ok(/8 окт\. 2026/.test(card));assert.ok(/8 окт\. 2026/.test(review));assert.ok(!card.includes('2026-10-08'));assert.equal(JSON.stringify(p),before);
 assert.equal(conciseProposalText('Полить первую теплицу Полить первую теплицу'),'Полить первую теплицу');assert.equal(conciseProposalText('да да'), 'да да');assert.equal(conciseProposalText('Цена 12.50 TRY, вес 0.125 кг.'),'Цена 12.50 TRY, вес 0.125 кг.');assert.ok(conciseProposalText('Очень длинное поручение '.repeat(12),40).length<=40);
 const h=harness();try{const html=h.ui.render();assert.ok(/8 окт\. 2026/.test(html));assert.ok(/1 окт\. 2026/.test(html));assert.ok(!html.includes('2026-10-08'));}finally{h.cleanup();}
});

async function editTask(h,p){
 await h.ui.queue();const {pending,request}=await h.clickOpen();request.resolve(p);await pending;h.nodes.get('#r2-edit').click();const form=h.nodes.get('#r2-edit-form');for(const[key,input]of Object.entries(form.elements))input.value=p.revision.payload[key]??'';return form;
}
function workerTask(){return proposal({title:'Полить теплицу',original_instruction:'Полить теплицу',worker_id:'w1',language:'fa',greenhouse_id:'g1',planned_date:'2026-10-09',rendered_text:'متن بررسی‌شده',note:null},{action_type:'WORKER_TASK',source:{id:'source',text:'Неизменный оригинал <source>'}});}
function taskData(){const data=ownerData();data.tasks.workers=[{id:'w1',display_name:'Первый работник',language:'fa'},{id:'w2',display_name:'Второй работник',language:'fa'},{id:'w3',display_name:'Третий работник',language:'tr'}];data.owner.finance.work_areas=[{id:'g1',kind:'greenhouse',name:'Первая теплица'},{id:'g2',kind:'greenhouse',name:'Вторая теплица'}];return data;}

test('task editor keeps intent primary and reliable facts in compact native disclosures',async()=>{
 const h=harness({data:taskData()});try{
  const p=workerTask();await editTask(h,p);const html=h.html();assert.ok(html.indexOf('name="original_instruction"')<html.indexOf('class="task-facts"'));assert.ok(html.indexOf('name="title"')>html.indexOf('Дополнительно'));assert.ok(/9 окт\. 2026/.test(html));assert.ok(html.includes('name="worker_id"'));assert.ok(html.includes('<select'));assert.ok(!html.includes('name="language_candidate"'));assert.ok(html.includes('Язык работника: Дари'));assert.ok(html.includes('Неизменный оригинал &lt;source&gt;'));assert.ok(html.includes('поручение не отправляется'));
  assert.equal(workerTranslationIssue({language:'fa',original_instruction:'Полить 5 L',rendered_text:'Полить 5 L'}),'Текст совпадает с оригиналом. Проверенный перевод ещё не добавлен.');
 }finally{h.cleanup();}
});

test('instruction, same-language worker and reviewed context changes invalidate translation without editing original evidence',async()=>{
 const h=harness({data:taskData()});try{
  const p=workerTask(),before=JSON.stringify(p),form=await editTask(h,p);form.elements.worker_id.value='w2';form.elements.worker_id.onchange();assert.equal(form.elements.rendered_text.value,'');assert.match(h.nodes.get('#r2-worker-language').textContent,/Дари/);
  form.elements.rendered_text.value='reviewed';form.elements.original_instruction.value='Полить вторую теплицу';form.elements.original_instruction.oninput();assert.equal(form.elements.rendered_text.value,'');assert.equal(form.elements.title.value,'Полить вторую теплицу');
  form.elements.title.value='Моё название';form.elements.title.oninput();form.elements.original_instruction.value='Полить ещё';form.elements.original_instruction.oninput();assert.equal(form.elements.title.value,'Моё название');
  for(const field of ['planned_date','greenhouse_id']){form.elements.rendered_text.value='reviewed';form.elements[field].onchange();assert.equal(form.elements.rendered_text.value,'');}
  assert.equal(JSON.stringify(p),before);
 }finally{h.cleanup();}
});

test('non-Russian original cannot masquerade as translated text in review or confirm',async()=>{
 const p=workerTask();p.revision.payload.rendered_text=p.revision.payload.original_instruction;const summary=proposalSummary(p,taskData(),false);assert.ok(summary.includes('Текст совпадает с оригиналом'));assert.ok(!summary.includes('<dt>Проверенный перевод</dt>'));
 const h=harness({data:taskData(),online:true});try{
  await h.ui.queue();const {pending,request}=await h.clickOpen();request.resolve(p);await pending;assert.match(h.html(),/id="r2-confirm"[^>]*disabled/);await h.nodes.get('#r2-confirm').click();assert.equal(h.calls.filter(c=>c.path.endsWith('/confirm')).length,0);
 }finally{h.cleanup();}
});

test('empty first queue retains the first-class review page without opening a nested sheet',async()=>{
 const h=harness({items:[],navigate(){}});try{assert.equal(await h.ui.queue(),false);assert.equal(h.nodes.get('#sheet').open,false);assert.equal(h.html(),'');}finally{h.cleanup();}
});

test('saving freezes capture throughout file encoding and durable write, including direct callback attempts',async()=>{
 const h=harness();try{
  const speech=speechMock();h.ui.capture('photo');const form=h.nodes.get('#r2-source-form'),input=h.nodes.get('#r2-file');form.elements.text.value='Immutable source';input.files=[{name:'proof.png',type:'image/png',size:5}];
  let transaction,stored;indexedDB.open=()=>{const request={};queueMicrotask(()=>{request.result={close(){},transaction(){transaction={objectStore(){return {put(record){stored=record;}};}};return transaction;}};request.onsuccess();});return request;};
  const saving=form.onsubmit({preventDefault(){},target:form});assert.equal(form.elements.text.disabled,true);assert.equal(input.disabled,true);assert.equal(form.submit.disabled,true);assert.ok(h.groups.get('[data-capture-mode]').every(b=>b.disabled));
  speech.tap(()=>h.groups.get('[data-capture-mode]')[1].click());assert.equal(speech.runs.length,0);form.elements.text.value='late edit';form.elements.text.oninput();assert.equal(form.elements.text.value,'Immutable source');
  h.readers[0].finish('data:image/png;base64,REAL');await tick();assert.ok(stored);assert.equal(stored.body.text,'Immutable source');assert.equal(stored.body.mode,'photo');speech.tap(()=>h.nodes.get('#r2-dictate').click());assert.equal(speech.runs.length,0);assert.equal(form.elements.text.disabled,true);transaction.oncomplete();await saving;
  assert.equal(form.elements.text.value,stored.body.text);assert.equal(form.elements.text.disabled,true);assert.equal(form.submit.disabled,true);assert.equal(h.nodes.get('#r2-source-status').dataset.state,'pending');
 }finally{h.cleanup();}
});

test('failed file encoding restores editable controls without claiming a saved original',async()=>{
 const h=harness();try{
  h.ui.capture('photo');const form=h.nodes.get('#r2-source-form'),input=h.nodes.get('#r2-file');input.files=[{name:'proof.png',type:'image/png',size:5}];const saving=form.onsubmit({preventDefault(){},target:form});h.readers[0].onerror();await saving;
  assert.equal(form.elements.text.disabled,false);assert.equal(input.disabled,false);assert.equal(form.submit.disabled,false);assert.ok(h.groups.get('[data-capture-mode]').every(b=>!b.disabled));assert.equal(h.records.size,0);assert.equal(h.nodes.get('#r2-source-status').dataset.state,'failed');
 }finally{h.cleanup();}
});
