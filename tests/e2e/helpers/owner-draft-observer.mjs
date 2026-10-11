import assert from 'node:assert/strict';
import {EncryptedRecordStore} from '../../../src/greenhouse_owner_os/web/pilot-storage.js';
import {pilotMode,ownerDraftScope} from './pilot-auth.mjs';

// Observe a durable snapshot without changing storage. The authenticated key
// remains local to this call and is never returned or written to test evidence.
export async function inspectOwnerDrafts({rows,config,id,expectedSha,mode},{session,key}={}){
 assert.ok(['CONNECTED_TEST','PRIVATE_DAILY_TEST'].includes(mode),'Unknown browser evidence mode');
 assert.ok(/^[0-9a-f]{40}$/.test(expectedSha||''),'Exact browser evidence SHA required');
 assert.equal(config.feature_sha,expectedSha);assert.equal(config.environment,'TEST_PREVIEW');
 assert.equal(config.backend_connected,true);assert.equal(config.actual_operations,false);
 assert.ok(config.entity_id&&config.owner_id&&id,'Authenticated owner draft scope required');
 const privateMode=mode==='PRIVATE_DAILY_TEST';
 assert.ok(privateMode?config.daily_pilot===true:config.daily_pilot===undefined||config.daily_pilot===false,'Browser evidence mode does not match authenticated config');
 const raw=JSON.stringify(rows);let records=rows;
 if(privateMode){
  assert.ok(config.participant_id,'Authenticated participant required');
  const identity=await session();
  assert.equal(identity.mode,'PRIVATE_DAILY_TEST');assert.equal(identity.role,'owner');assert.equal(identity.synthetic_only,true);
  assert.equal(identity.participant_id,config.participant_id);assert.equal(identity.entity_id,config.entity_id);assert.equal(identity.actor_id,config.owner_id);assert.equal(identity.build_sha,expectedSha);
  const localKey=await key();
  assert.equal(localKey.participant_id,config.participant_id);assert.equal(localKey.algorithm,'AES-GCM');
  assert.ok(/^[A-Za-z0-9_-]{43}$/.test(localKey.key||''),'Authenticated draft key shape required');
  records=await new EncryptedRecordStore({all:async()=>rows},config.participant_id,localKey.key).all();
 }
 const scope=ownerDraftScope(config);
 return {drafts:records.filter(record=>record.scope===scope&&record.type==='draft'&&record.id===id),raw};
}

export async function readOwnerDrafts(page,base,config,id,expectedSha){
 const rows=await page.evaluate(async()=>{const {BrowserStore}=await import('/r3/connected-adapter.js');return new BrowserStore().all();});
 const read=async endpoint=>{const response=await page.request.get(base+endpoint);assert.equal(response.status(),200,'Authenticated draft evidence read failed');return response.json();};
 return inspectOwnerDrafts({rows,config,id,expectedSha,mode:pilotMode?'PRIVATE_DAILY_TEST':'CONNECTED_TEST'},{session:()=>read('/pilot/session'),key:()=>read('/pilot/draft-key')});
}
