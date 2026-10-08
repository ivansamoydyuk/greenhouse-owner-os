import test from 'node:test';
import assert from 'node:assert/strict';
import {harvestUI} from '../../src/greenhouse_owner_os/web/harvest.js';
import {inventoryUI} from '../../src/greenhouse_owner_os/web/inventory.js';
import {tasksUI} from '../../src/greenhouse_owner_os/web/tasks.js';

function harvestFixture(){
 return {
  work_areas:[],references:[{id:'depot',kind:'counterparty',role:'depot',original_name:'TEST <depot>'}],
  owner:{season_start:'2026-10-01',season_end:'2027-10-01',season:[
   {currency:'TRY',kg:'600',count_covered_kg:'400',count:15,average_bunch_weight:'26.666666666666',average_price:'55',count_coverage_complete:false},
   {currency:'USD',kg:'999',count_covered_kg:null,count:null,average_bunch_weight:null,average_price:'77',count_coverage_complete:false},
  ]},
  harvest:{settlements:[],reports:[],batches:[],attention:[],cut_counts:[],total_cut_count:0,unknown_cut_count:0,analytics:[
   {name:'TEST greenhouse',greenhouse_id:'greenhouse',currency:'TRY',total_depot_kg:'600',sales_amount:'33000.00',receivable_outstanding:'0.00',average_price_kg:'55',average_weight_kg:'26.666666666666',confirmed_kg:'400',confirmed_count:15,quality_mix:{HIGHEST:'100',FIRST:'300',SECOND:'150',YERLI:'50'}},
   {name:'Dollar greenhouse',greenhouse_id:'other',currency:'USD',total_depot_kg:'999',sales_amount:'76923.00',receivable_outstanding:'0.00',average_price_kg:'77',average_weight_kg:null,confirmed_kg:'0',confirmed_count:0,quality_mix:{VERIFY:'999'}},
  ]},
 };
}
const harvestRender=(data,currency='TRY')=>harvestUI({getData:()=>data,getCurrency:()=>currency}).render();

test('Harvest starts with the authoritative season and preserves weighted-average coverage and currencies',()=>{
 const html=harvestRender(harvestFixture());
 assert.ok(html.indexOf('harvest-season-title')<html.indexOf('harvest-areas-title'));
 assert.match(html,/2026\/2027/);
 assert.match(html,/600 <small>кг<\/small>/);
 assert.match(html,/26,67 <small>кг<\/small>/);
 assert.match(html,/55,00 <small>TRY\/кг<\/small>/);
 assert.match(html,/Неполное покрытие количества/);
 assert.match(html,/400 кг \/ 15 веток из строк чеков депо/);
 assert.match(html,/Другие валюты.*учитываются отдельно/);
 assert.doesNotMatch(html,/999|Dollar greenhouse|77,00/);
 assert.doesNotMatch(html,/40,00|25,00/);
 assert.match(html,/Полевые отчёты не добавляются повторно; оплата не меняет выручку/);
});

test('Harvest absent counts never become zero averages and quality retains historical uncertainty',()=>{
 const data=harvestFixture();
 const html=harvestRender(data,'USD');
 assert.match(html,/Средний вес недоступен без подтверждённого количества/);
 assert.match(html,/Качество не проверено/);
 assert.doesNotMatch(html,/0,00 <small>кг<\/small>/);
 const normal=harvestRender(data);
 for(const label of ['Высшее','Первое','Второе','Историческое YERLI · нужна проверка'])assert.ok(normal.includes(label));
 assert.match(normal,/не означают новую проверку качества/);
 assert.match(normal,/YERLI не приравнивается к текущим уровням/);
});

