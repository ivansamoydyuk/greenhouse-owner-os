import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';
import {webcrypto} from 'node:crypto';
import * as core from '../../src/greenhouse_owner_os/web/core.js';
import * as navigation from '../../src/greenhouse_owner_os/web/navigation.js';
const appSource=await readFile(new URL('../../src/greenhouse_owner_os/web/app.js',import.meta.url),'utf8');
const response=(status,body={},json=true)=>({ok:status>=200&&status<300,status,json:async()=>{if(!json)throw Error('Invalid JSON');return structuredClone(body);}});
const tick=()=>new Promise(r=>setImmediate(r));
function harness(handler){
 const nodes=new Map(),requests=[],storage=new Map();let logoutCount=0,personalLogoutCount=0,personalInvalidations=0,localOpens=0,ownerScope;
 const node=id=>({id,innerHTML:'',textContent:'',dataset:{},style:{},hidden:false,inert:false,open:false,attributes:{},classList:{add(){},remove(){},toggle(){}},setAttribute(k,v){this.attributes[k]=v;},addEventListener(){},querySelector(){return null;},querySelectorAll(){return [];},close(){this.open=false;},getBoundingClientRect:()=>({height:0}),focus(){}});
 for(const id of ['app','sheet','confirmation','refresh','notice','nav','owner-dock','login','login-error','secret','load-retry','load-local'])nodes.set('#'+id,node(id));
 for(const id of ['.brand','.skip-link'])nodes.set(id,node(id));
 const module=options=>{if(options.getOwnerId)ownerScope=options.getOwnerId;return {render:()=>'<h1>TEST SNAPSHOT</h1>',mount(){},bind(){},localSources(){localOpens++;},getOffset:()=>0,invalidateView(){},logout(){logoutCount++;}};};
 const context=vm.createContext({...core,e:core.escape,...navigation,ownerR2:module,personalTasksUI:()=>({render:()=>'<h1>PERSONAL TASKS</h1>',bind(){},invalidateView(){personalInvalidations++;},logout(){personalLogoutCount++;}}),tasksUI:module,inventoryUI:module,harvestUI:module,globalCapture:module,inboxUI:module,containDialogTab(){},crypto:webcrypto,TextEncoder,Uint8Array,console,
  document:{querySelector:s=>nodes.get(s)||null,querySelectorAll:()=>[],body:{classList:{add(){},remove(){}}},documentElement:{style:{setProperty(){}}}},window:{addEventListener(){},scrollTo(){}},location:{hash:'#home'},history:{replaceState(){}},ResizeObserver:class{observe(){}},setTimeout:()=>0,clearTimeout(){},sessionStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},fetch:async(path,options)=>{requests.push({path,options});return handler(path,options);}});
 // Execute the real boot/reload/API functions. Only imported view modules and DOM
 // are bounded fakes; real-browser and PostgreSQL evidence run separately.
 vm.runInContext(appSource.replace(/^import .*;\n/gm,'').replace(/boot\(\)\.catch\(/,'globalThis.started=boot().catch(')+'\nglobalThis.testing={api,reload,boot,login,navigate,getData:()=>data,getEntity:()=>entity};',context);
 return {context,nodes,requests,storage,ready:context.started,api:context.testing,logouts:()=>logoutCount,personalLogouts:()=>personalLogoutCount,personalInvalidations:()=>personalInvalidations,localOpens:()=>localOpens,ownerScope:()=>ownerScope?.()};
}
const healthy=path=>path==='/preview/config'?response(200,{entity_id:'entity-a'}):path.includes('finance-overview')?response(200,{balances:{TRY:{cash:'123.45'}},references:[],entity_name:'TEST'}):path.endsWith('/owner/home')?response(200,{owner_id:'owner-a'}):response(200,{});
test('AUD-005 cold valid session with Inventory503 shows retry and no login or invented balance',async()=>{
 let outage=true;const h=harness(path=>outage&&path.endsWith('/inventory')?response(503):healthy(path));await h.ready;
 assert.doesNotMatch(h.nodes.get('#app').innerHTML,/Ключ доступа/);assert.match(h.nodes.get('#app').innerHTML,/Склад/);assert.match(h.nodes.get('#app').innerHTML,/503/);assert.match(h.nodes.get('#app').innerHTML,/Повторить/);assert.equal(h.logouts(),0);assert.equal(h.api.getData(),undefined);
 outage=false;await h.nodes.get('#load-retry').onclick();assert.equal(h.api.getData().balances.TRY.cash,'123.45');assert.match(h.nodes.get('#app').innerHTML,/TEST SNAPSHOT/);assert.equal(h.requests.filter(r=>r.path==='/preview/login').length,0);
});
for(const json of [true,false])test(`real cold401 clears snapshot and shows login, JSON=${json}`,async()=>{
 const h=harness(()=>response(401,{},json));await h.ready;assert.match(h.nodes.get('#app').innerHTML,/Ключ доступа/);assert.equal(h.api.getData(),null);assert.equal(h.logouts(),1);
});
for(const status of [403,429,503])test(`config ${status} is retryable unavailability, never wrong credentials`,async()=>{
 const h=harness(()=>response(status,{},false));await h.ready;assert.match(h.nodes.get('#app').innerHTML,/Повторить/);assert.doesNotMatch(h.nodes.get('#app').innerHTML,/Ключ доступа/);assert.equal(h.logouts(),0);
});
test('HTTP status/path survives non-JSON service failure and mutation idempotency survives retry',async()=>{
 let posts=0;const keys=[];const h=harness((path,options)=>{if(options.method==='POST'){keys.push(options.headers['Idempotency-Key']);posts++;return posts===1?response(503,{},false):response(200,{ok:true});}return healthy(path);});await h.ready;
 await assert.rejects(h.api.api('/test-write',{exact:'12.34'}),e=>e.status===503&&e.path==='/test-write');await h.api.api('/test-write',{exact:'12.34'});assert.equal(keys[0],keys[1]);assert.equal(h.storage.size,0);
});
test('warm failure keeps the atomic snapshot privately and recovery replaces it as a whole',async()=>{
 let outage=false,revision=1;const h=harness(path=>{if(outage&&path.endsWith('/inventory'))return response(503);const result=healthy(path);if(path.includes('finance-overview'))return response(200,{balances:{TRY:{cash:revision+'.00'}},references:[]});return result;});await h.ready;
 const old=h.api.getData();revision=2;outage=true;await assert.rejects(h.api.reload());assert.equal(h.api.getData(),old);assert.equal(old.balances.TRY.cash,'1.00');assert.match(h.nodes.get('#app').innerHTML,/не показываются как актуальные/);assert.doesNotMatch(h.nodes.get('#app').innerHTML,/TEST SNAPSHOT|1\.00|2\.00/);
 outage=false;await h.api.reload();assert.notEqual(h.api.getData(),old);assert.equal(h.api.getData().balances.TRY.cash,'2.00');assert.match(h.nodes.get('#app').innerHTML,/TEST SNAPSHOT/);
});
for(const oldOutcome of ['success','401','503'])test(`newer complete load wins over an older ${oldOutcome}`,async()=>{
 let hold=false,resolve;const h=harness(path=>hold&&path.includes('finance-overview')?(hold=false,new Promise(r=>resolve=r)):healthy(path));await h.ready;
 hold=true;const older=h.api.reload();await tick();await h.api.reload();const current=h.api.getData();resolve(oldOutcome==='success'?response(200,{balances:{TRY:{cash:'OLD'}}}):response(Number(oldOutcome))); await older;assert.equal(h.api.getData(),current);assert.match(h.nodes.get('#app').innerHTML,/TEST SNAPSHOT/);
});
for(const oldOutcome of ['success','401','503'])test(`logout and reauthenticated owner fence prior boot ${oldOutcome}`,async()=>{
 let hold=false,resolve,owner='a';const h=harness(path=>{if(hold&&path==='/preview/config'){hold=false;return new Promise(r=>resolve=r);}if(path==='/preview/config')return response(200,{entity_id:'entity-'+owner});if(path.endsWith('/owner/home'))return response(200,{owner_id:'owner-'+owner});return healthy(path);});await h.ready;
 hold=true;const old=h.api.boot();await tick();h.api.login();owner='b';await h.api.boot();const current=h.api.getData();resolve(oldOutcome==='success'?response(200,{entity_id:'entity-a'}):response(Number(oldOutcome)));await old;assert.equal(h.api.getData(),current);assert.equal(h.api.getEntity(),'entity-b');assert.match(h.nodes.get('#app').innerHTML,/TEST SNAPSHOT/);assert.equal(h.logouts(),1);
});

