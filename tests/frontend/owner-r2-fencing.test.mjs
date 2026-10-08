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
 const proposal=id=>({id,version:1,state:'READY',action_type:'PERSONAL_TASK',source:{text:'PRIVATE '+id},revision:{payload:{title:'TEST '+id},missing:[],basis:{},preview_digest:'a'.repeat(64)}});
 const api=(path,body)=>{if(path.includes('/confirmations?'))return Promise.resolve({items:[proposal('old')],preparing:0,next_offset:null});return new Promise((resolve,reject)=>held.push({path,body,resolve,reject}));};
 function openSheet(title,body){html=title+body;const content=nodes.get('#sheet-content');content.firstElementChild={};nodes.get('#sheet').open=true;for(const [key] of nodes)if(key.startsWith('#r2-'))nodes.delete(key);groups.clear();for(const match of body.matchAll(/\bid="([^"]+)"/g))nodes.set('#'+match[1],node(match[1]));const buttons=[...body.matchAll(/data-r2-open="([^"]+)"/g)].map(m=>{const n=node();n.dataset.r2Open=m[1];return n;});groups.set('[data-r2-open]',buttons);groups.set('[data-source-manual]',[...body.matchAll(/data-source-manual/g)].map(()=>node()));const form=nodes.get('#r2-edit-form');if(form)for(const m of body.matchAll(/<(?:input|select|textarea)[^>]*name="([^"]+)"/g))form.elements[m[1]]=node();}
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

test('manual handoff preserves the reviewed domain for every proposal kind',async()=>{
 for(const kind of ['PAYABLE','PAYMENT','DIRECT_EXPENSE','STOCK_RECEIPT','STOCK_APPLICATION','DEPOT_RECEIPT','PERSONAL_TASK','WORKER_TASK','INVOICE_MATCH','UNRESOLVED']){
  const h=harness();try{await h.ui.queue();const request=h.groups.get('[data-r2-open]')[0].click();await tick();const p=h.proposal('old');p.action_type=kind;Object.assign(p.revision.payload,{lines:[],qualities:[],currency:'TRY'});h.held[0].resolve(p);await request;h.nodes.get('#r2-edit').click();h.nodes.get('#r2-advanced').click();assert.deepEqual(h.manual,[kind],kind);}finally{h.cleanup();}
 }
});
test('failed-source manual recovery requests neutral choice; Home Finance keeps its default',async()=>{
 const h=harness();try{h.ui.bind();h.nodes.get('#owner-advanced').click();assert.deepEqual(h.manual,[undefined]);const request=h.nodes.get('#owner-sources').click();await tick();h.held[0].resolve({items:[{id:'failed',text:'Original preserved',state:'FAILED',retryable:false,proposals:[]}]});await request;h.groups.get('[data-source-manual]')[0].click();assert.deepEqual(h.manual,[undefined,'UNRESOLVED']);}finally{h.cleanup();}
});

test('duplicate depot text has an explicit separate-event review, while binaries never offer it',async()=>{
 const cases=[{basis:{confirmed_source_receipts:[{id:'prior'}]},file:false,shown:true},{basis:{existing_receipts:[{status:'posted'}]},file:false,shown:true},{basis:{existing_receipts:[{status:'draft'}]},file:false,shown:false},{basis:{confirmed_source_receipts:[{id:'prior'}]},file:true,shown:false}];
 for(const c of cases){const h=harness();const originalFormData=globalThis.FormData;try{
  await h.ui.queue();const request=h.groups.get('[data-r2-open]')[0].click();await tick();const p=h.proposal('old');p.action_type='DEPOT_RECEIPT';p.source.has_file=c.file;p.revision.basis=c.basis;Object.assign(p.revision.payload,{lines:[],qualities:[],currency:'TRY',separate_event_reviewed:false});h.held[0].resolve(p);await request;h.nodes.get('#r2-edit').click();assert.equal(h.html().includes('Это отдельный чек по текстовому источнику, не повтор прежнего'),c.shown);
  if(c.shown){globalThis.FormData=class{*[Symbol.iterator](){yield ['separate_event_reviewed','on'];}};const form=h.nodes.get('#r2-edit-form'),submit=form.onsubmit({preventDefault(){},target:form});await tick();assert.equal(h.held[1].body.payload.separate_event_reviewed,true);h.ui.logout();h.held[1].resolve({});await submit;}
 }finally{globalThis.FormData=originalFormData;h.cleanup();}}
});
