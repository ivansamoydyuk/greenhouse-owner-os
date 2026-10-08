import test from 'node:test';
import assert from 'node:assert/strict';
import {receiptTotal} from '../../src/greenhouse_owner_os/web/owner-r2.js';
test('depot preview totals use exact line arithmetic, not binary floats',()=>{
 assert.equal(receiptTotal([{net_weight_kg:'100',unit_price:'20'},{net_weight_kg:'50',unit_price:'15'},{net_weight_kg:'25',unit_price:'10'}]),'3000.00');
 assert.equal(receiptTotal([{net_weight_kg:'0.125',unit_price:'0.20'}]),'0.03');
});

import {proposalSummary,receiptEffectTotal,workerProposal} from '../../src/greenhouse_owner_os/web/owner-r2.js';
const data={owner:{finance:{references:[{id:'party',original_name:'Серкан'},{id:'cash',original_name:'Cash TR'}],work_areas:[{id:'greenhouse',kind:'greenhouse',name:'İmamlı'}],obligations:[{obligation_id:'debt',counterparty_id:'party',description:'Ремонт трубы',original_amount:'2600.00',remaining_amount:'2100.00',currency:'TRY'}]}},inventory:{products:[{id:'product',name:'KNO3'}],locations:[{id:'location',name:'Склад İmamlı'}]},tasks:{workers:[{id:'worker',display_name:'TEST worker'}]}};
const proposal=(action_type,payload,basis={})=>({id:'proposal',state:'READY',action_type,revision:{payload,basis,missing:[]}});
test('physical stock confirmation names product, exact quantity/unit/location and UNKNOWN cost',()=>{
 const html=proposalSummary(proposal('STOCK_RECEIPT',{event_date:'2026-10-08',lines:[{product_id:'product',original_product_name:'KNO3 <original>',quantity:'9007199254740993.125',unit:'kg',location_id:'location',unit_cost:null}]}),data,false);
 for(const text of ['KNO3','KNO3 &lt;original&gt;','9007199254740993.125 kg','Склад İmamlı','UNKNOWN · неизвестна, не 0','Увеличит физический остаток'])assert.ok(html.includes(text),text);
 assert.ok(!html.includes('data-r2-open'));assert.ok(!html.includes('<original>'));
});
test('finance description cannot hide party, debt, account, explicit currency or effects',()=>{
 const html=proposalSummary(proposal('PAYMENT',{amount:'500.00',currency:'TRY',description:'Оплата за ремонт',counterparty_id:'party',money_account_id:'cash',obligation_id:'debt'}, {'debt_remaining':{obligation_id:'debt',original_amount:'2600.00',settled_amount:'500.00',remaining_amount:'2100.00',currency:'TRY'}}),data,false);
 for(const text of ['Оплата за ремонт','Серкан','Cash TR','Ремонт трубы','2 100,00 TRY','только выбранный долг'])assert.ok(html.includes(text),text);
 const uah=proposalSummary(proposal('PAYABLE',{amount:'1000.00',currency:'UAH',counterparty_id:'party',attributions:[]}),data);
 assert.ok(uah.includes('1 000,00 UAH'));assert.ok(uah.includes('GENERAL'));assert.ok(!uah.includes('1 000,00 TRY'));
});
test('snapshot references override later Home names and exact economic attributions remain visible',()=>{
 const html=proposalSummary(proposal('PAYABLE',{amount:'100.00',currency:'EUR',counterparty_id:'party',attributions:[{work_area_id:'greenhouse',amount:'75.25'}]},{'core.counterparties:party':{original_name:'Reviewed party'},'core.work_areas:greenhouse':{name:'Reviewed greenhouse'}}),data);
 assert.ok(html.includes('Reviewed party'));assert.ok(html.includes('Reviewed greenhouse · 75,25 EUR'));assert.ok(html.includes('остаток остаётся GENERAL'));assert.ok(!html.includes('Серкан'));
});
test('stock application exposes required greenhouse and preserves quantity unit without conversion',()=>{
 const html=proposalSummary(proposal('STOCK_APPLICATION',{lines:[{product_id:'product',original_product_name:'KNO3',quantity:'2.500',unit:'L',location_id:'location',greenhouse_id:'greenhouse',unit_cost:null}]}),data,false);
 for(const text of ['2.500 L','Теплица внесения','İmamlı','уменьшит остаток'])assert.ok(html.includes(text),text);
});
test('depot confirmation retains original and scoped quality, raw line, linkage, allocation and no-cash effect',()=>{
 const html=proposalSummary(proposal('DEPOT_RECEIPT',{depot_id:'party',currency:'TRY',lines:[{original_quality_name:'Grand',source_line:'Grand 100 × 20',net_weight_kg:'100',unit_price:'20',stated_amount:'2000',count:4,allocations:[{work_area_id:'greenhouse',net_weight_kg:'100',amount:'2000',confirmed_count:4}]}],qualities:['HIGHEST'],quality_reviewed:true,batch_ids:['batch'],link_evidence:'Проверен отдельный отчёт',alacak_value:'2000',stated_total:'2000'},{same_day_batches:[{id:'batch',name:'TEST партия',label:'TEST партия'}]}),data,false);
 for(const text of ['Grand','Высшее · проверено владельцем','Grand 100 × 20','Точное распределение','100 кг · 2 000,00 TRY','TEST партия','Проверен отдельный отчёт','Alacak: 2000 TRY — не оплата','продажу и долг депо'])assert.ok(html.includes(text),text);
});
test('worker review shows recipient, greenhouse, original and reviewed translation with not-sent state',()=>{
 const html=proposalSummary(proposal('WORKER_TASK',{title:'Полить',worker_id:'worker',greenhouse_id:'greenhouse',original_instruction:'Полить 5 L',language:'fa',rendered_text:'TEST دری 5 L'}),data,false);
 for(const text of ['TEST worker','İmamlı','Полить 5 L','Дари','TEST دری 5 L','Работнику не отправлено'])assert.ok(html.includes(text),text);
});