test('REVIEW: newer successful load must restore interactivity before stale pending read completes',async()=>{
 let hold=false,resolve;const h=harness(path=>hold&&path.includes('finance-overview')?(hold=false,new Promise(r=>resolve=r)):healthy(path));await h.ready;
 hold=true;const older=h.api.reload();await tick();await h.api.reload();const stillInert=h.nodes.get('#app').inert;
 resolve(response(200,{balances:{TRY:{cash:'OLD'}}}));await older;
 assert.equal(stillInert,false,'New snapshot should be usable without waiting indefinitely for superseded load');
});
test('REVIEW: mutation waiting for key digest must not send under a different session',async()=>{
 const h=harness(healthy);await h.ready;let resume;
 h.context.crypto={randomUUID:()=>webcrypto.randomUUID(),subtle:{digest:()=>new Promise(r=>resume=r)}};
 const pending=h.api.api('/test-old-owner-write',{private:'old-owner'}).catch(e=>e);await tick();h.api.login();await h.api.boot();
 resume(new Uint8Array(32).buffer);await pending;
 assert.equal(h.requests.filter(r=>r.path==='/test-old-owner-write').length,0,'Old mutation must not start after session changed during async request preparation');
});

test('authenticated cold and warm module outage retain local-copy access without showing stale balances',async()=>{
 let outage=true;const h=harness(path=>outage&&path.endsWith('/inventory')?response(503):healthy(path));await h.ready;
 assert.match(h.nodes.get('#app').innerHTML,/id="load-local"/);await h.nodes.get('#load-local').onclick();assert.equal(h.localOpens(),1);assert.equal(h.api.getData(),undefined);
 outage=false;await h.api.boot();outage=true;await assert.rejects(h.api.reload());await h.nodes.get('#load-local').onclick();assert.equal(h.localOpens(),2);assert.doesNotMatch(h.nodes.get('#app').innerHTML,/TEST SNAPSHOT/);
 h.api.login();assert.doesNotMatch(h.nodes.get('#app').innerHTML,/id="load-local"/);
});
test('unknown authenticated owner never exposes another owner local copies during config failure',async()=>{
 const h=harness(()=>response(503));await h.ready;assert.doesNotMatch(h.nodes.get('#app').innerHTML,/id="load-local"/);
});

