import test from 'node:test';
import assert from 'node:assert/strict';
import {EncryptedRecordStore} from '../../src/greenhouse_owner_os/web/pilot-storage.js';
import {inspectOwnerDrafts} from '../e2e/helpers/owner-draft-observer.mjs';
const sha='a'.repeat(40),privateMode='PRIVATE_DAILY_TEST',legacyMode='CONNECTED_TEST';
const secret=()=>Buffer.from(crypto.getRandomValues(new Uint8Array(32))).toString('base64url');
async function fixture(mode=privateMode){
 const config={entity_id:'entity',owner_id:'owner',feature_sha:sha,environment:'TEST_PREVIEW',backend_connected:true,actual_operations:false,...(mode===privateMode?{daily_pilot:true,participant_id:'participant'}:{})};
 const scope='entity:owner'+(mode===privateMode?':participant':''),id='quick/receipt',value={id:'form',file:{filename:'PRIVATE original.png',file_base64:Buffer.from('EXACT ORIGINAL BYTES').toString('base64')},plan_preview:{cards:[{component:'private component'}],totals:[{quantity:'50'}]}};
 const record={key:scope+':draft:'+id,scope,type:'draft',id,value};
 const records=[record,{...record,key:scope+':draft:other',id:'other'},{...record,key:'foreign:draft:'+id,scope:'foreign'}];
 const localKey={participant_id:'participant',algorithm:'AES-GCM',key:secret()},identity={mode:privateMode,role:'owner',synthetic_only:true,participant_id:'participant',entity_id:'entity',actor_id:'owner',build_sha:sha};
 const store=new EncryptedRecordStore({all:async()=>[]},'participant',localKey.key);
 const rows=mode===privateMode?await Promise.all(records.map(row=>store.envelope(row))):records;
 return {input:{rows,config,id,expectedSha:sha,mode},providers:{session:async()=>identity,key:async()=>localKey},record,identity,localKey};
}

for(const mode of [legacyMode,privateMode])test(mode+' observer preserves one exact scoped original and mode-specific raw visibility',async()=>{
 const f=await fixture(mode),before=JSON.stringify(f.input.rows),result=await inspectOwnerDrafts(f.input,mode===privateMode?f.providers:{session(){throw Error('Unexpected private authentication');},key(){throw Error('Unexpected private key');}});
 assert.equal(result.drafts.length,1);assert.deepEqual(result.drafts[0],f.record);assert.deepEqual(Buffer.from(result.drafts[0].value.file.file_base64,'base64'),Buffer.from('EXACT ORIGINAL BYTES'));
 assert.deepEqual(result.drafts[0].value.plan_preview.totals,[{quantity:'50'}]);assert.equal(result.raw.includes(f.record.value.file.filename),mode===legacyMode);assert.equal(result.raw.includes(f.record.value.file.file_base64),mode===legacyMode);assert.equal(result.raw.includes('"plan_preview"'),mode===legacyMode);
 assert.equal(JSON.stringify(f.input.rows),before);assert.ok(!JSON.stringify(result).includes(f.localKey.key));assert.deepEqual(Object.keys(result).sort(),['drafts','raw']);
});

for(const field of ['mode','role','synthetic_only','participant_id','entity_id','actor_id','build_sha'])test('private observer rejects mismatched authenticated '+field+' before obtaining key',async()=>{
 const f=await fixture();f.identity[field]=field==='synthetic_only'?false:'foreign';let keyRead=false;f.providers.key=async()=>{keyRead=true;return f.localKey;};
 await assert.rejects(inspectOwnerDrafts(f.input,f.providers));assert.equal(keyRead,false);
});

for(const field of ['participant_id','algorithm','key'])test('private observer rejects mismatched or malformed key '+field+' without exposing key material',async()=>{
 const f=await fixture();f.localKey[field]=field==='key'?'SENSITIVE INVALID KEY':'foreign';
 await assert.rejects(inspectOwnerDrafts(f.input,f.providers),error=>!error.message.includes('SENSITIVE INVALID KEY'));
});

test('private observer rejects incorrect valid key and plaintext legacy storage rather than falling back',async()=>{
 const f=await fixture();f.localKey.key=secret();await assert.rejects(inspectOwnerDrafts(f.input,f.providers),/локальную копию/);
 const plain=await fixture();plain.input.rows=[plain.record];await assert.rejects(inspectOwnerDrafts(plain.input,plain.providers),/незашифрованные/);
});

for(const change of ['unknown-mode','config-mode','sha','missing-participant','environment','operations'])test('observer rejects invalid evidence boundary '+change,async()=>{
 const f=await fixture();
 if(change==='unknown-mode')f.input.mode='UNKNOWN';
 if(change==='config-mode')f.input.config.daily_pilot=false;
 if(change==='sha')f.input.expectedSha='b'.repeat(40);
 if(change==='missing-participant')delete f.input.config.participant_id;
 if(change==='environment')f.input.config.environment='PRODUCTION';
 if(change==='operations')f.input.config.actual_operations=true;
 await assert.rejects(inspectOwnerDrafts(f.input,f.providers));
});

test('observer excludes other participants even when they have the same logical route',async()=>{
 const f=await fixture(),other=new EncryptedRecordStore({all:async()=>[]},'different-participant',secret());f.input.rows.push(await other.envelope(f.record));
 const result=await inspectOwnerDrafts(f.input,f.providers);assert.equal(result.drafts.length,1);assert.deepEqual(result.drafts[0],f.record);
});
