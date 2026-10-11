import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {btn as realButton} from '../../prototype/owner-r3/components.js';
import {ownerFormSource} from '../../prototype/owner-r3/connected-adapter.js';
const draft={id:'b034230a-678b-49be-9e63-d5dd4b35372a',captured_at:'2026-10-10T15:01:00+03:00'};
test('typed harvest original preserves lexical values without fabricating prose',()=>{
 const fields={event_date:'2026-10-10',original_greenhouse:'İmamlı',work_area_id:'part',cut_count:'00135',field_weight_kg:'',reporter:' Владелец ',notes:''};
 const source=ownerFormSource('field_harvest',draft,fields);
 assert.deepEqual(source.fields,fields);assert.equal(source.form_id,draft.id);assert.equal(source.captured_at,draft.captured_at);assert.equal(source.form_kind,'field_harvest');assert.equal(source.schema_version,1);assert.equal(source.timezone,'Europe/Istanbul');assert.equal('raw_text' in source,false);
});
test('typed receipt keeps exact decimal spelling, units and empty notes',()=>{
 const fields={event_date:'2026-10-10',product_id:'p',original_product_name:'KNO3',location_id:'l',quantity:'120,00',unit:'kg',notes:''};
 assert.deepEqual(ownerFormSource('inventory_receipt',draft,fields).fields,fields);
 assert.equal(ownerFormSource('inventory_receipt',draft,fields).fields.unit,'kg');
});
test('typed source has stable identity and excludes non-evidence draft state',()=>{
 const fields={quantity:'120',unit:'kg',payment:'false',stock_balance:'999'};
 const a=ownerFormSource('inventory_receipt',draft,fields);const b=ownerFormSource('inventory_receipt',structuredClone(draft),fields);
 assert.deepEqual(a,b);assert.equal('stock_balance' in a.fields,false);assert.equal('payment' in a.fields,false);
 assert.throws(()=>ownerFormSource('unknown',draft,fields));assert.throws(()=>ownerFormSource('field_harvest',{},{}));
});
test('connected form paths do not require redundant narrative',()=>{
 const ui=fs.readFileSync('prototype/owner-r3/connected.js','utf8');
 assert.ok(!ui.includes('requireText(draft.original, "исходную формулировку"'));
 assert.ok(!ui.includes('requireText(draft.original, "исходную запись о приходе"'));
 assert.ok(ui.includes('Количество веток'));
 assert.ok(ui.includes('owner_form:v1:field_harvest'));
 assert.ok(ui.includes('owner_form:v1:inventory_receipt'));
 assert.ok(ui.includes('capture_context: "quick_expense"'));
 assert.ok(ui.includes('formOriginal(r.original_evidence)'));
 assert.ok(ui.includes('formOriginal(r.evidence)'));
});
test('delayed validation focus cannot cross a route or session boundary',()=>{
 const ui=fs.readFileSync('prototype/owner-r3/connected.js','utf8');
 const body=ui.slice(ui.indexOf('function showError('),ui.indexOf('async function action('));
 let callback,focus=0;const field={isConnected:true,focus(){focus++},scrollIntoView(){}};
 const target={dataset:{}};const node={isConnected:true,inert:false,querySelector:s=>s==='.form-error'?target:field};
 const run=new Function('node','requestAnimationFrame','document',`${body}; let route='receipt', epoch=1; const api={generation:1}; showError({message:'Missing',field:'quantity'},node); return {route:()=>route='home',epoch:()=>epoch++,session:()=>api.generation++};`);
 for(const change of ['route','epoch','session']){const ctx=run(node,cb=>callback=cb,{});ctx[change]();callback();assert.equal(focus,0);}
 run(node,cb=>callback=cb,{});callback();assert.equal(focus,1);
 node.isConnected=false;run(node,cb=>callback=cb,{});callback();assert.equal(focus,1);
});

test('receipt comments remain source-only and cannot mask physical duplicate review',()=>{
 const ui=fs.readFileSync('prototype/owner-r3/connected.js','utf8');
 const receipt=ui.slice(ui.indexOf('} else if (["receipt","transfer"].includes(name))'),ui.indexOf('} else if (name === "product")'));
 assert.ok(receipt.includes('notes: null'));
 assert.ok(receipt.includes('notes: draft.original'));
 assert.ok(!receipt.includes('notes: draft.original || null'));
});

