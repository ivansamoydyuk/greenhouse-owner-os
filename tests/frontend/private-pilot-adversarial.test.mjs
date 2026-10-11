import test from 'node:test';
import assert from 'node:assert/strict';
import {PilotCipher,EncryptedRecordStore,EncryptedWorkerDraftStore} from '../../src/greenhouse_owner_os/web/pilot-storage.js';
const key=n=>Buffer.alloc(32,n).toString('base64url');
function disk(){const m=new Map();return {m,get length(){return m.size},key:i=>[...m.keys()][i],getItem:k=>m.get(k)??null,setItem:(k,v)=>m.set(k,v),removeItem:k=>m.delete(k)};}
function records(){const m=new Map();return {m,put:async r=>m.set(r.key,structuredClone(r)),get:async k=>m.get(k),all:async()=>[...m.values()],delete:async k=>m.delete(k),claim:async r=>{const old=m.get(r.key);if(!old||old.state==='REJECTED'){m.set(r.key,structuredClone(r));return r;}return old;}};}

test('independent encryption: owner pending body and ACK are opaque at rest',async()=>{
 const raw=records(),store=new EncryptedRecordStore(raw,'owner-A',key(1));
 const logical='owner-A:request:SECRET_PURPOSE';
 const input={key:logical,scope:'owner-A',state:'ACK',body:{description:'PRIVATE_8500_насос'},result:{original:'PRIVATE_135_веток'}};
 await store.put(input);
 const bytes=JSON.stringify([...raw.m]);
 for(const value of ['SECRET_PURPOSE','PRIVATE_8500_насос','PRIVATE_135_веток',key(1)])assert(!bytes.includes(value));
 assert.deepEqual(await new EncryptedRecordStore(raw,'owner-A',key(1)).get(logical),input);
 assert.equal(await new EncryptedRecordStore(raw,'worker-B',key(2)).get(logical),undefined);
});

test('independent cipher rejects wrong key, participant and record transplant',async()=>{
 const cipher=new PilotCipher('A',key(3)),record=await cipher.seal('draft-A',{text:'точный оригинал'});
 assert.deepEqual((await cipher.open(record)).value,{text:'точный оригинал'});
 await assert.rejects(new PilotCipher('A',key(4)).open(record));
 await assert.rejects(new PilotCipher('B',key(3)).open(record));
 await assert.rejects(cipher.open({...record,key:await cipher.address('draft-B')}));
 const again=await cipher.seal('draft-A',{text:'точный оригинал'});
 assert.notEqual(record.iv,again.iv);assert.notEqual(record.ciphertext,again.ciphertext);
});

test('independent encrypted photo-sized draft survives exact round trip',async()=>{
 const cipher=new PilotCipher('photo-owner',key(5));
 const value={filename:'TEST-image.jpg',file_base64:'X'.repeat(500000),note:'Дари متن'};
 const encoded=await cipher.seal('photo',value);
 assert.deepEqual((await cipher.open(encoded)).value,value);
});

test('independent parallel encrypted claims retain one immutable pending identity',async()=>{
 const raw=records(),a=new EncryptedRecordStore(raw,'owner',key(6)),b=new EncryptedRecordStore(raw,'owner',key(6));
 const [one,two]=await Promise.all([a.claim({key:'intent',state:'PENDING',body:{amount:'8500'}}),b.claim({key:'intent',state:'PENDING',body:{amount:'9999'}})]);
 assert.deepEqual(one,two);assert.equal(raw.m.size,1);
});

test('independent worker logical keys and form fields never persist plaintext',async()=>{
 const raw=disk(),store=await EncryptedWorkerDraftStore.create(raw,'worker-A',key(7));
 const logical='worker-write:/responses:{"text":"SECRET_WORKER_REPORT"}';
 store.setItem(logical,'PRIVATE_INTENT');store.setItem('form','PRIVATE_FORM متن');await store.flush();
 const serialized=JSON.stringify([...raw.m]);
 for(const value of ['SECRET_WORKER_REPORT','PRIVATE_INTENT','PRIVATE_FORM',key(7)])assert(!serialized.includes(value));
 const restored=await EncryptedWorkerDraftStore.create(raw,'worker-A',key(7));
 assert.equal(restored.getItem(logical),'PRIVATE_INTENT');assert.equal(restored.getItem('form'),'PRIVATE_FORM متن');
 assert.equal((await EncryptedWorkerDraftStore.create(raw,'worker-B',key(8))).getItem('form'),null);
});

test('independent failed encrypted save stays pending and recovers latest exact draft',async()=>{
 const raw=disk(),write=raw.setItem;let fail=true;raw.setItem=(k,v)=>{if(fail)throw Error('quota');write(k,v)};
 const store=await EncryptedWorkerDraftStore.create(raw,'worker',key(9));
 await assert.rejects(store.setItem('report','first'));assert.equal(store.failed,true);
 await assert.rejects(store.setItem('other','other'));assert.equal(store.failed,true);
 let protectedPage=false;store.protectUnload({preventDefault(){protectedPage=true}});assert(protectedPage);
 fail=false;store.setItem('report','latest');await store.flush();assert.equal(store.failed,false);
 const restored=await EncryptedWorkerDraftStore.create(raw,'worker',key(9));assert.equal(restored.getItem('report'),'latest');
});

test('independent delayed old-account ACK never encrypts old facts for new account',async()=>{
 const {ConnectedAPI}=await import('../../prototype/owner-r3/connected-adapter.js');
 const raw=records();let release,entered;const started=new Promise(r=>entered=r),response=new Promise(r=>release=r);
 const api=new ConnectedAPI({store:raw,fetcher:async()=>{entered();await response;return {ok:true,json:async()=>({private:'OWNER_A_SECRET'})}}});
 const config=owner=>({entity_id:'e',owner_id:owner,feature_sha:'sha',environment:'TEST_PREVIEW',backend_connected:true,actual_operations:false});
 api.configure(config('A'));const a=new EncryptedRecordStore(raw,'A',key(10));api.store=a;
 const pending=api.mutate('/v1/example',{private:'OWNER_A_BODY'},'intent');const rejected=assert.rejects(pending,/сессии|Сессия/);await started;
 api.configure(config('B'));const b=new EncryptedRecordStore(raw,'B',key(11));api.store=b;release();await rejected;
 assert.deepEqual(await b.all(),[]);
 const old=await a.all();assert.equal(old.length,1);assert.equal(old[0].body.private,'OWNER_A_BODY');
});

test('independent dirty legacy origin blocks without deleting original draft',async()=>{
 const {assertNoLegacyDrafts}=await import('../../src/greenhouse_owner_os/web/pilot-storage.js');
 const old={key:'entity:owner:draft:expense',value:{text:'LEGACY_ORIGINAL'}};
 const saved=structuredClone(old),raw=records();raw.m.set(old.key,old);
 const current=new EncryptedRecordStore(raw,'new-participant',key(12));
 await assert.rejects(current.all(),/незашифрованные черновики/);assert.deepEqual(raw.m.get(old.key),saved);
 const legacy=disk();legacy.setItem('worker-write:LEGACY_REPORT','OLD_ID');
 assert.throws(()=>assertNoLegacyDrafts([],[legacy]),/незашифрованные черновики/);assert.equal(legacy.getItem('worker-write:LEGACY_REPORT'),'OLD_ID');
});
