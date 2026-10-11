import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {FertilizerDraftWrites} from '../../prototype/owner-r3/fertilizer-draft-writes.js';
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject};};
const tick=()=>Promise.resolve();
test('failed durable write retains form values and is never reported saved; next edit retries',async()=>{const form={reviewed:'on'},states=[],gate=deferred();const api={scope:'owner',generation:1,draft:()=>gate.promise};const writes=new FertilizerDraftWrites(api,(_,s)=>states.push(s));const p=writes.enqueue('fertilizer-new',form);gate.reject(Error('Disk unavailable'));await assert.rejects(p,/Disk/);assert.equal(form.reviewed,'on');assert.equal(writes.state('fertilizer-new'),'error');assert.deepEqual(states,['saving','error']);await assert.rejects(writes.flush('fertilizer-new'));api.draft=async()=>{};await writes.enqueue('fertilizer-new',form);assert.equal(writes.state('fertilizer-new'),'saved');});
test('overlapping snapshots serialize and flush waits for newest durable value',async()=>{const gates=[deferred(),deferred()],seen=[],states=[];const api={scope:'o',generation:1,draft:async(_,v)=>{seen.push(v);await gates[seen.length-1].promise;}};const w=new FertilizerDraftWrites(api,(_,s)=>states.push(s));const form={reviewed:false};const a=w.enqueue('f',form);form.reviewed=true;const b=w.enqueue('f',form);let flushed=false;const flush=w.flush('f').then(()=>flushed=true);await tick();await tick();assert.deepEqual(seen,[{reviewed:false}]);gates[0].resolve();await a;await tick();assert.equal(w.state('f'),'saving');assert.equal(flushed,false);assert.deepEqual(seen,[{reviewed:false},{reviewed:true}]);gates[1].resolve();await b;await flush;assert.equal(flushed,true);assert.equal(states.filter(s=>s==='saved').length,1);});
test('session and entity generation fences prevent queued prior-owner writes and saved badges',async()=>{const gate=deferred(),seen=[],states=[];const api={scope:'ownerA',generation:1,draft:async(_,v)=>{seen.push(v);await gate.promise;}};const w=new FertilizerDraftWrites(api,(_,s)=>states.push(s));const a=w.enqueue('f',{n:1}),b=w.enqueue('f',{n:2});await tick();await tick();api.scope='ownerB';api.generation=2;gate.resolve();await assert.rejects(a,/Сессия/);await assert.rejects(b,/Сессия/);assert.deepEqual(seen,[{n:1}]);assert.equal(w.state('f'),'idle');assert.equal(states.includes('saved'),false);});
test('pending and failed drafts guard unload, durable completion clears guard',async()=>{const gate=deferred(),api={scope:'o',generation:1,draft:()=>gate.promise},w=new FertilizerDraftWrites(api);let prevented=0;const event={preventDefault:()=>prevented++};const p=w.enqueue('f',{});assert.equal(w.protectUnload('f',event),true);assert.equal(event.returnValue,'');gate.resolve();await p;assert.equal(w.protectUnload('f',event),false);assert.equal(prevented,1);api.draft=async()=>{throw Error('disk');};await assert.rejects(w.enqueue('f',{}));assert.equal(w.protectUnload('f',event),true);});
test('fertilizer actions, saves and navigation are wired to durable ordering without clearing failed forms',()=>{const source=readFileSync(new URL('../../prototype/owner-r3/connected.js',import.meta.url),'utf8');assert.match(source,/if\(name\.startsWith\('fert-'\)\) return action\(async\(\)=>\{\s*await fertilizerWrites\.flush\(route\)/);assert.match(source,/if\(route\.startsWith\('fertilizer-'\)\)await fertilizerWrites\.enqueue\(route,draft\)/);assert.match(source,/await fertilizerWrites\.flush\(previous\);\}catch\(error\)\{if\(bootstrapRecovery\)\{route=location\.hash\.slice\(1\)\|\|'home';showBootstrapRecovery\(\);return;\}history\.replaceState/);assert.match(source,/beforeunload.*fertilizerWrites\.protectUnload/);assert.match(source,/Серверное подтверждение отдельно/);});
test('derived fertilizer reference fields follow the input snapshot in the same write queue',async()=>{const seen=[],api={scope:'o',generation:1,draft:async(_,value)=>seen.push(value)},w=new FertilizerDraftWrites(api);const draft={canonical_key:'part'};const first=w.enqueue('fertilizer-references',draft);Object.assign(draft,{area_da:'5',verification:'Проверено соответствие CURRENT'});const derived=w.enqueue('fertilizer-references',draft);await Promise.all([first,derived]);assert.equal(seen.at(-1).area_da,'5');assert.equal(seen.at(-1).verification,'Проверено соответствие CURRENT');const source=readFileSync(new URL('../../prototype/owner-r3/connected.js',import.meta.url),'utf8');assert.match(source,/value=draft\.verification;fertilizerWrites\.enqueue\(route,draft\)/);});

test('a burst keeps only the newest waiting snapshot and all superseded callers await its durable commit',async()=>{
  const gates=[deferred(),deferred()],seen=[],states=[];
  const api={scope:'owner',generation:1,draft:async(_,value)=>{const index=seen.length;seen.push(value);await gates[index].promise;}};
  const writes=new FertilizerDraftWrites(api,(_,state)=>states.push(state));
  const first=writes.enqueue('attach-depot/report',{kg:'1'}),pending=[];
  let acknowledged=0;
  for(let n=2;n<=100;n++)pending.push(writes.enqueue('attach-depot/report',{kg:String(n)}).then(()=>acknowledged++));
  assert.deepEqual(seen,[{kg:'1'}]);gates[0].resolve();await first;await tick();
  assert.deepEqual(seen,[{kg:'1'},{kg:'100'}]);assert.equal(acknowledged,0);assert.equal(writes.state('attach-depot/report'),'saving');
  let flushed=false;const flush=writes.flush('attach-depot/report').then(()=>flushed=true);await tick();assert.equal(flushed,false);
  gates[1].resolve();await Promise.all(pending);await flush;
  assert.equal(acknowledged,99);assert.equal(states.filter(state=>state==='saved').length,1);assert.equal(writes.unsaved('attach-depot/report'),false);
});

test('failed coalesced latest snapshot rejects every covered caller, protects unload, and retries unchanged facts',async()=>{
  const gates=[deferred(),deferred()],seen=[];
  const api={scope:'o',generation:1,draft:async(_,value)=>{const index=seen.length;seen.push(value);await gates[index].promise;}};
  const writes=new FertilizerDraftWrites(api),first=writes.enqueue('r',{kg:'1'});
  const superseded=writes.enqueue('r',{kg:'2'}),latest=writes.enqueue('r',{kg:'300'});
  gates[0].resolve();await first;gates[1].reject(Error('quota'));
  await assert.rejects(superseded,/quota/);await assert.rejects(latest,/quota/);await assert.rejects(writes.flush('r'),/quota/);
  assert.deepEqual(seen,[{kg:'1'},{kg:'300'}]);assert.equal(writes.protectUnload('r',{preventDefault(){}}),true);
  api.draft=async(_,value)=>seen.push(value);await writes.enqueue('r',{kg:'300'});await writes.flush('r');assert.deepEqual(seen.at(-1),{kg:'300'});assert.equal(writes.state('r'),'saved');
});

test('an edit queued by a durable acknowledgement is drained before flush returns',async()=>{
  const seen=[],api={scope:'o',generation:1,draft:async(_,value)=>seen.push(value)},writes=new FertilizerDraftWrites(api);
  const first=writes.enqueue('r',{kg:'1'});const next=first.then(()=>writes.enqueue('r',{kg:'300'}));
  await first;await writes.flush('r');await next;assert.deepEqual(seen,[{kg:'1'},{kg:'300'}]);assert.equal(writes.state('r'),'saved');
});
