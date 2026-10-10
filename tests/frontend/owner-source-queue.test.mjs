import test from 'node:test';
import assert from 'node:assert/strict';
import {ownerR2} from '../../src/greenhouse_owner_os/web/owner-r2.js';
const tick=()=>new Promise(r=>setImmediate(r));
const ownerData=()=>({owner:{owner_id:'owner-a'}});
const proposal=()=>({});
function harness({data=ownerData(),items=[proposal()],navigate,online=false,dock=true,onWrite=()=>{}}={}){
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
 globalThis.indexedDB={open(){const r={};queueMicrotask(()=>{r.result={close(){},transaction(){const tx={};tx.objectStore=()=>({getAll(){const result={result:[...records.values()]};queueMicrotask(()=>{result.onsuccess?.();tx.oncomplete?.();});return result;},put(record){records.set(record.key,record);onWrite(record);queueMicrotask(()=>tx.oncomplete?.());},delete(key){records.delete(key);queueMicrotask(()=>tx.oncomplete?.());}});return tx;}};r.onsuccess();});return r;}};
 const api=(path,body,options)=>{calls.push({path,body,options});if(path.includes('/confirmations?'))return Promise.resolve({items,preparing:0,next_offset:null});return new Promise((resolve,reject)=>requests.push({path,body,resolve,reject}));};
 const openSheet=(title,markup)=>{html=title+markup;nodes.get('#sheet-content').firstElementChild={};nodes.get('#sheet').open=true;for(const key of nodes.keys())if(key.startsWith('#r2-'))nodes.delete(key);groups.clear();for(const match of markup.matchAll(/\bid="([^"]+)"/g))nodes.set('#'+match[1],makeNode(match[1]));for(const attr of ['r2-open','r2-edit','r2-later','r2-evidence','capture-mode','local-export','local-delete','local-retry']){const datasetKey=attr.replace(/-([a-z])/g,(_,x)=>x.toUpperCase());groups.set(`[data-${attr}]`,[...markup.matchAll(new RegExp(`data-${attr}="([^"]+)"`,'g'))].map(m=>{const b=makeNode();b.dataset[datasetKey]=m[1];return b;}));}for(const formId of ['r2-source-form','r2-edit-form']){const form=nodes.get('#'+formId);if(form){form.submit=makeNode();for(const match of markup.matchAll(/<(?:input|select|textarea)[^>]*name="([^"]+)"/g))form.elements[match[1]]=makeNode();}}};
 const ui=ownerR2({api,openSheet,reload:async()=>{},getData:()=>currentData,getEntity:()=> 'entity',notice:message=>notices.push(message),advanced(){},navigate});
 const cleanup=()=>{ui.logout();for(const[key,descriptor]of previous)if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];};
 return {ui,nodes,groups,readers,requests,calls,records,notices,body,windowEvents,documentEvents,cleanup,html:()=>html,changeOwner(){currentData={...currentData,owner:{...currentData.owner,owner_id:'owner-b'}};},async clickOpen(){const pending=groups.get('[data-r2-open]')[0].click();await tick();return {pending,request:requests.at(-1)};}};
}

const record=id=>({key:'entity:owner-a:'+id,scope:'entity:owner-a',body:{client_capture_id:id,captured_at:'2026-10-10T00:00:00Z',mode:'text',text:'TEST '+id}});
const failure=(status,message='TEST invalid upload')=>Object.assign(Error(message),{status});
const accept=req=>req.resolve({server_saved:true,client_capture_id:req.body.client_capture_id});
test('AUD-004 permanent invalid source does not block two independent sources and preserves its original',async()=>{
 const h=harness({online:true});try{
  const bad=record('a-bad');bad.body.file_base64='Tk9UIEFOTiBJTUFHRQ==';bad.body.mime_type='image/png';bad.body.filename='bad.png';
  for(const r of [bad,record('b-valid'),record('c-valid')])h.records.set(r.key,structuredClone(r));
  const sync=h.ui.sync();const caught=sync.catch(e=>e);await tick();h.requests[0].reject(failure(422,'Поддерживаются JPEG/PNG/PDF до 20 MiB. HEIC сохраните как JPEG.'));await tick();
  assert.equal(h.requests.length,2,'Second source must be attempted after permanent 422');accept(h.requests[1]);await tick();accept(h.requests[2]);await caught;
  assert.equal(h.records.size,1);assert.deepEqual(h.records.get(bad.key).body,bad.body);assert.equal(h.records.get(bad.key).upload_error.status,422);assert.equal(h.records.get(bad.key).upload_error.kind,'validation');
  await h.ui.sync();assert.equal(h.requests.length,3,'Automatic retries skip the unchanged permanent failure');
  h.ui.bind();await h.nodes.get('#owner-local').click();assert.match(h.html(),/422/);assert.match(h.html(),/HEIC/);assert.equal(h.groups.get('[data-local-export]').length,1);assert.equal(h.groups.get('[data-local-retry]').length,1);
 }finally{h.cleanup();}
});
for(const status of [401,403,408,429,500,503,0])test(`queue pauses on ${status||'network loss'} and retries original identity`,async()=>{
 const h=harness({online:true});try{
  const first=record('a-first'),second=record('b-second');for(const r of [first,second])h.records.set(r.key,structuredClone(r));
  const attempt=h.ui.sync(),same=h.ui.sync();assert.equal(attempt,same,'Concurrent callers share exact in-flight promise');const caught=attempt.catch(e=>e);await tick();h.requests[0].reject(failure(status));assert.equal((await caught).status,status);
  assert.equal(h.requests.length,1);assert.equal(h.records.size,2);assert.deepEqual(h.records.get(first.key).body,first.body);assert.equal(h.records.get(first.key).upload_error.kind,status===401?'auth':status===403?'access':'retry');
  const retry=h.ui.sync();await tick();assert.deepEqual(h.requests[1].body,first.body);accept(h.requests[1]);await tick();accept(h.requests[2]);await retry;assert.equal(h.records.size,0);
 }finally{h.cleanup();}
});
test('permanent explicit retry keeps original UUID, bytes and exported JSON',async()=>{
 const h=harness({online:true});const originalCreate=URL.createObjectURL,originalRevoke=URL.revokeObjectURL;let exported;try{
  const bad=record('bad');bad.upload_error={kind:'validation',status:422,message:'TEST <script>invalid</script>'};h.records.set(bad.key,structuredClone(bad));
  h.ui.bind();await h.nodes.get('#owner-local').click();assert.match(h.html(),/&lt;script&gt;/);assert.doesNotMatch(h.html(),/<script>/);
  URL.createObjectURL=blob=>{exported=blob;return 'blob:test';};URL.revokeObjectURL=()=>{};h.groups.get('[data-local-export]')[0].click();assert.deepEqual(JSON.parse(await exported.text()),bad.body);
  const retry=h.groups.get('[data-local-retry]')[0].click();await tick();assert.equal(h.requests.length,1);assert.deepEqual(h.requests[0].body,bad.body);accept(h.requests[0]);await retry;assert.equal(h.records.size,0);
 }finally{URL.createObjectURL=originalCreate;URL.revokeObjectURL=originalRevoke;h.cleanup();}
});
for(const result of ['success','validation','network'])test(`old owner ${result} cannot delete or mark private queued source`,async()=>{
 const h=harness({online:true});try{
  const a=record('a'),b={...record('b'),key:'entity:owner-b:b',scope:'entity:owner-b'};h.records.set(a.key,structuredClone(a));h.records.set(b.key,b);
  const sent=h.ui.sync();await tick();h.ui.logout();h.changeOwner();if(result==='success')accept(h.requests[0]);else h.requests[0].reject(failure(result==='validation'?422:0,'PRIVATE ERROR'));await sent;
  assert.deepEqual(h.records.get(a.key),a);assert.equal(h.requests.length,1);assert.deepEqual(h.notices,[]);
 }finally{h.cleanup();}
});
test('untrusted acknowledgement never deletes original and pauses subsequent sources',async()=>{
 const h=harness({online:true});try{
  for(const r of [record('first'),record('second')])h.records.set(r.key,r);
  const attempt=h.ui.sync(),caught=attempt.catch(e=>e);await tick();h.requests[0].resolve({server_saved:true,client_capture_id:'wrong'});assert.match((await caught).message,/не подтвердила/);assert.equal(h.records.size,2);assert.equal(h.requests.length,1);
 }finally{h.cleanup();}
});
test('422 while saving has an honest permanent error in the frozen capture view',async()=>{
 const h=harness({online:true});try{
  h.ui.capture();const form=h.nodes.get('#r2-source-form');form.elements.text.value='Original';const saved=form.onsubmit({preventDefault(){},target:form});await tick();h.requests[0].reject(failure(422,'TEST bad signature'));await saved;
  assert.equal(h.nodes.get('#r2-source-status').dataset.state,'failed');assert.match(h.nodes.get('#r2-source-status').textContent,/422.*bad signature/);assert.doesNotMatch(h.nodes.get('#r2-source-status').textContent,/восстановлении связи/);assert.equal(form.submit.disabled,true);assert.equal(h.records.size,1);assert.equal(h.requests.length,1);
 }finally{h.cleanup();}
});

test('REVIEW: a deferred error write must not update a logged-out session',async()=>{
 const h=harness({online:true});try{
  const original=record('deferred-error');h.records.set(original.key,structuredClone(original));
  const work=h.ui.sync();await tick();
  const originalOpen=indexedDB.open;let resume;
  indexedDB.open=()=>{const inner=originalOpen(),outer={};inner.onsuccess=()=>{resume=()=>{outer.result=inner.result;outer.onsuccess();};};return outer;};
  h.requests[0].reject(failure(422,'PRIVATE OLD SESSION ERROR'));await tick();
  assert.equal(typeof resume,'function');h.ui.logout();h.changeOwner();indexedDB.open=originalOpen;resume();await work;
  assert.deepEqual(h.records.get(original.key),original,'A logged-out job must not mark the original after pendingStore resolves');
 }finally{h.cleanup();}
});
test('REVIEW: late validation failure must not resurrect an explicitly deleted source',async()=>{
 const h=harness({online:true});try{
  const original=record('deleted-during-upload');h.records.set(original.key,structuredClone(original));
  const work=h.ui.sync();await tick();h.ui.bind();await h.nodes.get('#owner-local').click();await h.groups.get('[data-local-delete]')[0].click();
  assert.equal(h.records.size,0);h.requests[0].reject(failure(422));await work;
  assert.equal(h.records.size,0,'Deletion must remain effective after the in-flight failure');
 }finally{h.cleanup();}
});
test('REVIEW: explicit retry must not disappear when the existing pass rejects',async()=>{
 const h=harness({online:true});try{
  const transient=record('a-transient'),bad=record('b-permanent');bad.upload_error={kind:'validation',status:422,message:'original failure'};
  h.records.set(transient.key,transient);h.records.set(bad.key,bad);
  const activePass=h.ui.sync();const settled=activePass.catch(e=>e);await tick();h.ui.bind();await h.nodes.get('#owner-local').click();
  const explicit=h.groups.get('[data-local-retry]')[0].click();h.requests[0].reject(failure(503));await settled;await tick();
  const target=h.requests.find(r=>r.body.client_capture_id===bad.body.client_capture_id);assert.ok(target,'Selected source retry starts after shared failure');accept(target);await explicit;
  assert.ok(h.requests.some(r=>r.body.client_capture_id===bad.body.client_capture_id),'The selected explicit retry should be attempted or clearly retained');
 }finally{h.cleanup();}
});

test('REVIEW: deferred acknowledgement deletion must not mutate a logged-out session',async()=>{
 const h=harness({online:true});try{
  const original=record('deferred-delete');h.records.set(original.key,structuredClone(original));
  const work=h.ui.sync();await tick();const originalOpen=indexedDB.open;let resume;
  indexedDB.open=()=>{const inner=originalOpen(),outer={};inner.onsuccess=()=>{resume=()=>{outer.result=inner.result;outer.onsuccess();};};return outer;};
  accept(h.requests[0]);await tick();assert.equal(typeof resume,'function');h.ui.logout();h.changeOwner();indexedDB.open=originalOpen;resume();await work;
  assert.deepEqual(h.records.get(original.key),original,'Logged-out acknowledgement must not delete the queued original');
 }finally{h.cleanup();}
});

for(const server_saved of [false,'false',1,{},null,undefined])test(`untrusted non-boolean acknowledgement is retained: ${JSON.stringify(server_saved)}`,async()=>{
 const h=harness({online:true});try{const original=record('ack');h.records.set(original.key,original);const work=h.ui.sync(),caught=work.catch(e=>e);await tick();h.requests[0].resolve({server_saved,client_capture_id:'ack'});assert.match((await caught).message,/не подтвердила/);assert.deepEqual(h.records.get(original.key).body,original.body);}finally{h.cleanup();}
});

test('source upload supplies current owner scope fence through asynchronous API preparation',async()=>{
 const h=harness({online:true});try{const original=record('scope-fence');h.records.set(original.key,original);const work=h.ui.sync();await tick();assert.equal(h.calls[0].options.isCurrent(),true);h.changeOwner();assert.equal(h.calls[0].options.isCurrent(),false);accept(h.requests[0]);await work;assert.equal(h.records.size,1);}finally{h.cleanup();}
});
