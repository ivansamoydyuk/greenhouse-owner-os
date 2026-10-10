import test from 'node:test';
import assert from 'node:assert/strict';
import {financeSelection,financeRoute,financeQuery,financeNumber,financeAmount} from '../../prototype/owner-r3/finance-connected.js';
test('finance period defaults follow current season but are explicit dates',()=>{
 assert.equal(financeSelection('money','2026-09-30').date_from,'2025-10-01');
 assert.equal(financeSelection('money','2026-10-01').date_from,'2026-10-01');
 assert.equal(financeSelection('money','2026-10-10').date_to,'2026-10-10');
});
test('money navigation retains date currency greenhouse and account across drilldown',()=>{
 const f={tab:'result',date_from:'2026-10-01',date_to:'2026-10-10',currency:'EUR',greenhouse_id:'area-1',money_account_id:'account-1'};
 assert.deepEqual(financeSelection(financeRoute(f),'2026-10-10'),f);
 assert.equal(financeQuery(f),'date_from=2026-10-01&date_to=2026-10-10&currency=EUR&greenhouse_id=area-1&money_account_id=account-1');
});
test('unknown balance stays unknown and large exact decimals are not rounded in JavaScript',()=>{
 assert.equal(financeAmount(null,'TRY'),'Неизвестно');
 assert.equal(financeAmount('0.00','TRY'),'0 TRY');
 assert.equal(financeNumber('999999999999999999.99'),'999 999 999 999 999 999,99');
 assert.equal(financeAmount('-500.50','TRY'),'-500,5 TRY');
});
test('invalid route date does not silently normalize February into March',()=>{
 assert.equal(financeSelection('money/cash/2026-02-31/2026-10-10','2026-10-10').date_from,'2026-10-01');
});
import {financeAnalyticsView,financeDocumentView,financeSourceLinks} from '../../prototype/owner-r3/finance-connected.js';
const selection=financeSelection('money','2026-10-10');
const emptyDenominator={confirmed_kg:null,exact_kg:'0',estimated_kg:'0',unallocated_kg:'0',stale_estimate_count:0,sources:[]};
const response={currency:'TRY',cutover_date:'2026-10-04',accounts:[],movements:[],areas:[],cash_totals:{net_change:'200.00'},debts:{receivable:'600.00',payable:'300.00',parties:[],documents:[]},economics:{revenue:'1000.00',operating_cost:'500.00',operating_result:'500.00',virtual_general_cost:'0.00',unresolved_cost:'0.00',unallocated_revenue:'1000.00'},economic_events:[],harvest_denominator:emptyDenominator,cost_per_kg:{known_finance_cost_per_kg:null},coverage:{inventory_cost_rows:[],unclassified_obligation_ids:[]},allocation_policy:{version:1,effective_from:'2026-10-01',status:'UNRESOLVED_REFERENCE',targets:[]}};
test('overview keeps economic result and money movement distinct',()=>{
 const html=financeAnalyticsView(response,selection);
 assert.match(html,/600 TRY/);assert.match(html,/300 TRY/);assert.match(html,/200 TRY/);assert.match(html,/500 TRY/);
 assert.match(html,/Движение денег периода/);assert.match(html,/Результат известной части/);assert.match(html,/Документы всех дат/);
});
test('unknown opening before cutover is never labelled confirmed zero',()=>{
 const r={...response,accounts:[{money_account_id:'a',name:'Банк',currency:'TRY',opening_balance:null,closing_balance:null,current_balance:'200',incoming:'400',outgoing:'200',net_change:'200',opening_basis:'UNKNOWN_BEFORE_CUTOVER'}]};
 const html=financeAnalyticsView(r,{...selection,tab:'cash'});
 assert.match(html,/Начало периода предшествует/);assert.match(html,/Неизвестно/);assert.doesNotMatch(html,/Подтверждённый нулевой остаток/);
});
test('result renders sources, policy, UNKNOWN denominator and partial cost without fake profit completeness',()=>{
 const html=financeAnalyticsView(response,{...selection,tab:'result'});
 assert.match(html,/Результат по учтённым затратам/);assert.match(html,/Исторический срез/);assert.match(html,/не полная себестоимость/);assert.match(html,/Неизвестно/);
});
test('untrusted financial names descriptions source text are escaped',()=>{
 const r={...response,accounts:[{name:'<img src=x onerror=alert(1)>',currency:'TRY',current_balance:'3'}]};
 const html=financeAnalyticsView(r,selection);assert.doesNotMatch(html,/<img/);assert.match(html,/&lt;img/);
});
test('debt document preserves reversal and exact allocation history with source link',()=>{
 const doc={counterparty_name:'Мастер',direction:'payable',status:'posted',settlement_status:'partial',obligation_id:'o',counterparty_id:'p',currency:'TRY',original_amount:'500',allocated_amount:'200',remaining_amount:'300',event_date:'2026-10-09',due_date:null,source:{kind:'obligation',id:'o',description:'Ремонт',original_id:'s'},history:[{kind:'payment',id:'pay1',status:'reversed',event_date:'2026-10-10',amount:'50',currency:'TRY',source:'<script>bad()</script>'}]};
 const html=financeDocumentView(doc,financeRoute(selection));assert.match(html,/#source\/s/);assert.match(html,/#payment\/pay1/);assert.match(html,/Отменён обратной проводкой/);assert.match(html,/300 TRY/);assert.doesNotMatch(html,/<script>/);
});
test('source links do not turn source-provided text into URLs',()=>{
 const html=financeSourceLinks({kind:'direct_expense',id:'x',description:'https://evil.test',original_id:'a" onclick="bad'});
 assert.doesNotMatch(html,/href="https:/);assert.doesNotMatch(html,/" onclick="/);
});
import {visibleEconomicEvents} from '../../prototype/owner-r3/finance-connected.js';
test('selected greenhouse source list excludes unrelated zero events while retaining its explicit allocation',()=>{
 const events=[{id:'B-only',selected_amount:'0.00',allocations:[{greenhouse_id:'B',amount:'7'}]},{id:'A-exact',selected_amount:'3.00',allocations:[{greenhouse_id:'A',amount:'3'}]},{id:'A-rounded',selected_amount:'0.00',allocations:[{greenhouse_id:'A',amount:'0.00'}]},{id:'unresolved',selected_amount:'0.00',allocations:[{greenhouse_id:null,classification:'UNRESOLVED',amount:'5'}]}];
 assert.deepEqual(visibleEconomicEvents(events,'A').map(x=>x.id),['A-exact','A-rounded']);
 assert.equal(visibleEconomicEvents(events,null).length,4);
});
import {denominatorCoverage} from '../../prototype/owner-r3/finance-connected.js';
test('retained account filter is visible outside cash tab and does not imply economic filtering',()=>{
 const r={...response,available_accounts:[{money_account_id:'a',name:'Банк выбранный'}]};
 const html=financeAnalyticsView(r,{...selection,money_account_id:'a',tab:'result'});
 assert.match(html,/только счёт «Банк выбранный»/);assert.match(html,/не ограничивает выручку, затраты и долги/);
});
test('partial denominator and unknown zero-rounded basis remain visibly incomplete',()=>{
 const html=denominatorCoverage({classification:'PARTIAL',unallocated_kg:'0.000',basis_incomplete:true});
 assert.match(html,/Покрытие веса неполное/);assert.match(html,/неизвестная доля/);assert.match(html,/округлённый остаток 0 не делает покрытие полным/);
});
