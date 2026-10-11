import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {observeDiscardedReadTerminal} from '../e2e/helpers/discarded-read-terminal.mjs';
import {boundedHandshake} from '../e2e/helpers/bounded-handshake.mjs';

const request=()=>({method:()=> 'GET',url:()=> 'https://test.invalid/v1/entities/test/owner/captures'});
const response=(target,status=200)=>({request:()=>target,status:()=>status});
const listeners=page=>['response','requestfinished','requestfailed'].map(event=>page.listenerCount(event));

for(const terminal of ['requestfinished','requestfailed']){
  test(`controlled expired read requires its exact HTTP200 and ${terminal}, preserving explicit disposition`,async()=>{
    const page=new EventEmitter(),held=request(),observer=observeDiscardedReadTerminal(page,held);
    page.emit('response',response(held));page.emit(terminal,held);
    assert.deepEqual(await observer.promise,{status:200,terminal,disposition:terminal==='requestfailed'?'expired-read-failed':'expired-read-finished'});
    assert.deepEqual(listeners(page),[0,0,0]);observer.dispose();assert.deepEqual(listeners(page),[0,0,0]);
  });
}

test('another request with the same URL cannot satisfy either header or terminal proof',async()=>{
  const page=new EventEmitter(),held=request(),other=request(),observer=observeDiscardedReadTerminal(page,held);
  let settled=false;observer.promise.then(()=>settled=true);
  page.emit('response',response(other));page.emit('requestfailed',other);await Promise.resolve();assert.equal(settled,false);
  page.emit('response',response(held));page.emit('requestfinished',other);await Promise.resolve();assert.equal(settled,false);
  page.emit('requestfailed',held);assert.equal((await observer.promise).terminal,'requestfailed');
  assert.deepEqual(listeners(page),[0,0,0]);
});

test('an arbitrary failure without the controlled200 times out and releases all listeners',async()=>{
  const page=new EventEmitter(),held=request(),observer=observeDiscardedReadTerminal(page,held),entries=[];
  let fire,ready,cancelled=0;const scheduled=new Promise(resolve=>ready=resolve);
  const handshake=boundedHandshake({checkpoint:async entry=>entries.push(entry),schedule:callback=>{fire=callback;ready();return 4;},cancel:id=>{assert.equal(id,4);cancelled++;}});
  const pending=handshake('late-response-terminal',()=>observer.promise);
  await scheduled;page.emit('requestfailed',held);fire();
  try{await assert.rejects(pending,/handshake timed out: late-response-terminal/);}
  finally{observer.dispose();}
  assert.equal(entries.at(-1).state,'failed');assert.equal(entries.at(-1).timedOut,true);assert.equal(cancelled,1);
  assert.deepEqual(listeners(page),[0,0,0]);
});

test('non200 response rejects instead of accepting an unrelated network failure as recovery evidence',async()=>{
  const page=new EventEmitter(),held=request(),observer=observeDiscardedReadTerminal(page,held);
  page.emit('response',response(held,503));page.emit('requestfailed',held);
  await assert.rejects(observer.promise,/unexpected HTTP 503/);assert.deepEqual(listeners(page),[0,0,0]);
  assert.throws(()=>observeDiscardedReadTerminal(page,{method:()=> 'POST'}),/exact held bootstrap GET/);
});

test('listeners installed before release catch synchronous response and failed terminal without a race',async()=>{
  const page=new EventEmitter(),held=request(),observer=observeDiscardedReadTerminal(page,held),entries=[];
  const handshake=boundedHandshake({checkpoint:async entry=>entries.push(entry)});
  const result=await handshake('late-response-terminal',()=>{
    page.emit('response',response(held));page.emit('requestfailed',held);
    return observer.promise;
  });
  assert.equal(result.disposition,'expired-read-failed');
  assert.deepEqual(entries,[{phase:'late-response-terminal',state:'started'},{phase:'late-response-terminal',state:'complete'}]);
  assert.deepEqual(listeners(page),[0,0,0]);
});