test('Harvest preserves source quality and amount discrepancies in settlement cards',()=>{
 const data=harvestFixture();
 data.harvest.settlements=[{id:'receipt',depot_id:'depot',currency:'TRY',status:'posted',total_amount:'100.00',remaining_amount:'100.00',stated_total:'101.00',event_date:'2026-10-08',original_greenhouse:'<source greenhouse>',reconciliation:{depot_weight_kg:'2'},lines:[{original_quality_name:'Grand <source>'}],issues:['amount_discrepancy','unallocated']}];
 const html=harvestRender(data);
 assert.match(html,/TEST &lt;depot&gt;/);
 assert.match(html,/Качество в источнике: Grand &lt;source&gt;/);
 assert.match(html,/Сумма в источнике: 101,00 TRY · есть расхождение/);
 assert.match(html,/Есть нераспределённые кг \/ сумма/);
 assert.doesNotMatch(html,/<source greenhouse>|<source>/);
});

test('Harvest honestly renders unavailable snapshots and empty periods without invented metrics',()=>{
 const data=harvestFixture();delete data.owner;data.harvest.analytics=[];
 const html=harvestRender(data);
 assert.match(html,/Сводка сезона недоступна/);
 assert.match(html,/Проведённых чеков в выбранном периоде и валюте пока нет/);
 assert.doesNotMatch(html,/Всего за сезон|0 <small>кг/);
});

function inventoryFixture(){
 return {work_areas:[],inventory:{
  products:[{id:'p1',name:'KNO3 <test>',aliases:[],active:true,base_unit:'kg'},{id:'p2',name:'Multilex TEST',aliases:[],active:true,base_unit:'L'}],
  locations:[{id:'l1',name:'İmamlı TEST'},{id:'l2',name:'Özvadi TEST'}],
  stock:[{product_id:'p1',location_id:'l1',product_name:'KNO3 <test>',location_name:'İmamlı TEST',quantity:'100.000000000001',uncosted_quantity:'100.000000000001',base_unit:'kg'},{product_id:'p2',location_id:'l2',product_name:'Multilex TEST',location_name:'Özvadi TEST',quantity:'20',uncosted_quantity:null,base_unit:'L'}],
  attention:[],events:[],applied:[],movements:[{event_id:'receipt',product_id:'p1',location_id:'l1',event_date:'2026-10-08',role:'receipt',quantity:'100.000000000001',base_unit:'kg'}],
 }};
}

test('Inventory presents product, exact independent units, location, UNKNOWN cost and recent movement first',()=>{
 const html=inventoryUI({getData:()=>inventoryFixture()}).render();
 assert.match(html,/KNO3 &lt;test&gt;/);
 assert.match(html,/100\.000000000001 kg/);
 assert.match(html,/20 L/);
 assert.match(html,/İmamlı TEST/);
 assert.match(html,/Последнее движение: Приход · 2026-10-08/);
 assert.match(html,/Стоимость неизвестна · UNKNOWN для 100\.000000000001 kg/);
 assert.match(html,/Начальные фактические остатки: UNKNOWN/);
 assert.ok(html.indexOf('inventory-stock-title')<html.indexOf('inventory-reference-settings'));
 assert.match(html,/<details class="card module-disclosure" id="inventory-reference-settings">/);
 assert.match(html,/data-ievent="receipt"/);
 assert.doesNotMatch(html,/Низкий запас|Мин\. остаток|120 kg|Стоимость подтверждена/);
});

test('Inventory empty state and unknown consumption value stay honest',()=>{
 const data=inventoryFixture();data.inventory.stock=[];data.inventory.movements=[];
 data.inventory.applied=[{greenhouse_id:null,work_area_id:null,product_name:'TEST',known_cost:'0',currency:null,uncosted_quantity:'3',quantity:'3',base_unit:'kg'}];
 const html=inventoryUI({getData:()=>data}).render();
 assert.match(html,/Нулевой расчётный остаток не является проверенным начальным пересчётом/);
 assert.match(html,/Проведённых движений пока нет/);
 assert.match(html,/Стоимость UNKNOWN · неоценено 3 kg/);
 assert.doesNotMatch(html,/Известная стоимость: 0/);
 for(const id of ['i-product','i-location','i-filter','i-from','i-to'])assert.ok(html.includes(`id="${id}"`));
 for(const action of ['receipt','application','transfer','plan','writeoff'])assert.ok(html.includes(`data-inew="${action}"`));
});

