import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {taskPreparationDetails} from '../../src/greenhouse_owner_os/web/owner-r2.js';

const harness=readFileSync(new URL('../e2e/owner-iphone-ui.mjs',import.meta.url),'utf8');
const expression=harness.match(/const fullOriginal = ([^\n]+);/)[1];
const choose=new Function('sheet','page',`return ${expression};`);
const fragment='Сегодня полить İmamlı';
const longText='Сегодня полить İmamlı. Сегодня полить İmamlı. TEST · Проверить давление после полива. Сверить каждую строку.';

// A small locator model evaluates the actual harness expression against the
// confirmed DOM shape: nested provenance and the separate original disclosure.
function fixture(reverse=false){
 const original={summary:'Оригинальный источник',depth:1,open:false,text:longText};
 const provenance={summary:'Происхождение и уточнение',depth:2,open:false,text:fragment};
 const details=reverse?[original,provenance]:[provenance,original];
 const locator=rows=>({
  locator(selector){
   if(selector==='#sheet-content > details')return locator(details.filter(node=>node.depth===1));
   if(selector===':scope > .source-text')return locator(rows.map(detail=>({text:detail.text,detail})));
   if(selector==='.source-text')return locator(details.map(detail=>({text:detail.text,detail})));
   throw Error('Unexpected selector '+selector);
  },
  filter({has}){assert.equal(has.exact,true);return locator(rows.filter(row=>row.summary===has.text));},
  count:async()=>rows.length,
  innerText:async()=>{assert.equal(rows.length,1,'Strict uniqueness');return rows[0].text;},
  evaluate:async callback=>{assert.equal(rows.length,1,'Strict uniqueness');return callback(rows[0]);},
 });
 const sheet=()=>locator([]),page={getByText:(text,{exact})=>({text,exact})};
 return {sheet,page,original,provenance};
}

for(const reverse of [false,true])test(`full original remains uniquely scoped when provenance also has source text (reverse=${reverse})`,async()=>{
 const priorWindow=globalThis.window,h=fixture(reverse);globalThis.window={isVisibleOwnerControl:node=>node.detail.open};
 try{
  const provenance=taskPreparationDetails({action_type:'PERSONAL_TASK',revision:{payload:{original_instruction:fragment},provenance:{}},recovery_origin_context:{version:1,note:fragment}});
  assert.match(provenance,/class="source-text"/);assert.ok(provenance.includes(fragment),'Meaningful source-linked provenance remains present');
  assert.equal(await h.sheet().locator('.source-text').count(),2,'The previous broad locator is truly ambiguous');
  const selected=choose(h.sheet,h.page);assert.equal(await selected.count(),1);assert.equal(await selected.evaluate(node=>window.isVisibleOwnerControl(node)),false);
  h.original.open=true;assert.equal(await selected.evaluate(node=>window.isVisibleOwnerControl(node)),true);assert.equal(await selected.innerText(),longText);assert.equal(h.provenance.open,false);
 }finally{globalThis.window=priorWindow;}
});

test('retained browser checks unique source, collapsed visibility and complete original after the trusted disclosure click',()=>{
 assert.match(harness,/assert\.equal\(await fullOriginal\.count\(\),1/);
 assert.match(harness,/assert\.equal\(await fullOriginal\.evaluate\(node => window\.isVisibleOwnerControl\(node\)\), false, 'Full original remains under disclosure'\)/);
 assert.ok(harness.includes("await sheet().getByText('Оригинальный источник', {exact: true}).click();"));
 assert.ok(harness.includes("assert.equal(await fullOriginal.innerText(), longText, 'Original remains complete and exact under disclosure');"));
 assert.doesNotMatch(expression,/\.first\(|\.nth\(|hasText|longText/);
});