test('local-copy scope follows verified current owner independently of unpublished atomic business data',async()=>{
 let owner='owner-a',entity='entity-a',outage='';const h=harness(path=>path==='/preview/config'?response(200,{entity_id:entity}):path.endsWith('/owner/home')?response(200,{owner_id:owner}):outage&&path.includes(outage)?response(503):healthy(path));await h.ready;const old=h.api.getData();
 owner='owner-b';outage='/inventory';await assert.rejects(h.api.reload());assert.equal(h.api.getData(),old);assert.equal(h.ownerScope(),'owner-b');assert.match(h.nodes.get('#app').innerHTML,/id="load-local"/);
 entity='entity-b';outage='/finance-overview';await h.api.boot();assert.equal(h.ownerScope(),null);assert.doesNotMatch(h.nodes.get('#app').innerHTML,/id="load-local"/);
});

test('REVIEW: newer boot config failure must leave retry usable while stale reload is pending',async()=>{
 let hold=false,resolve,configFails=false;const h=harness(path=>path==='/preview/config'&&configFails?response(503):hold&&path.includes('finance-overview')?(hold=false,new Promise(r=>resolve=r)):healthy(path));await h.ready;
 hold=true;const older=h.api.reload();await tick();configFails=true;await h.api.boot();const stillInert=h.nodes.get('#app').inert;
 resolve(response(200,{balances:{TRY:{cash:'OLD'}}}));await older;
 assert.equal(stillInert,false,'A current unavailable/retry view must not remain inert because of an obsolete load');
});


test('personal-task host invalidates reads on unavailable view, navigation and logout',async()=>{
 let outage=false;const h=harness(path=>outage&&path.endsWith('/inventory')?response(503):healthy(path));await h.ready;
 await h.api.navigate('personal_tasks',{historyMode:'none',refresh:false});assert.match(h.nodes.get('#app').innerHTML,/PERSONAL TASKS/);assert.equal(h.personalInvalidations(),1);
 outage=true;await assert.rejects(h.api.reload());assert.ok(h.personalInvalidations()>1);
 h.api.login();assert.equal(h.personalLogouts(),1);assert.equal(h.logouts(),1);
});
