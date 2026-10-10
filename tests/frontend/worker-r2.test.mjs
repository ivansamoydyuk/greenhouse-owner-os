import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {R2_CONTRACT,responseSnapshot,responseKey,pendingResponse,responseAlreadyStored,r2Controls,r2History} from '../../src/greenhouse_owner_os/web/worker-r2.js';
const task={id:'task',cycle:2,execution_contract:R2_CONTRACT,status:'assigned',reports:[]};
const assignment={id:'assignment',cycle:2,acknowledged_at:null};
const worker={id:'worker',entity_id:'entity'};
const make=(response='PROBLEM',a=assignment)=>responseSnapshot(task,a,response,'ru','<script>exact & note</script>','response-id','2026-10-10T21:00:00Z');
test('worker button is exact typed original and comment remains unchanged',()=>{
 const body=make();assert.deepEqual(body,{client_response_id:'response-id',assignment_id:'assignment',expected_cycle:2,response:'PROBLEM',captured_at:'2026-10-10T21:00:00Z',button_label:'Проблема',original_language:'ru',comment:'<script>exact & note</script>'});
});
test('problem requires no fabricated receipt, DONE requires receipt and current assignment',()=>{
 assert.equal(make().response,'PROBLEM');assert.throws(()=>make('DONE'),/Сначала/);assert.equal(make('DONE',{...assignment,acknowledged_at:'now'}).response,'DONE');assert.throws(()=>make('PROBLEM',{...assignment,cycle:1}),/Назначение/);
});
test('old R1 contract is not silently converted to new response flow',()=>assert.throws(()=>responseSnapshot({...task,execution_contract:'R1_STARTED_REPORT'},assignment,'PROBLEM','ru'),/Назначение/));
test('pending identity separates worker entity task assignment and cycle',()=>{
 const key=responseKey(worker,task,assignment);for(const [w,t,a] of [[{...worker,id:'other'},task,assignment],[{...worker,entity_id:'other'},task,assignment],[worker,{...task,cycle:3},assignment],[worker,task,{...assignment,id:'other'}]])assert.notEqual(responseKey(w,t,a),key);
});
test('reload restores same response UUID captured time and comment',()=>{
 const body=make(),storage={getItem:()=>JSON.stringify(body)};assert.deepEqual(pendingResponse(storage,'k',task,assignment),body);assert.throws(()=>pendingResponse(storage,'k',{...task,cycle:3},assignment),/версии/);
});
test('only exact response identity resolves uncertain result',()=>{
 const body=make(),history={...task,response_history:[{client_response_id:body.client_response_id,assignment_id:assignment.id,cycle:2}]};assert.equal(responseAlreadyStored(history,body),true);assert.equal(responseAlreadyStored(history,{...body,client_response_id:'other'}),false);
});
test('pending UI offers same retry and refresh, never new result buttons',()=>{
 const html=r2Controls(task,assignment,'ru',make());assert.match(html,/worker-r2-retry/);assert.match(html,/worker-r2-refresh/);assert.doesNotMatch(html,/data-r2-response/);assert.doesNotMatch(html,/<script>/);
});
test('R2 exposes received done problem and no start; done remains disabled until received',()=>{
 const html=r2Controls(task,assignment,'ru',null);assert.match(html,/data-r2-response="DONE" class="primary" disabled/);assert.match(html,/data-r2-response="PROBLEM"/);assert.doesNotMatch(html,/worker-start/);
});
test('history preserves prior cycles without counting them as current and escapes exact source',()=>{
 const html=r2History({...task,response_history:[{...make(),cycle:1,button_label:'<img src=x>',comment:'<script>x</script>'}]});assert.match(html,/Предыдущий цикл/);assert.doesNotMatch(html,/<img|<script/);
});
test('Dari manual instruction button labels retained without pretending translation verified',()=>{
 const body=responseSnapshot(task,assignment,'PROBLEM','fa','۵ لیتر نریزید','id','2026-10-10T00:00:00Z');assert.equal(body.button_label,'مشکل');assert.equal(body.comment,'۵ لیتر نریزید');assert.equal(body.original_language,'fa');
});
test('owner new task declares R2 and completion uses trusted server guard',()=>{
 const src=fs.readFileSync('prototype/owner-r3/connected.js','utf8');assert.match(src,/execution_contract: "R2_RECEIVED_REPORT"/);assert.match(src,/t.can_complete \? btn/);assert.match(src,/Исходные ответы работников/);
});

test('owner review attributes each report to a worker and explicit current or prior cycle',()=>{
 const src=fs.readFileSync('prototype/owner-r3/connected.js','utf8');assert.match(src,/workerName\(r.worker_id\)/);assert.match(src,/cycleLabel\(r.cycle\)/);assert.match(src,/workerName\(x.worker_id\)/);assert.match(src,/Идентичность источника/);
});
