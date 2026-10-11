import test from 'node:test';
import assert from 'node:assert/strict';
import {ownerR2} from '../../src/greenhouse_owner_os/web/owner-r2.js';
const tick=()=>new Promise(r=>setImmediate(r));
function harness(){
 const nodes=new Map(),held=[],notices=[],manual=[],dbRequests=[],windowEvents=new Map();let html='',owner='owner-a';
 const groups=new Map();
 function node(id=''){return {id,dataset:{},open:false,hidden:false,disabled:false,textContent:'',firstElementChild:null,elements:{text:{value:''}},classList:{toggle(){},remove(){}},events:{},addEventListener(name,fn){this.events[name]=fn;},querySelector(){return node();},querySelectorAll(){return [];},close(){this.open=false;this.events.close?.();},click(){return this.onclick?.()||this.events.click?.();},setAttribute(){},set innerHTML(value){this._html=value;},get innerHTML(){return this._html||'';}};}
 for(const id of ['sheet','sheet-content','owner-queue','owner-local','owner-sources','owner-pending-state','owner-capture-bar','owner-advanced'])nodes.set('#'+id,node(id));
 globalThis.document={querySelector:s=>nodes.get(s)||null,querySelectorAll:s=>groups.get(s)||[],addEventListener(){},body:{classList:{toggle(){},remove(){}},append(){}},createElement:()=>node()};
 globalThis.window={addEventListener:(event,fn)=>windowEvents.set(event,fn),confirm:()=>true};
 const priorNavigator=Object.getOwnPropertyDescriptor(globalThis,'navigator');Object.defineProperty(globalThis,'navigator',{configurable:true,value:{onLine:false}});
 globalThis.indexedDB={open(){const r={};dbRequests.push(r);return r;}};
 const data=()=>({owner:{owner_id:owner,finance:{references:[],work_areas:[],obligations:[]}},inventory:{products:[],locations:[]},tasks:{workers:[]}});
 const proposal=id=>({id,version:1,state:'READY',action_type:'PERSONAL_TASK',source:{id:'source-'+id,text:'PRIVATE '+id},revision:{payload:{title:'TEST '+id},missing:[],basis:{},preview_digest:'a'.repeat(64)}});
 const api=(path,body)=>{if(path.includes('/confirmations?'))return Promise.resolve({items:[proposal('old')],preparing:0,next_offset:null});return new Promise((resolve,reject)=>held.push({path,body,resolve,reject}));};
 function openSheet(title,body){html=title+body;const content=nodes.get('#sheet-content');content.firstElementChild={};nodes.get('#sheet').open=true;for(const [key] of nodes)if(key.startsWith('#r2-'))nodes.delete(key);groups.clear();for(const match of body.matchAll(/\bid="([^"]+)"/g))nodes.set('#'+match[1],node(match[1]));const buttons=[...body.matchAll(/data-r2-open="([^"]+)"/g)].map(m=>{const n=node();n.dataset.r2Open=m[1];return n;});groups.set('[data-r2-open]',buttons);groups.set('[data-source-manual]',[...body.matchAll(/data-source-manual="([^"]+)"/g)].map(m=>{const n=node();n.dataset.sourceManual=m[1];return n;}));const form=nodes.get('#r2-edit-form');if(form)for(const m of body.matchAll(/<(?:input|select|textarea)[^>]*name="([^"]+)"/g))form.elements[m[1]]=node();}
 const ui=ownerR2({api,openSheet,reload:async()=>{},getData:data,getEntity:()=> 'entity',notice:x=>notices.push(x),advanced:kind=>manual.push(kind)});
 const releaseDb=(request,records)=>{const result={close(){},transaction(){return {objectStore(){return {getAll(){const r={result:records};queueMicrotask(()=>r.onsuccess());return r;}};}};}};request.result=result;request.onsuccess();};
 const cleanup=()=>{if(priorNavigator)Object.defineProperty(globalThis,'navigator',priorNavigator);else delete globalThis.navigator;for(const key of ['document','window','indexedDB'])delete globalThis[key];};
 return {ui,nodes,groups,held,notices,manual,dbRequests,proposal,releaseDb,cleanup,changeOwner:()=>owner='owner-b',html:()=>html};
}
test('late review cannot overwrite a newer capture view or expose the old original',async()=>{
 const h=harness();try{await h.ui.queue();const request=h.groups.get('[data-r2-open]')[0].click();await tick();assert.equal(h.held.length,1);h.ui.capture();h.held[0].resolve(h.proposal('old'));await request;assert.ok(h.html().startsWith('Что произошло?'));assert.ok(!h.html().includes('PRIVATE'));}finally{h.cleanup();}
});
test('late review after logout and owner switch cannot open a private card',async()=>{
 const h=harness();try{await h.ui.queue();const request=h.groups.get('[data-r2-open]')[0].click();await tick();h.ui.logout();h.changeOwner();h.nodes.get('#sheet').close();h.held[0].resolve(h.proposal('old'));await request;assert.equal(h.nodes.get('#sheet').open,false);assert.ok(!h.html().includes('PRIVATE'));}finally{h.cleanup();}
});
test('source response and old errors are fenced by authenticated owner session',async()=>{
 const h=harness();try{h.ui.bind();const request=h.nodes.get('#owner-sources').click();await tick();h.ui.logout();h.changeOwner();h.held[0].resolve({items:[{id:'old',text:'PRIVATE SOURCE',state:'FAILED',proposals:[]}]});await request;assert.equal(h.nodes.get('#sheet').open,false);assert.ok(!h.html().includes('PRIVATE'));const retry=h.nodes.get('#owner-sources').click();await tick();h.ui.logout();h.held[1].reject(Error('PRIVATE ERROR'));await retry;assert.deepEqual(h.notices,[]);}finally{h.cleanup();}
});
test('IndexedDB originals from a prior owner are discarded before opening local sources',async()=>{
 const h=harness();try{h.ui.bind();const request=h.nodes.get('#owner-local').click();assert.equal(h.dbRequests.length,2);h.ui.logout();h.changeOwner();h.releaseDb(h.dbRequests[1],[{scope:'entity:owner-a',body:{text:'PRIVATE LOCAL'}}]);await request;assert.equal(h.nodes.get('#sheet').open,false);assert.ok(!h.html().includes('PRIVATE'));}finally{h.cleanup();}
});

test('other proposal kinds retain their existing domain-specific manual handoff',async()=>{
 for(const kind of ['PAYABLE','PAYMENT','STOCK_RECEIPT','STOCK_APPLICATION','DEPOT_RECEIPT','PERSONAL_TASK','WORKER_TASK','INVOICE_MATCH']){
  const h=harness();try{await h.ui.queue();const request=h.groups.get('[data-r2-open]')[0].click();await tick();const p=h.proposal('old');p.action_type=kind;Object.assign(p.revision.payload,{lines:[],qualities:[],currency:'TRY'});h.held[0].resolve(p);await request;h.nodes.get('#r2-edit').click();h.nodes.get('#r2-advanced').click();assert.deepEqual(h.manual,[kind],kind);}finally{h.cleanup();}
 }
});
test('failed source explicitly selects DirectExpense with its source ID; Home Finance keeps its default',async()=>{
 const h=harness();try{h.ui.bind();h.nodes.get('#owner-advanced').click();assert.deepEqual(h.manual,[undefined]);const request=h.nodes.get('#owner-sources').click();await tick();h.held[0].resolve({items:[{id:'failed',text:'Original preserved',state:'FAILED',retryable:false,proposals:[]}]});await request;assert.match(h.html(),/Прямой расход по этому источнику/);assert.equal(h.held.length,1);const handoff=h.groups.get('[data-source-manual]')[0].click();await tick();assert.equal(h.held[1].path,'/v1/entities/entity/owner/captures/failed/direct-expense-recovery');assert.deepEqual(h.held[1].body,{});h.nodes.get('#sheet').close();h.held[1].resolve({id:'target'});await handoff;assert.equal(h.held.length,2);assert.deepEqual(h.manual,[undefined]);}finally{h.cleanup();}
});

async function openUnresolved(h){
 await h.ui.queue();const open=h.groups.get('[data-r2-open]')[0].click();await tick();const p=h.proposal('old');p.action_type='UNRESOLVED';p.state='NEEDS_INPUT';p.revision.payload={note:'PRIVATE source is not a parsed purpose'};h.held[0].resolve(p);await open;h.nodes.get('#r2-edit').click();return p;
}
function expense(h,state='NEEDS_INPUT'){
 const p=h.proposal('target');p.action_type='DIRECT_EXPENSE';p.source.id='source-old';p.state=state;p.version=3;p.revision.payload={amount:null,currency:null,event_date:null,money_account_id:null,counterparty_id:null,description:null,attributions:[]};p.revision.missing=['Сумма','Валюта'];p.recovery={origin_proposal_id:'old',origin_version:1,recovery_proposal_id:'target'};return p;
}
test('explicit recovery preserves origin/version, suppresses double click, and edits only the fresh target preview',async()=>{
 const h=harness();try{await openUnresolved(h);assert.match(h.html(),/Прямой расход по этому источнику/);assert.equal(h.held.length,1);const b=h.nodes.get('#r2-advanced'),run=b.click();b.click();await tick();assert.equal(h.held.length,2);assert.deepEqual(h.held[1].body,{origin_proposal_id:'old',expected_version:1});assert.ok(h.held[1].path.includes('/source-old/direct-expense-recovery'));h.held[1].resolve({...expense(h),version:1});await tick();assert.ok(h.held[2].path.endsWith('/target/preview'));assert.ok(!h.html().startsWith('Исправить'));h.held[2].resolve(expense(h));await run;assert.ok(h.html().startsWith('Исправить карточку'));assert.match(h.html(),/Сумма: Не установлено/);assert.match(h.html(),/Валюта: Не установлено/);assert.match(h.html(),/Контрагент необязателен/);assert.match(h.html(),/source-old\/original/);assert.deepEqual(h.manual,[]);}finally{h.cleanup();}
});
for(const boundary of ['handoff','preview'])for(const cancellation of ['close','owner','capture'])test(`recovery ${boundary} response is fenced after ${cancellation}`,async()=>{
 const h=harness();try{await openUnresolved(h);const run=h.nodes.get('#r2-advanced').click();await tick();if(boundary==='preview'){h.held[1].resolve({id:'target'});await tick();}if(cancellation==='owner'){h.ui.logout();h.changeOwner();h.nodes.get('#sheet').close();}else if(cancellation==='capture')h.ui.capture();else h.nodes.get('#sheet').close();h.held[boundary==='handoff'?1:2].resolve(expense(h));await run;assert.equal(h.held.length,boundary==='handoff'?2:3);assert.equal(h.nodes.get('#sheet').open,cancellation==='capture');if(cancellation==='capture')assert.ok(h.html().startsWith('Что произошло?'));assert.ok(!h.html().includes('PRIVATE target'));}finally{h.cleanup();}
});
test('lost handoff response can retry the same request and target without opening generic Finance',async()=>{
 const h=harness();try{await openUnresolved(h);const run=h.nodes.get('#r2-advanced').click();await tick();h.held[1].reject(Error('Connection lost'));await run;assert.equal(h.nodes.get('#r2-recovery-status').textContent,'Connection lost');const retry=h.nodes.get('#r2-recovery-retry').click();await tick();assert.equal(h.held[2].path,h.held[1].path);assert.deepEqual(h.held[2].body,h.held[1].body);h.held[2].resolve({id:'target'});await tick();h.held[3].resolve(expense(h));await retry;assert.ok(h.html().startsWith('Исправить карточку'));assert.deepEqual(h.manual,[]);}finally{h.cleanup();}
});
test('old origin opens its current target without a second recovery or origin-version mutation',async()=>{
 const h=harness();try{await h.ui.queue();const run=h.groups.get('[data-r2-open]')[0].click();await tick();const origin=h.proposal('old');origin.state='REJECTED';origin.recovery={origin_proposal_id:'old',origin_version:1,recovery_proposal_id:'target'};h.held[0].resolve(origin);await tick();assert.ok(h.held[1].path.endsWith('/target/preview'));h.held[1].resolve({...expense(h,'POSTED'),current_effect:{status:'reversed'}});await run;assert.match(h.html(),/Расход отменён/);assert.equal(h.nodes.has('#r2-edit'),false);assert.equal(h.nodes.has('#r2-reopen'),false);assert.equal(h.held.length,2);}finally{h.cleanup();}
});
test('existing DirectExpense manual editor returns to the same review and never generic Finance',async()=>{
 const h=harness();try{await h.ui.queue();const open=h.groups.get('[data-r2-open]')[0].click();await tick();h.held[0].resolve(expense(h));await open;h.nodes.get('#r2-edit').click();assert.match(h.html(),/Вернуться к проверке/);const back=h.nodes.get('#r2-advanced').click();await tick();assert.ok(h.held[1].path.endsWith('/target/preview'));h.held[1].resolve(expense(h));await back;assert.deepEqual(h.manual,[]);}finally{h.cleanup();}
});
test('DirectExpense confirmation replay refreshes current reversal truth before presentation',async()=>{
 const h=harness();try{await h.ui.queue();const open=h.groups.get('[data-r2-open]')[0].click();await tick();const p=expense(h,'READY');p.id='old';p.recovery=null;p.revision.missing=[];h.held[0].resolve(p);await open;navigator.onLine=true;const confirm=h.nodes.get('#r2-confirm').click();await tick();assert.ok(h.held[1].path.endsWith('/old/confirm'));h.held[1].resolve({state:'POSTED'});await tick();assert.ok(h.held[2].path.endsWith('/old/preview'));h.held[2].resolve({...p,state:'POSTED',current_effect:{status:'reversed'}});await confirm;assert.match(h.html(),/Расход отменён/);assert.deepEqual(h.notices,[]);}finally{h.cleanup();}
});

test('duplicate depot text has an explicit separate-event review, while binaries never offer it',async()=>{
 const cases=[{basis:{confirmed_source_receipts:[{id:'prior'}]},file:false,shown:true},{basis:{existing_receipts:[{status:'posted'}]},file:false,shown:true},{basis:{existing_receipts:[{status:'draft'}]},file:false,shown:false},{basis:{confirmed_source_receipts:[{id:'prior'}]},file:true,shown:false}];
 for(const c of cases){const h=harness();const originalFormData=globalThis.FormData;try{
  await h.ui.queue();const request=h.groups.get('[data-r2-open]')[0].click();await tick();const p=h.proposal('old');p.action_type='DEPOT_RECEIPT';p.source.has_file=c.file;p.revision.basis=c.basis;Object.assign(p.revision.payload,{lines:[],qualities:[],currency:'TRY',separate_event_reviewed:false});h.held[0].resolve(p);await request;h.nodes.get('#r2-edit').click();assert.equal(h.html().includes('Это отдельный чек по текстовому источнику, не повтор прежнего'),c.shown);
  if(c.shown){globalThis.FormData=class{*[Symbol.iterator](){yield ['separate_event_reviewed','on'];}};const form=h.nodes.get('#r2-edit-form'),submit=form.onsubmit({preventDefault(){},target:form});await tick();assert.equal(h.held[1].body.payload.separate_event_reviewed,true);h.ui.logout();h.held[1].resolve({});await submit;}
 }finally{globalThis.FormData=originalFormData;h.cleanup();}}
});

test('Saved Sources click never serializes the PointerEvent as a pagination offset',async()=>{
 const h=harness();try{h.ui.bind();const event={toString:()=> '[object PointerEvent]'};const run=h.nodes.get('#owner-sources').events.click(event);await tick();assert.equal(h.held[0].path,'/v1/entities/entity/owner/captures?offset=0');h.held[0].resolve({items:[]});await run;}finally{h.cleanup();}
});
test('new counterparty return keeps saved provenance separate from unsaved changed expense controls',async()=>{
 const h=harness(),originalFormData=globalThis.FormData;try{
  await h.ui.queue();const run=h.groups.get('[data-r2-open]')[0].click();await tick();const p=expense(h);p.revision.payload.amount='321';p.revision.payload.currency='TRY';p.revision.provenance={amount:{category:'SOURCE FACT'}};h.held[0].resolve(p);await run;h.nodes.get('#r2-edit').click();
  globalThis.FormData=class{*[Symbol.iterator](){yield ['amount','999'];yield ['currency','TRY'];}};
  h.nodes.get('#r2-party-new').click();const form=h.nodes.get('#r2-new-party');form.elements.name={value:'TEST Party'};const submit=form.onsubmit({preventDefault(){},target:form});await tick();h.held[1].resolve({id:'new-party'});await submit;
  assert.match(h.html(),/name="amount"[^>]*value="999"/);assert.match(h.html(),/Сумма: Из источника · 321/);assert.doesNotMatch(h.html(),/Из источника · 999/);assert.match(h.html(),/Поля ниже — текущий ввод/);assert.equal(p.revision.payload.amount,'321');assert.equal(p.revision.provenance.amount.category,'SOURCE FACT');
 }finally{globalThis.FormData=originalFormData;h.cleanup();}
});