function tasksFixture(){
 return {tasks:{areas:[],workers:[{id:'w1',display_name:'TEST <worker>',active:true,language:'fa'}],rules:[{id:'rule',definition:{title:'TEST recurring'},frequency:'weekly',weekday:4,version:1}],attention:[{id:'issue',title:'TEST issue',reasons:['Нужна проверка <source>']}],tasks:[
  {id:'done',title:'TEST completed',status:'completed',category:'general',planned_date:'2026-10-07'},
  {id:'working',title:'TEST active',status:'in_progress',category:'repair',planned_date:'2026-10-08'},
  {id:'reported',title:'TEST report',status:'reported',category:'general',planned_date:'2026-10-08'},
  {id:'cancelled',title:'TEST cancelled',status:'cancelled',category:'general',planned_date:'2026-10-07'},
 ]}};
}

test('Tasks prioritize exceptions and current work over personnel while preserving all lifecycle states',()=>{
 const html=tasksUI({getData:()=>tasksFixture()}).render();
 assert.match(html,/Открытые поручения<\/span><strong class="metric-value">2/);
 assert.match(html,/В работе<\/span><strong class="metric-value">1/);
 assert.ok(html.indexOf('task-attention-title')<html.indexOf('id="task-list"'));
 assert.ok(html.indexOf('data-task="working"')<html.indexOf('data-task="done"'));
 assert.ok(html.indexOf('id="task-list"')<html.indexOf('id="worker-directory"'));
 assert.match(html,/Отчёт работника · на проверке/);
 assert.match(html,/Выполнено/);
 assert.match(html,/Отменено/);
 assert.match(html,/Нужна проверка &lt;source&gt;/);
 assert.match(html,/TEST &lt;worker&gt;/);
 assert.match(html,/Отчёт работника ещё не означает, что владелец принял работу/);
});

test('Task directory and recurrence remain working secondary disclosures and manual capture is quiet',()=>{
 const html=tasksUI({getData:()=>tasksFixture()}).render();
 for(const id of ['worker-directory','task-recurrence'])assert.ok(html.includes(`<details class="card module-disclosure" id="${id}">`));
 for(const selector of ['id="worker-new"','data-worker-active="w1"','data-worker-session="w1"','data-rule="rule"','data-rule-change="rule"'])assert.ok(html.includes(selector));
 for(const key of ['status','worker','greenhouse','category','date'])assert.ok(html.includes(`id="task-filter-${key}"`));
 assert.match(html,/<button id="global-capture" class="quiet">Записать событие вручную<\/button>/);
 assert.doesNotMatch(html,/class="capture primary"|RECEIVED_REPORT|Отправлено в Telegram/);
});


test('Inventory period results use central rendering and retain only the matching snapshot filter',async()=>{
 const data=inventoryFixture(),response={...data.inventory,applied:[]},events=[],requests=[];
 const elements={
  '#i-from':{value:'2026-10-05'},'#i-to':{value:'2026-10-08'},'#i-filter':{},'#i-product':{},'#i-location':{},
  '#refresh':{dispatchEvent(event){events.push(event);data.inventory=event.detail;}},
 };
 const originalDocument=globalThis.document;
 globalThis.document={querySelector:selector=>elements[selector],querySelectorAll:()=>[]};
 try{
  const ui=inventoryUI({getData:()=>data,getEntity:()=>'entity',api:async path=>{requests.push(path);return response;},notice:message=>assert.fail(message)});
  ui.bind();await elements['#i-filter'].onclick();
  assert.deepEqual(requests,['/v1/entities/entity/inventory?date_from=2026-10-05&date_to=2026-10-08']);
  assert.equal(events.length,1);assert.equal(events[0].type,'inventory-range');assert.equal(events[0].detail,response);
  const filtered=ui.render();assert.match(filtered,/id="inventory-consumption" open/);assert.match(filtered,/2026-10-05 → 2026-10-08/);
  assert.match(filtered,/id="i-from"[^>]*value="2026-10-05"/);
  data.inventory={...response};
  const refreshed=ui.render();assert.doesNotMatch(refreshed,/выбран период|2026-10-05 → 2026-10-08/);
 }finally{globalThis.document=originalDocument;}
});
