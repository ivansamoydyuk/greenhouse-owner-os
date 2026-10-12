import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import {ConnectedAPI} from '../../prototype/owner-r3/connected-adapter.js';
import {FertilizerDraftWrites} from '../../prototype/owner-r3/fertilizer-draft-writes.js';
import {EncryptedRecordStore} from '../../src/greenhouse_owner_os/web/pilot-storage.js';
const deferred=()=>{let resolve;const promise=new Promise(done=>resolve=done);return {promise,resolve};};

test('actual owner input and unload handlers retain pending multiline fields, coalesce encrypted writes, and recover exact acknowledged draft',async()=>{
  const rows=new Map(),raw={all:async()=>[...rows.values()],get:async key=>rows.get(key),put:async row=>rows.set(row.key,row)};
  const secret=Buffer.from(crypto.getRandomValues(new Uint8Array(32))).toString('base64url');
  const store=new EncryptedRecordStore(raw,'owner-A',secret),api=new ConnectedAPI({store});
  api.configure({entity_id:'e',owner_id:'owner',feature_sha:'sha',environment:'TEST_PREVIEW',backend_connected:true,actual_operations:false});
  const route='attach-depot/report',draft={id:'draft',line_count:'2',file:{filename:'original.png',file_base64:'PRIVATE ORIGINAL'}};
  await api.draft(route,draft);
  const gates=[deferred(),deferred()],started=[deferred(),deferred()],seen=[];
  raw.put=async row=>{const index=seen.length;seen.push(row);started[index].resolve();await gates[index].promise;rows.set(row.key,row);};
  const states=[],errors=[],writes=new FertilizerDraftWrites(api,(_,state)=>states.push(state));
  const handlers={},form={dataset:{form:'depot'}},fields={line_1_quality:'İkinci',line_1_quality_category:'SECOND'};
  const context={draft,route,ownerEditSequence:0,fertilizerWrites:writes,clearFieldValidation(){},values:()=>({...fields}),showError:error=>errors.push(error),
    document:{addEventListener:(name,handler)=>handlers[name]=handler},window:{addEventListener:(name,handler)=>handlers[name]=handler}};
  const source=readFileSync(new URL('../../prototype/owner-r3/connected.js',import.meta.url),'utf8');
  vm.runInNewContext(source.slice(source.indexOf('document.addEventListener("input"'),source.indexOf('document.addEventListener("change"')),context);
  vm.runInNewContext(source.split('\n').find(line=>line.startsWith("window.addEventListener('beforeunload'")),context);
  const input=name=>handlers.input({target:{name,type:'text',closest:()=>form}});
  input('line_1_quality');await started[0].promise;
  for(const [name,value] of [['line_1_kg','300'],['line_1_price','50'],['line_1_count','33'],['stated_total','45000']]){fields[name]=value;input(name);}
  assert.equal(context.ownerEditSequence,5);assert.equal(states.at(-1),'saving');let prevented=false;const event={preventDefault:()=>prevented=true};handlers.beforeunload(event);
  assert.equal(prevented,true);assert.equal(event.returnValue,'');assert.equal(draft.line_1_kg,'300');assert.equal((await api.readDraft(route)).line_1_kg,undefined);
  gates[0].resolve();await started[1].promise;
  assert.equal(states.includes('saved'),false);assert.equal(seen.length,2);assert.ok(!JSON.stringify(seen).includes('PRIVATE ORIGINAL'));
  gates[1].resolve();await writes.flush(route);assert.equal(states.at(-1),'saved');assert.deepEqual(errors,[]);
  prevented=false;handlers.beforeunload(event);assert.equal(prevented,false);
  const reloaded=new ConnectedAPI({store:new EncryptedRecordStore(raw,'owner-A',secret)});reloaded.configure({entity_id:'e',owner_id:'owner',feature_sha:'sha',environment:'TEST_PREVIEW',backend_connected:true,actual_operations:false});
  const recovered=await reloaded.readDraft(route);assert.equal(recovered.line_1_kg,'300');assert.equal(recovered.line_1_price,'50');assert.equal(recovered.line_1_count,'33');assert.equal(recovered.stated_total,'45000');assert.equal(recovered.file.file_base64,'PRIVATE ORIGINAL');
  const other=new EncryptedRecordStore(raw,'owner-B',secret);assert.equal(await other.get(`${api.scope}:draft:${route}`),undefined);
});
