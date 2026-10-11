import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {ColdBootstrapAttempt} from '../../prototype/owner-r3/cold-bootstrap.js';
import {ConnectedAPI, BrowserStore} from '../../prototype/owner-r3/connected-adapter.js';
import {EncryptedRecordStore, PilotCipher} from '../../src/greenhouse_owner_os/web/pilot-storage.js';
import {Header, e, btn} from '../../prototype/owner-r3/components.js';

const source = fs.readFileSync(new URL('../../prototype/owner-r3/connected.js', import.meta.url), 'utf8');
const section = (from, to) => source.slice(source.indexOf(from), source.indexOf(to, source.indexOf(from)));
const bootstrapStart = source.lastIndexOf('(async () => {');
const bootstrap = source.slice(bootstrapStart, source.indexOf('// Safari visual viewport', bootstrapStart));
const settings = {entity_id:'e',owner_id:'o',participant_id:'p',daily_pilot:true,environment:'TEST_PREVIEW',feature_sha:'test',backend_connected:true,actual_operations:false};
const secret = Buffer.from(crypto.getRandomValues(new Uint8Array(32))).toString('base64url');
const deferred = () => { let resolve, reject; const promise = new Promise((r,j) => {resolve=r;reject=j;}); return {promise, resolve, reject}; };

test('default timer cancellation keeps the browser receiver for completion and deadline recovery', () => {
  const attemptSource=fs.readFileSync(new URL('../../prototype/owner-r3/cold-bootstrap.js',import.meta.url),'utf8').replace('export class ColdBootstrapAttempt','class ColdBootstrapAttempt');
  for(const boundary of ['finish','deadline']){
    const ctx={};
    vm.runInNewContext(`
      globalThis.cancelled=[];globalThis.failures=[];
      globalThis.setTimeout=function(callback,ms){globalThis.fire=callback;globalThis.delay=ms;return 73;};
      globalThis.clearTimeout=function(handle){'use strict';if(this!==globalThis)throw TypeError('Illegal invocation');globalThis.cancelled.push(handle);};
      ${attemptSource}
      globalThis.attempt=new ColdBootstrapAttempt({generation:()=>1,failed:error=>globalThis.failures.push(error.message)});
    `,ctx);
    assert.equal(ctx.delay,15000);
    if(boundary==='finish')assert.doesNotThrow(()=>ctx.attempt.finish());
    else assert.doesNotThrow(()=>ctx.fire());
    assert.deepEqual([...ctx.cancelled],[73]);
    assert.equal(ctx.attempt.state,boundary==='finish'?'complete':'failed');
    assert.equal(ctx.failures.length,boundary==='finish'?0:1);
    assert.doesNotThrow(()=>ctx.fire());
    assert.deepEqual([...ctx.cancelled],[73]);
    assert.equal(ctx.failures.length,boundary==='finish'?0:1);
  }
});

