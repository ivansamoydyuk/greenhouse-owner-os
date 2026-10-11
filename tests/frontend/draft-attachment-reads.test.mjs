import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {DraftAttachmentReads} from '../../prototype/owner-r3/connected-adapter.js';
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
test('pending selected original survives a cloned product submission',async()=>{
 const wait=deferred(),queue=new DraftAttachmentReads(()=>wait.promise),draft={id:'receipt',quantity:'100',note:'known'};
 const file={name:'source.png'};queue.begin('owner','quick/receipt',draft,file);
 const clone=structuredClone(draft),pending=queue.merge('owner','quick/receipt',clone);
 wait.resolve({filename:'source.png',file_base64:'exact'});await pending;
 assert.equal(clone.file.file_base64,'exact');assert.equal(clone.quantity,'100');assert.equal(clone.note,'known');
});
test('newest selected original wins even when old read finishes last',async()=>{
 const a=deferred(),b=deferred(),queue=new DraftAttachmentReads(file=>file===1?a.promise:b.promise),draft={id:'same'};
 queue.begin('scope','route',draft,1);const merged=queue.merge('scope','route',draft);
 queue.begin('scope','route',draft,2);b.resolve({filename:'new',file_base64:'newbytes'});a.resolve({filename:'old',file_base64:'oldbytes'});
 await merged;assert.equal(draft.file.filename,'new');
});
test('failed current original blocks submission instead of becoming absent',async()=>{
 const q=new DraftAttachmentReads(()=>Promise.reject(Error('read failed'))),draft={id:'r'};
 await assert.rejects(q.merge('scope','route',draft,{name:'bad'}),/read failed/);assert.equal(draft.file,undefined);
});
test('owner, route and draft identity cannot borrow another original',async()=>{
 const q=new DraftAttachmentReads(async()=>({filename:'private'}));await q.merge('owner1','receipt',{id:'one'},{});
 for(const [scope,route,id] of [['owner2','receipt','one'],['owner1','other','one'],['owner1','receipt','two']]){
  const d={id};await q.merge(scope,route,d);assert.equal(d.file,undefined);
 }
});
test('file capture does not use busy-dropping action and fences session before persistence',()=>{
 const ui=fs.readFileSync('prototype/owner-r3/connected.js','utf8');const file=ui.slice(ui.indexOf('if (event.target.name === "file" && draft)'),ui.indexOf('event.target.name === "worker"'));
 assert.ok(!file.includes('action(async'));assert.ok(file.includes('attachmentReads.begin'));
 assert.ok(file.indexOf('scope !== api.scope || generation !== api.generation')<file.indexOf('await api.draft(savedRoute, target)'));
 assert.ok(ui.includes('await attachmentReads.merge(scope, route, draft, file)'));
 const save=ui.slice(ui.indexOf('async function saveDraft('),ui.indexOf('async function clearDraft('));assert.ok(save.indexOf('generation !== api.generation')<save.indexOf('await fertilizerWrites.enqueue(route, draft)'));
 assert.ok(ui.includes('latestReceipt?.id !== submittedDraft.id'));assert.ok(!ui.includes('latestReceipt : submittedDraft'));
});
test('actual saveDraft rejects session switch during selected-file read before any persistence',async()=>{
 const ui=fs.readFileSync('prototype/owner-r3/connected.js','utf8');
 const source=ui.slice(ui.indexOf('function assertSubmissionScope('),ui.indexOf('async function clearDraft('));
 const wait=deferred(),queue=new DraftAttachmentReads(()=>wait.promise),writes=[];
 const api={scope:'owner1',generation:1,draft:async(...args)=>writes.push(args)};
 const save=new Function('api','attachmentReads',source+'; return saveDraft;')(api,queue);
 const snapshot={scope:'owner1',generation:1,route:'quick/receipt',draft:{id:'old'},fields:{quantity:'100'},file:{name:'private.png'}};
 const pending=save(snapshot);api.scope='owner2';api.generation=2;wait.resolve({filename:'private.png',file_base64:'private'});
 await assert.rejects(pending,/Сессия изменилась/);assert.deepEqual(writes,[]);
});
test('completed draft invalidates a late file callback without reviving it',async()=>{
 const wait=deferred(),q=new DraftAttachmentReads(()=>wait.promise),file={name:'old'},draft={id:'old'};
 const reading=q.begin('owner','receipt',draft,file);q.clear('owner','receipt','old');wait.resolve({filename:'old'});await reading;
 assert.equal(q.current('owner','receipt','old',file),false);
 const fresh={id:'new'};await q.merge('owner','receipt',fresh);assert.equal(fresh.file,undefined);
 const ui=fs.readFileSync('prototype/owner-r3/connected.js','utf8');assert.ok(ui.includes('stored?.id !== savedDraft.id'));assert.ok(ui.includes('attachmentReads.clear(scope, id, stored.id)'));
});