test('valid local field correction clears only its own validation message',()=>{
 const ui=fs.readFileSync('prototype/owner-r3/connected.js','utf8');
 const body=ui.slice(ui.indexOf('function clearFieldValidation('),ui.indexOf('document.addEventListener("input"'));
 const run=new Function('input','quantity',body+'; clearFieldValidation(input);');
 const error={textContent:'Укажите количество',dataset:{validationField:'quantity'}};
 const input={name:'quantity',value:'120',checkValidity:()=>true,closest:()=>({querySelector:()=>error})};
 run(input,v=>{assert.equal(v,'120')});assert.equal(error.textContent,'');
 error.textContent='Ответ сервера не получен';error.dataset.validationField='';run(input,()=>{});assert.equal(error.textContent,'Ответ сервера не получен');
 error.textContent='Укажите количество';error.dataset.validationField='quantity';input.value='0';run(input,()=>{throw Error('invalid')});assert.equal(error.textContent,'Укажите количество');
});
test('focused capture line is revealed only when actually obscured',()=>{
 const ui=fs.readFileSync('prototype/owner-r3/connected.js','utf8');
 const body=ui.slice(ui.indexOf('function revealFocusedCaptureField()'),ui.indexOf("document.addEventListener('focusin'"));
 const run=new Function('document','window','getComputedStyle','innerHeight',body+'; revealFocusedCaptureField();');
 let moved=0;let y=360;const field={matches:()=>true,closest:()=>true,tagName:'TEXTAREA',getBoundingClientRect:()=>({top:y,height:112})};
 const document={activeElement:field,querySelector:()=>({getBoundingClientRect:()=>({top:348})})};
 const window={visualViewport:{offsetTop:0,height:430},scrollBy:({top})=>moved=top};
 run(document,window,()=>({paddingTop:'12px',lineHeight:'20px'}),430);assert.ok(moved>0);
 moved=0;y=100;run(document,window,()=>({paddingTop:'12px',lineHeight:'20px'}),430);assert.equal(moved,0);
});

test('ordinary viewport scrolling never recenters the focused capture field',()=>{
 const ui=fs.readFileSync('prototype/owner-r3/connected.js','utf8');
 const body=ui.slice(ui.indexOf('function updateVisibleViewport()'),ui.indexOf('// Scroll only if'));
 const handlers={};let reveals=0;const window={visualViewport:{height:430,offsetTop:0,addEventListener:(name,fn)=>handlers[name]=fn}};
 const document={documentElement:{style:{setProperty(){}}}};
 new Function('window','document','requestAnimationFrame','revealFocusedCaptureField',body)(window,document,fn=>fn(),()=>reveals++);
 handlers.scroll();assert.equal(reveals,0);handlers.resize();assert.equal(reveals,1);handlers.scroll();assert.equal(reveals,1);
});

test('receipt confirmation readiness preserves critical and duplicate gates',()=>{
 const ui=fs.readFileSync('prototype/owner-r3/connected.js','utf8');
 const body=ui.slice(ui.indexOf('function receiptCanConfirm('),ui.indexOf('function syncReceiptConfirmation('));
 const ready=new Function('r','reviewed',body+'; return receiptCanConfirm(r,reviewed);');
 const ordinary={kind:'receipt',status:'draft',replacement_id:null,evidence:{verification:'VERIFY'},issues:['cost','review']};
 assert.equal(ready(ordinary,false),true);
 const duplicate={...ordinary,issues:['cost','review','duplicate']};assert.equal(ready(duplicate,false),false);assert.equal(ready(duplicate,true),true);
 for(const issue of ['product','quantity','unit','conversion','location','date','insufficient','unknown_new_issue'])assert.equal(ready({...ordinary,issues:[issue]},true),false);
 assert.equal(ready({...ordinary,evidence:{verification:'HISTORY'}},true),false);assert.equal(ready({...ordinary,replacement_id:'new'},true),false);
 assert.equal(ready({...ordinary,status:'posted'},true),false);assert.equal(ready({...ordinary,issues:undefined},true),false);
});

test('receipt review renders a real disabled attribute before duplicate review',()=>{
 const ui=fs.readFileSync('prototype/owner-r3/connected.js','utf8');
 const body=ui.slice(ui.indexOf('function receiptCanConfirm('),ui.indexOf('function personalList('));
 const render=new Function('r','btn',`const Header=()=>'', panel=x=>x, statuses={draft:'Черновик'}, stateTag=()=>'', facts=()=>'',displayDate=x=>x,formOriginal=()=>'',notice=()=>'',e=String,entityPath=x=>x,link=()=>'',data={inventory:{locations:[]}}; ${body}; return receiptDetail(r);`);
 const r={kind:'receipt',status:'draft',payload:{lines:[]},evidence:{verification:'VERIFY'},issues:['cost','review','duplicate'],issue_messages:[],movements:[]};
 const tag=render(r,realButton).match(/<button[^>]*data-action="post-receipt"[^>]*>/)[0];assert.match(tag,/\sdisabled(?:\s|>)/);assert.doesNotMatch(tag,/\strue(?:\s|>)/);
 const ordinary=render({...r,issues:['cost','review']},realButton).match(/<button[^>]*data-action="post-receipt"[^>]*>/)[0];assert.doesNotMatch(ordinary,/\sdisabled(?:\s|>)/);
});