async function harness(hold = null, retainedRows = null) {
  const entered = deferred(), gate = deferred(), calls = [], timers = new Map(), events = {};
  let timerId = 0, storageWrites = 0;
  const wait = async () => { entered.resolve(); await gate.promise; };
  const logical = 'e:o:p:draft:harvest-navigation';
  const original = {key:logical,scope:'e:o:p',type:'draft',id:'harvest-navigation',value:{route:'harvest/month/2026-10',original:'EXACT'}};
  const pending = {key:'e:o:p:request:exact-key',scope:'e:o:p',type:'request',state:'PENDING',keyHeader:'r3:exact-key',body:{amount:'100.00'}};
  const cipher = new PilotCipher('p', secret);
  const rows = retainedRows || [await cipher.seal(logical, original), await cipher.seal(pending.key, pending)];
  const frozen = JSON.stringify(rows);
  // Exercise actual BrowserStore completion, including getAll's encrypted-origin guard.
  const idb = {open() {
    const request = {};
    queueMicrotask(() => {
      request.result = {close() {}, transaction(_store, mode) {
        if (mode !== 'readonly') storageWrites++;
        const tx = {objectStore:() => ({getAll:() => read(rows, 'idb-guard'), get:key => read(rows.find(r => r.key === key), 'idb-get')})};
        function read(value, phase) {
          const r = {};
          queueMicrotask(async () => { r.result = value; r.onsuccess?.(); if (hold === phase) await wait(); tx.oncomplete?.(); });
          return r;
        }
        return tx;
      }};
      request.onsuccess?.();
    });
    return request;
  }};
  const fetcher = async (path, options = {}) => {
    calls.push({path, method:options.method || 'GET'});
    if ((hold === 'config-fetch' && path === '/preview/config') ||
        (hold === 'key-fetch' && path === '/pilot/draft-key') ||
        (hold === 'participants' && path === '/pilot/participants') ||
        (hold === 'preflight' && path === '/pilot/session') ||
        (hold === 'warmup' && path.endsWith('/owner/captures')) ||
        (hold === 'payment-fetch' && path === '/v1/payments/committed')) await wait();
    const body = path === '/preview/config' ? {...settings} : path === '/pilot/draft-key' ? {participant_id:'p',algorithm:'AES-GCM',key:secret} : path === '/pilot/session' ? {role:'owner',participant_id:'p'} : path === '/v1/payments/committed' ? {id:'committed',state:'posted'} : {};
    return {ok:true,status:200,json:async () => {
      if ((hold === 'config-body' && path === '/preview/config') || (hold === 'payment-body' && path === '/v1/payments/committed')) await wait();
      return body;
    }};
  };
  const api = new ConnectedAPI({fetcher, store:new BrowserStore(), onAuth:() => { ctx.authCalls++; }});
  const configure = api.configure.bind(api);
  api.configure = config => { configure(config); api.store = new EncryptedRecordStore(api.baseStore, config.participant_id, config.draft_key); };
  const main = {dataset:{},focus() {}};
  const ctx = {
    api, bootstrapAttempt:null, bootstrapRecovery:null, config:null, data:{}, failures:{}, ready:false,
    app:{dataset:{},innerHTML:'',style:{}}, sheetRoot:{innerHTML:''}, route:'payment/committed', epoch:0, readTimer:null,
    current:null, draft:null, estimateReview:null, harvestScroll:new Map(), authCalls:0,
    ColdBootstrapAttempt:class extends ColdBootstrapAttempt { constructor(options) { super({...options, schedule:(fn,ms) => { const id=++timerId; timers.set(id,{fn,ms}); return id; }, cancel:id => timers.delete(id)}); } },
    clearTimeout() {}, Header, e, btn, panel:body => `<section>${body}</section>`, setRadial() {}, CaptureBar:() => '', Navigation:() => '',
    fertilizerWrites:{flush:async () => { if(hold?.startsWith('render-flush')) { await wait(); if(hold==='render-flush-error')throw Error('late flush error'); } }}, paymentDetail:payment => `<p>${payment.state}</p>`, errorBox:error => `<p>${error.message}</p>`,
    document:{querySelector:selector => selector === '#main' ? main : null, addEventListener:(name, fn) => events[name] = fn},
    window:{addEventListener:(name, fn) => events[name] = fn},
    location:{hash:'#payment/committed',reload:() => { ctx.reloadedHash = ctx.location.hash; }},
  };
  vm.runInNewContext(section('function shell(', 'function setRadial(') +
    section('function showBootstrapRecovery()', 'function unavailable(') +
    section('async function render()', 'function values(') +
    section('window.addEventListener("hashchange"', 'window.addEventListener("keydown"') +
    section('async function verifyVisibleParticipant()', "window.addEventListener('pagehide'"), ctx);
  const clickStart = source.indexOf('document.addEventListener("click", (event) => {');
  vm.runInNewContext(source.slice(clickStart, source.indexOf('  const [name, ...rest]', clickStart)) + '});', ctx);
  return {ctx, rows, entered:entered.promise, release:gate.resolve, calls, events, timers,
    run() { globalThis.indexedDB = idb; return vm.runInNewContext(bootstrap, ctx); },
    expire() { const [{fn,ms}] = [...timers.values()]; assert.equal(ms,15000); fn(); },
    preserved() { assert.equal(JSON.stringify(rows),frozen); assert.equal(storageWrites,0); assert.ok(calls.every(c => c.method === 'GET')); },
  };
}

for (const hold of ['config-fetch','config-body','key-fetch','participants','preflight','warmup','idb-guard','idb-get','payment-fetch','payment-body','render-flush','render-flush-error']) {
  test(`actual cold ${hold} deadline stays fail-closed after late completion; fresh reload preserves original and pending key`, async () => {
    const h = await harness(hold), boot = h.run();
    await h.entered;
    const generation = h.ctx.api.generation, epoch = h.ctx.epoch;
    h.expire();
    assert.equal(h.ctx.ready,false);
    assert.equal(h.ctx.api.generation,generation+1);
    assert.equal(h.ctx.epoch,epoch+1);
    assert.equal(h.ctx.app.dataset.bootstrapStage,'recovery');
    assert.match(h.ctx.app.innerHTML,/Повторить загрузку страницы/);
    assert.doesNotMatch(h.ctx.app.innerHTML,/<(?:form|input|textarea|select)\b|class="dock"/);
    const recovery = h.ctx.app.innerHTML, config = h.ctx.config, data = JSON.stringify(h.ctx.data);
    h.release(); await boot;
    assert.equal(h.ctx.app.innerHTML,recovery);
    assert.equal(h.ctx.config,config);
    assert.equal(JSON.stringify(h.ctx.data),data);
    assert.equal(h.ctx.app.dataset.bootstrapStage,'recovery');
    assert.equal(h.ctx.ready,false);
    assert.equal(h.ctx.authCalls,0);
    h.ctx.location.hash = '#capture/text';
    await h.events.hashchange(); await h.ctx.render(); await h.ctx.verifyVisibleParticipant();
    assert.equal(h.ctx.app.innerHTML,recovery);
    assert.equal(h.ctx.ready,false);
    h.events.click({preventDefault(){},target:{closest:() => ({dataset:{action:'bootstrap-retry'}})}});
    assert.equal(h.ctx.reloadedHash,'#capture/text');
    h.preserved();
    const retry = await harness(null,h.rows);
    await retry.run();
    assert.equal(retry.ctx.ready,true);
    assert.equal(retry.ctx.app.dataset.bootstrapStage,'render-returned');
    assert.match(retry.ctx.app.innerHTML,/posted/);
    assert.equal(retry.ctx.api.bootstrapGuard,null);
    assert.equal(retry.timers.size,0);
    assert.equal((await retry.ctx.api.readDraft('harvest-navigation')).original,'EXACT');
    assert.equal((await retry.ctx.api.store.get('e:o:p:request:exact-key')).keyHeader,'r3:exact-key');
    retry.preserved();
  });
}

