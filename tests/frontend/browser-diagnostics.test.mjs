import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs/promises';
import path from 'node:path';
import {metadataRing,safePath,observeBrowserDiagnostics} from '../e2e/helpers/browser-diagnostics.mjs';

test('primary browser manifest is saved before optional diagnostics and preserves the original failure',async()=>{
 const source=await fs.readFile(new URL('../e2e/r3-connected.mjs',import.meta.url),'utf8');
 const finallyBody=source.slice(source.lastIndexOf('} finally {')+'} finally {'.length,-2);
 for(const failure of ['capture','write',null]){
  const original=Error('original browser failure'),writes=[];
  const result={sha:'test-sha',status:'FAIL',errors:[{message:original.message}]};
  const context={original,result,path,out:'/evidence',diagnostics:[{engine:'chromium',observer:{capture:async()=>{
   assert.deepEqual(writes.map(x=>path.basename(x.file)),['browser-results.json']);
   if(failure==='capture')throw Error('diagnostic capture failed');
   return {network:{entries:[]}};
  }}}],fs:{writeFile:async(file,content)=>{
   writes.push({file,content});
   if(failure==='write'&&file.endsWith('browser-diagnostics.json'))throw Error('diagnostic write failed');
  }}};
  await assert.rejects(vm.runInNewContext('(async()=>{try{throw original;}finally{'+finallyBody+'}})()',context),error=>error===original);
  assert.deepEqual(JSON.parse(writes[0].content),result);
  assert.equal(writes.length,failure==='capture'?1:2);
 }
});

test('diagnostic allowlist strips credentials, origin, query, fragments, headers, bodies and arbitrary fields',()=>{
 const ring=metadataRing(3);ring.record({event:'response',id:1,atMs:10,durationMs:5,status:200,method:'GET',url:'https://USER:SECRET@private.example/pilot/draft-key?token=SECRET#SECRET',headers:{Authorization:'SECRET'},body:{key:'SECRET'},cookie:'SECRET',failure:'SECRET',phase:'SECRET'});
 assert.deepEqual(ring.snapshot().entries,[{event:'response',id:1,atMs:10,durationMs:5,status:200,path:'/pilot/draft-key',method:'GET'}]);assert.ok(!JSON.stringify(ring.snapshot()).includes('SECRET'));
 assert.equal(safePath('https://host/unknown/SECRET?token=SECRET'),'/other');assert.equal(safePath('not a URL SECRET'),'/invalid');
});

test('network metadata ring is bounded, counts dropped events and returns isolated copies',()=>{
 const ring=metadataRing(3);for(let id=1;id<=10;id++)ring.record({event:'request',id,atMs:id,url:'http://host/preview/config'});
 const snapshot=ring.snapshot();assert.equal(snapshot.dropped,7);assert.deepEqual(snapshot.entries.map(x=>x.id),[8,9,10]);snapshot.entries[0].path='changed';assert.equal(ring.snapshot().entries[0].path,'/preview/config');
 for(const size of [0,-1,2001,1.5])assert.throws(()=>metadataRing(size));
});

test('metadata records explicit generation mismatch without arbitrary stage values',()=>{
 const ring=metadataRing();ring.record({event:'checkpoint',phase:'generation-mismatch',generation:2,atMs:30});ring.record({event:'checkpoint',phase:'PRIVATE DATA',generation:NaN});
 assert.deepEqual(ring.snapshot().entries,[{event:'checkpoint',atMs:30,generation:2,phase:'generation-mismatch'},{event:'checkpoint'}]);
});

test('browser observer preserves storage calls and retains only bounded transaction lifecycle metadata',async()=>{
 const listeners={},requests=[],transactions=[];let init,clock=0;
 class Factory {open(...args){requests.push(args);return new EventTarget();}}
 class Database {constructor(){this.name='r3-connected-test-v1';}transaction(...args){transactions.push(args);return Object.assign(new EventTarget(),{mode:args[1]});}}
 const ctx={IDBFactory:Factory,IDBDatabase:Database,window:{},performance:{now:()=>++clock}};
 const page={on:(name,callback)=>listeners[name]=callback,addInitScript:async callback=>init=callback,evaluate:async()=>({phase:'harvest-navigation-read',generation:1,rendered:false,storage:ctx.window.__testStorageDiagnostics.snapshot()})};
 const observer=await observeBrowserDiagnostics(page);vm.runInNewContext('('+init.toString()+')()',ctx);
 const request=new Factory().open('r3-connected-test-v1',1);request.dispatchEvent(new Event('success'));
 const db=new Database();for(let i=0;i<70;i++){const tx=db.transaction(['records','PRIVATE STORE NAME'],'readonly');tx.privateValue='SECRET';tx.dispatchEvent(new Event('complete'));}
 assert.deepEqual(requests,[['r3-connected-test-v1',1]]);assert.equal(transactions.length,70);assert.deepEqual(transactions[0],[['records','PRIVATE STORE NAME'],'readonly']);
 const network={url:()=> 'http://host/pilot/draft-key?token=SECRET',method:()=> 'GET'};listeners.request(network);listeners.response({request:()=>network,status:()=>200});listeners.requestfinished(network);observer.checkpoint('payment-reload-return');
 const result=await observer.capture();assert.equal(result.storage.entries.length,100);assert.equal(result.storage.dropped,42);assert.equal(result.bootstrap.phase,'harvest-navigation-read');assert.equal(result.rendered,false);assert.deepEqual(result.network.entries.map(x=>x.event),['request','response','finished','checkpoint']);assert.ok(!JSON.stringify(result).includes('SECRET'));assert.ok(!JSON.stringify(result).includes('PRIVATE STORE NAME'));
});