test('depot effect total displays the exact individually reviewed amount decision',()=>{
 assert.equal(receiptEffectTotal([{net_weight_kg:'1',unit_price:'1.00',confirmed_amount:'2'},{net_weight_kg:'0.125',unit_price:'0.20'}]),'2.03');
});

test('unknown explicit currency is never rendered as a TRY amount',()=>{
 const html=proposalSummary(proposal('PAYABLE',{amount:'150.00',currency:null,counterparty_id:'party'}),data);
 assert.ok(html.includes('Валюта не определена'));assert.ok(!html.includes('150,00 TRY'));
});

test('worker handoff language follows selected verified worker and never an old candidate',()=>{
 const workers=[{id:'a',language:'fa'},{id:'b',language:'tr'}];
 const payload={worker_id:'b',language:'fa',rendered_text:'Owner reviewed Turkish'};
 assert.deepEqual(workerProposal(payload,workers),{...payload,language:'tr'});
 assert.equal(workerProposal({...payload,worker_id:'missing'},workers).language,null);
 assert.equal(workerProposal({...payload,worker_id:'a',language:null},workers).language,'fa');
});

test('payment review reads exact remaining amount from the real PostgreSQL balance-row snapshot',()=>{
 const basis={debt_remaining:{obligation_id:'debt',original_amount:'999999999999999999.99',settled_amount:'0.01',remaining_amount:'999999999999999999.98',currency:'TRY'}};
 const html=proposalSummary(proposal('PAYMENT',{amount:'1.00',currency:'TRY',counterparty_id:'party',obligation_id:'debt'},basis),data,false);
 assert.ok(html.includes('999 999 999 999 999 999,98 TRY'));assert.ok(!html.includes('[object Object]'));assert.ok(!html.includes('2 100,00 TRY'));
});