test('hashchange awaiting a failed flush cannot restore old hash or replace terminal recovery', async () => {
  const h=await harness('warmup'),boot=h.run();await h.entered;
  const flush=deferred();h.ctx.fertilizerWrites.flush=()=>flush.promise;
  h.ctx.location.hash='#capture/text';const navigation=h.events.hashchange();
  h.expire();const recovery=h.ctx.app.innerHTML;
  flush.reject(Error('late navigation flush'));await navigation;
  assert.equal(h.ctx.location.hash,'#capture/text');assert.equal(h.ctx.route,'capture/text');
  assert.equal(h.ctx.app.innerHTML,recovery);assert.equal(h.ctx.ready,false);
  h.release();await boot;h.preserved();
});

test('established-page failed draft flush still restores the prior hash and retains the form', async () => {
  const h=await harness();await h.run();const original=h.ctx.app.innerHTML;
  h.ctx.history={replaceState:(_state,_title,hash)=>h.ctx.location.hash=hash};
  h.ctx.showError=error=>h.ctx.validationError=error.message;
  h.ctx.fertilizerWrites.flush=async()=>{throw Error('draft not durable');};
  h.ctx.location.hash='#capture/text';await h.events.hashchange();
  assert.equal(h.ctx.location.hash,'#payment/committed');
  assert.equal(h.ctx.route,'payment/committed');assert.equal(h.ctx.app.innerHTML,original);
  assert.equal(h.ctx.validationError,'draft not durable');assert.equal(h.ctx.bootstrapRecovery,null);
  h.preserved();
});

test('generation replacement before configuration or during warmup cannot install old identity or data', async () => {
  for (const hold of ['key-fetch','warmup']) {
    const h = await harness(hold), boot = h.run(); await h.entered;
    h.ctx.api.generation++;
    h.ctx.config = {participant_id:'different-current-owner'};
    h.ctx.data = {sentinel:'different-current-owner'};
    h.release(); await boot;
    assert.equal(h.ctx.ready,false);
    assert.equal(h.ctx.config.participant_id,'different-current-owner');
    assert.deepEqual(h.ctx.data,{sentinel:'different-current-owner'});
    assert.match(h.ctx.app.innerHTML,/Сессия изменилась/);
    assert.equal(h.ctx.app.dataset.bootstrapStage,'recovery');
    h.preserved();
  }
});

test('captured cold guard rejects late auth response and cannot adopt a replacement guard', async () => {
  for (const boundary of ['fetch','body','preflight-body']) {
    const entered=deferred(), gate=deferred(); let auth=0, oldValid=true, replacementCalls=0;
    const api=new ConnectedAPI({store:{},onAuth:() => auth++,fetcher:async path => {
      if (boundary==='fetch') { entered.resolve(); await gate.promise; }
      return {ok:false,status:401,json:async () => { if (boundary!=='fetch') { entered.resolve(); await gate.promise; } return {}; }};
    }});
    if(boundary==='preflight-body')api.fetcher=async()=>({ok:true,status:200,json:async()=>{entered.resolve();await gate.promise;return {role:'owner',participant_id:'old'};}});
    api.dailyPilot=boundary==='preflight-body'; api.participantId='old';
    api.bootstrapGuard=() => { if(!oldValid)throw Error('expired old attempt'); };
    const request=api.get('/v1/read'); await entered.promise;
    oldValid=false; api.bootstrapGuard=() => replacementCalls++; gate.resolve();
    await assert.rejects(request,/expired old attempt/);
    assert.equal(auth,0); assert.equal(replacementCalls,0);
  }
});

test('completed cold guards allow captured success and normal mutations have no bootstrap timeout semantics', async () => {
  let generation=1, timer, failure=0;
  const attempt=new ColdBootstrapAttempt({generation:()=>generation,failed:()=>failure++,schedule:fn=>(timer=fn),cancel(){}});
  attempt.finish(); attempt.check(); timer(); assert.equal(failure,0);
  const methods=[];
  const api=new ConnectedAPI({store:{},fetcher:async(_path,options)=>{methods.push(options.method||'GET');return {ok:true,json:async()=>({ok:true})};}});
  assert.deepEqual(await api.get('/normal'),{ok:true});
  api.bootstrapGuard=()=>{throw Error('expired read guard');};
  assert.deepEqual(await api.request('/command',{method:'POST'}),{ok:true});
  assert.deepEqual(methods,['GET','POST']);
  generation++; assert.throws(()=>attempt.check(),/Сессия изменилась/);
});
