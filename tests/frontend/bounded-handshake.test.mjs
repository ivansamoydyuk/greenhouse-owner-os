import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import {boundedHandshake} from '../e2e/helpers/bounded-handshake.mjs';

const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
function clock(){const ready=deferred(),cancelled=[];let callback,delay;return {ready:ready.promise,cancelled,schedule:(fn,ms)=>{callback=fn;delay=ms;ready.resolve();return 9;},cancel:id=>cancelled.push(id),fire:()=>callback(),get delay(){return delay;}};}

test('handshake persists a checkpoint before starting work and records completion without changing its result',async()=>{
  const directory=await fs.mkdtemp(path.join(os.tmpdir(),'bootstrap-handshake-'));
  const file=path.join(directory,'progress.json'),timer=clock();
  const run=boundedHandshake({checkpoint:entry=>fs.writeFile(file,JSON.stringify(entry)),schedule:timer.schedule,cancel:timer.cancel});
  try{
    const result=await run('records-before',async()=>{
      assert.deepEqual(JSON.parse(await fs.readFile(file,'utf8')),{phase:'records-before',state:'started'});
      return {unchanged:'value'};
    });
    assert.deepEqual(result,{unchanged:'value'});
    assert.deepEqual(JSON.parse(await fs.readFile(file,'utf8')),{phase:'records-before',state:'complete'});
    assert.equal(timer.delay,30000);assert.deepEqual(timer.cancelled,[9]);
  }finally{await fs.rm(directory,{recursive:true});}
});

test('stuck handshake rejects at the existing budget, records failure, clears timer and never converts late completion to success',async()=>{
  const entries=[],timer=clock(),held=deferred();
  const run=boundedHandshake({checkpoint:async entry=>entries.push(entry),schedule:timer.schedule,cancel:timer.cancel});
  const pending=run('late-response-terminal',()=>held.promise);
  await timer.ready;timer.fire();
  await assert.rejects(pending,/Bootstrap recovery handshake timed out: late-response-terminal/);
  assert.deepEqual(entries,[{phase:'late-response-terminal',state:'started'},{phase:'late-response-terminal',state:'failed',timedOut:true}]);
  assert.deepEqual(timer.cancelled,[9]);held.resolve('late');await Promise.resolve();
  assert.equal(entries.length,2);
});

test('original handshake error survives failed failure-checkpoint writes and always clears its timer',async()=>{
  const original=Error('original assertion'),timer=clock(),entries=[];
  const run=boundedHandshake({checkpoint:async entry=>{entries.push(entry);if(entry.state==='failed')throw Error('artifact write failed');},schedule:timer.schedule,cancel:timer.cancel});
  await assert.rejects(run('late-records',()=>{throw original;}),error=>error===original);
  assert.deepEqual(timer.cancelled,[9]);assert.equal(entries.at(-1).timedOut,false);
  await assert.rejects(run('arbitrary secret phase',()=>{}),/Unknown handshake phase/);
  assert.equal(entries.length,2);
});

test('default handshake timers use the native global receiver for both success and timeout',async()=>{
  const source=(await fs.readFile(new URL('../e2e/helpers/bounded-handshake.mjs',import.meta.url),'utf8')).replace('export function boundedHandshake','function boundedHandshake');
  for(const outcome of ['success','timeout']){
    const ctx={};vm.runInNewContext(`
      globalThis.entries=[];globalThis.cancelled=[];
      globalThis.setTimeout=function(fn,ms){'use strict';if(this!==globalThis)throw TypeError('Illegal invocation');globalThis.fire=fn;globalThis.delay=ms;return 17;};
      globalThis.clearTimeout=function(id){'use strict';if(this!==globalThis)throw TypeError('Illegal invocation');globalThis.cancelled.push(id);};
      ${source}
      globalThis.run=boundedHandshake({checkpoint:async entry=>globalThis.entries.push(entry)});
    `,ctx);
    const work=deferred(),pending=ctx.run('warmup-entered',()=>work.promise);
    await Promise.resolve();await Promise.resolve();
    assert.equal(ctx.delay,30000);
    if(outcome==='success'){work.resolve('exact');assert.equal(await pending,'exact');}
    else{ctx.fire();await assert.rejects(pending,/timed out/);work.resolve();}
    assert.deepEqual([...ctx.cancelled],[17]);
    assert.equal(ctx.entries.at(-1).state,outcome==='success'?'complete':'failed');
  }
});
