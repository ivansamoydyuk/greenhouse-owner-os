import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
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
 const target={};const node={isConnected:true,inert:false,querySelector:s=>s==='.form-error'?target:field};
 const run=new Function('node','requestAnimationFrame','document',`${body}; let route='receipt', epoch=1; const api={generation:1}; showError({message:'Missing',field:'quantity'},node); return {route:()=>route='home',epoch:()=>epoch++,session:()=>api.generation++};`);
 for(const change of ['route','epoch','session']){const ctx=run(node,cb=>callback=cb,{});ctx[change]();callback();assert.equal(focus,0);}
 run(node,cb=>callback=cb,{});callback();assert.equal(focus,1);
 node.isConnected=false;run(node,cb=>callback=cb,{});callback();assert.equal(focus,1);
});

test('receipt comments remain source-only and cannot mask physical duplicate review',()=>{
 const ui=fs.readFileSync('prototype/owner-r3/connected.js','utf8');
 const receipt=ui.slice(ui.indexOf('} else if (name === "receipt")'),ui.indexOf('} else if (name === "product")'));
 assert.ok(receipt.includes('notes: null'));
 assert.ok(receipt.includes('notes: draft.original'));
 assert.ok(!receipt.includes('notes: draft.original || null'));
});
