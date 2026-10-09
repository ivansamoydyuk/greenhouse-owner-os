import test from 'node:test';
import assert from 'node:assert/strict';
import {
  createDailyDemo, ensureDaily, overview, toMinor, fromMinor, formatMinor, createAction, updateAction, confirmAction,
  harvestRows, financeParties, projectDaily, receiptCandidates, syncLegacyConfirmation,
} from '../../prototype/owner-r3/daily-demo.js';
import {initialState} from '../../prototype/owner-r3/fixtures.js';
import {confirm as legacyConfirm, persist, loadState} from '../../prototype/owner-r3/demo-adapter.js';
const fresh=()=>({daily:createDailyDemo()});
const post=(s,kind,values)=>{const a=createAction(s,kind,values);assert.equal(a.canConfirm,true,a.clarifications.join('; '));return confirmAction(s,a.id,a.revision);};
const expenseInput={purpose:'Инструменты',amount:'7250.25',currency:'TRY'};

test('daily dataset is deterministic, independent and bounded to the requested period',()=>{
  const a=createDailyDemo(),b=createDailyDemo();assert.deepEqual(a,b);
  a.receipts[0].lines[0].amountMinor=1;assert.notEqual(a.receipts[0].lines[0].amountMinor,b.receipts[0].lines[0].amountMinor);
  assert.equal(b.period.from,'2026-09-01');assert.equal(b.period.to,'2026-10-09');
  assert.ok(b.sources.every(s=>s.synthetic));assert.ok(b.sources.every(s=>s.date>=b.period.from&&s.date<=b.period.to));
});
test('money conversion is exact minor units, rejects rounding and unsupported numeric syntax',()=>{
  assert.equal(toMinor('9 876 543,21'),987654321);assert.equal(toMinor('0.01'),1);assert.equal(toMinor('7000'),700000);
  assert.equal(fromMinor(12345),123.45);assert.match(formatMinor(12345),/123,45/);assert.equal(formatMinor(null),'UNKNOWN');
  for(const invalid of ['0','-1','1.001','1e3','NaN','Infinity','', '1,000.00','10000000000000'])assert.throws(()=>toMinor(invalid),/INVALID_AMOUNT/);
  assert.throws(()=>fromMinor(1.25),/UNSAFE_INTEGER/);
});
test('all confirmed receipt lines have exact gram/price arithmetic and receipt totals reconcile',()=>{
  const d=createDailyDemo();assert.ok(d.receipts.length>=5);
  for(const r of d.receipts){assert.equal(r.totalMinor,r.lines.reduce((s,l)=>s+l.amountMinor,0));assert.equal(r.netKg,r.lines.reduce((s,l)=>s+l.netGrams,0)/1000);
    for(const l of r.lines){assert.equal(l.amountMinor*1000,l.netGrams*l.priceMinor);assert.ok(Number.isSafeInteger(l.amountMinor));}}
});
test('duplicate quality lines retain their own identity and are never merged',()=>{
  const r=createDailyDemo().receipts.find(r=>r.id==='depot-sep17');const repeated=r.lines.filter(l=>l.quality==='II');
  assert.equal(repeated.length,2);assert.notEqual(repeated[0].id,repeated[1].id);assert.equal(repeated[0].netKg,220);assert.equal(repeated[1].netKg,160);
});
test('rolling Alacak balances reconcile as-of each receipt, and are not revenue or cash',()=>{
  const d=createDailyDemo();for(const r of d.receipts){const sales=d.receipts.filter(x=>x.date<=r.date).reduce((s,x)=>s+x.totalMinor,0);const cash=d.payments.filter(x=>x.direction==='in'&&x.date<=r.date).reduce((s,x)=>s+x.amountMinor,0);assert.equal(r.rollingAlacakMinor,sales-cash);}
  const before=overview(d);d.receipts[0].rollingAlacakMinor=99000000;assert.deepEqual(overview(d),before);
});
test('owner-corrected ADET retains synthetic original and only corrected value contributes',()=>{
  const d=createDailyDemo(),r=d.receipts.find(r=>r.id==='depot-sep08');assert.equal(r.adet,154);assert.match(r.originalAdet,/151/);assert.equal(r.adetProvenance,'owner-correction');
  const row=harvestRows(d,{seasonOnly:false}).find(r=>r.id==='imamli');assert.equal(row.bunches,154+93);
});
test('mixed count allocation is a sum-preserving estimate and never allocates kg or revenue',()=>{
  const d=createDailyDemo(),r=d.receipts.find(r=>r.allocations.length);assert.equal(r.allocations.reduce((s,a)=>s+a.bunches,0),r.adet);
  assert.ok(r.allocations.every(a=>a.provenance==='derived-estimate'&&a.kg===null&&a.revenueMinor===null));
  for(const row of harvestRows(d,{seasonOnly:false})){assert.equal(row.totalKg,null);assert.equal(row.totalRevenueMinor,null);assert.equal(row.kgComplete,false);}
});
test('overview separates accrual totals, cash, debt and coverage; it never invents opening cash or full profit',()=>{
  const o=overview(fresh());assert.equal(o.revenueMinor,23536500);assert.equal(o.operatingExpenseMinor,26720000);
  assert.equal(o.recordedOperatingResultMinor,-3183500);assert.equal(o.operatingResultMinor,null);
  assert.equal(o.cashReceivedMinor,15500000);assert.equal(o.cashPaidMinor,25800000);assert.equal(o.receivableMinor,8036500);assert.equal(o.payableMinor,3420000);
  assert.equal(o.cashBalanceMinor,null);assert.equal(o.completeness.status,'partial');assert.equal(o.completeness.openingCashKnown,false);
  assert.notEqual(o.revenueMinor,o.cashReceivedMinor);assert.notEqual(o.operatingExpenseMinor,o.cashPaidMinor);
});
test('salary has three cash events and one non-posting summary, never a fourth payout',()=>{
  const d=createDailyDemo(),s=d.salarySettlements[0],payments=d.payments.filter(p=>s.paymentIds.includes(p.id));
  assert.equal(payments.length,3);assert.equal(payments.reduce((n,p)=>n+p.amountMinor,0),s.totalMinor);assert.equal(s.postsCash,false);
  assert.equal(d.expenses.filter(e=>e.id===s.expenseId).length,1);assert.equal(d.expenses.find(e=>e.id===s.expenseId).amountMinor,s.totalMinor);
  const before=overview(d);s.totalMinor+=100000;assert.deepEqual(overview(d),before);
});
test('supplier later debt note references the original purchase without another delivery, expense or payment',()=>{
  const d=createDailyDemo(),note=d.supplierClarifications[0],o=d.obligations.find(o=>o.id===note.obligationId);
  const applied=d.payments.flatMap(p=>p.allocations).filter(a=>a.obligationId===o.id).reduce((s,a)=>s+a.amountMinor,0);
  assert.equal(o.amountMinor-applied,note.remainingMinor);assert.equal(note.createsDelivery,false);assert.equal(note.createsPayment,false);
  assert.equal(d.expenses.filter(e=>e.id===o.expenseId).length,1);assert.equal(d.inventory[0].movements.filter(m=>m.kind==='arrival').length,1);
  const before=overview(d);d.supplierClarifications.push({...note,id:'same-debt-note-again'});assert.deepEqual(overview(d),before);
});
test('loan principal is cash only, and invoice alone never supplies an inventory balance',()=>{
  const d=createDailyDemo(),loan=d.payments.find(p=>p.category==='loan-principal');assert.ok(loan);assert.ok(!d.expenses.some(e=>e.id===loan.id||e.category==='loan-principal'));
  const invoice=d.invoices[0];assert.equal(invoice.arrivalConfirmed,false);assert.equal(invoice.quantity,null);assert.equal(invoice.unit,null);assert.equal(invoice.costMinor,null);
  assert.ok(!d.inventory.some(s=>s.name===invoice.product));
});
test('seeded inventory balances reconcile to movements, including equal transfer legs',()=>{
  const d=createDailyDemo();for(const row of d.inventory.filter(s=>s.quantity!=null))assert.equal(row.quantity,row.movements.reduce((s,m)=>s+m.quantity,0));
  const transfer=d.inventory.flatMap(s=>s.movements).filter(m=>m.transferId==='transfer-sep28');assert.equal(transfer.length,2);assert.equal(transfer.reduce((s,m)=>s+m.quantityMinor,0),0);
  assert.equal(d.inventory.find(s=>s.id==='film-stock').quantity,null);assert.ok(d.inventory.every(s=>s.costMinor===null));
});
test('saving a raw expense source without party is allowed and never posts anything automatically',()=>{
  const s=fresh(),before=overview(s),count=s.daily.sources.length,a=createAction(s,'expense',{raw:'Инструменты 7250 лир'});
  assert.equal(s.daily.sources.length,count+1);assert.equal(s.daily.sources.at(-1).raw,'Инструменты 7250 лир');assert.equal(a.canConfirm,false);assert.ok(a.clarifications.length);
  assert.equal(a.input.date,'2026-10-09');assert.equal(a.dateAssumed,true);assert.equal(a.status,'pending');
  const after=overview(s);for(const k of ['cashPaidMinor','operatingExpenseMinor','payableMinor'])assert.equal(after[k],before[k]);assert.throws(()=>confirmAction(s,a.id),/NEEDS_INPUT/);
});
test('explicit expense confirmation needs amount/purpose/currency, allows absent party, creates no payment or payable',()=>{
  const s=fresh(),before=overview(s),a=createAction(s,'expense',{raw:'Инструменты',...expenseInput});
  assert.equal(a.canConfirm,true);assert.equal(overview(s).operatingExpenseMinor,before.operatingExpenseMinor);
  confirmAction(s,a.id,a.revision);const after=overview(s);assert.equal(after.operatingExpenseMinor,before.operatingExpenseMinor+725025);
  assert.equal(after.cashPaidMinor,before.cashPaidMinor);assert.equal(after.payableMinor,before.payableMinor);assert.equal(s.daily.expenses.at(-1).party,null);
  const again=overview(s);confirmAction(s,a.id);assert.deepEqual(overview(s),again);
});
test('correction updates one pending action, retains original source and fences stale confirmation',()=>{
  const s=fresh(),a=createAction(s,'expense',{raw:'Инструменты примерно семь тысяч'}),id=a.id,sourceCount=s.daily.sources.length,sourceRaw=s.daily.sources.at(-1).raw;
  updateAction(s,id,expenseInput);assert.equal(a.revision,2);assert.equal(a.canConfirm,true);assert.equal(a.provenance,'owner-correction');assert.equal(s.daily.sources.length,sourceCount);assert.equal(s.daily.sources.at(-1).raw,sourceRaw);
  assert.throws(()=>confirmAction(s,id,1),/STALE/);confirmAction(s,id,2);assert.throws(()=>updateAction(s,id,{amount:'20'}),/READ_ONLY/);
});
test('invalid calendar dates and missing currency remain clarification candidates',()=>{
  const s=fresh();for(const date of ['2026-02-30','2026-13-01','09.10.2026'])assert.equal(createAction(s,'expense',{...expenseInput,date}).canConfirm,false);
  assert.equal(createAction(s,'expense',{...expenseInput,currency:''}).canConfirm,false);assert.equal(createAction(s,'expense',{...expenseInput,currency:'JPY'}).canConfirm,false);
});
test('payment requires a real selected account, currency and direction and is not an operating expense',()=>{
  const s=fresh(),before=overview(s),input={purpose:'Возврат основной суммы займа',amount:'3200',currency:'TRY',direction:'out',category:'loan-principal'};
  const a=createAction(s,'payment',input);assert.equal(a.canConfirm,false);assert.throws(()=>confirmAction(s,a.id),/NEEDS_INPUT/);
  updateAction(s,a.id,{accountId:'cash-try'});confirmAction(s,a.id);const after=overview(s);
  assert.equal(after.cashPaidMinor,before.cashPaidMinor+320000);assert.equal(after.operatingExpenseMinor,before.operatingExpenseMinor);assert.equal(after.payableMinor,before.payableMinor);
});
test('obligation requires a creditor; confirmation changes debt only',()=>{
  const s=fresh(),before=overview(s),a=createAction(s,'obligation',{...expenseInput,direction:'payable'});
  assert.equal(a.canConfirm,false);updateAction(s,a.id,{party:'DEMO Электромастер'});confirmAction(s,a.id);
  const after=overview(s);assert.equal(after.payableMinor,before.payableMinor+725025);assert.equal(after.operatingExpenseMinor,before.operatingExpenseMinor);assert.equal(after.cashPaidMinor,before.cashPaidMinor);
});
test('explicit debt payment reconciles both cash and remaining obligation without second expense',()=>{
  const s=fresh(),before=overview(s);post(s,'payment',{purpose:'Частичная оплата ремонта',amount:'8000',currency:'TRY',direction:'out',category:'operating',accountId:'cash-try',obligationId:'pump-payable'});
  const after=overview(s);assert.equal(after.cashPaidMinor,before.cashPaidMinor+800000);assert.equal(after.payableMinor,before.payableMinor-800000);assert.equal(after.operatingExpenseMinor,before.operatingExpenseMinor);
  assert.equal(createAction(s,'payment',{purpose:'Слишком большой платёж',amount:'4000',currency:'TRY',direction:'out',category:'operating',accountId:'cash-try',obligationId:'pump-payable'}).canConfirm,false);
});
test('debt payment rejects opposite direction, mismatched currency and counterparty',()=>{
  const s=fresh(),input={purpose:'Оплата',amount:'100',currency:'TRY',direction:'out',category:'operating',accountId:'cash-try',obligationId:'pump-payable'};
  for(const patch of [{direction:'in'},{currency:'EUR'},{party:'DEMO Другой'}])assert.equal(createAction(s,'payment',{...input,...patch}).canConfirm,false);
});
test('receivable cash receipt reduces receivable and adds cash but not revenue',()=>{
  const s=fresh(),before=overview(s);post(s,'payment',{purpose:'Оплата чека',amount:'1000',currency:'TRY',direction:'in',category:'operating',accountId:'bank-try',obligationId:'obligation-depot-oct09'});
  const after=overview(s);assert.equal(after.cashReceivedMinor,before.cashReceivedMinor+100000);assert.equal(after.receivableMinor,before.receivableMinor-100000);assert.equal(after.revenueMinor,before.revenueMinor);
});
test('non-TRY expense remains explicit and is not combined into TRY overview',()=>{
  const s=fresh(),before=overview(s);post(s,'expense',{purpose:'DEMO расход EUR',amount:'50',currency:'EUR'});
  const after=overview(s);assert.equal(after.operatingExpenseMinor,before.operatingExpenseMinor);assert.deepEqual(after.excludedCurrencies,['EUR']);
});
test('future dated cash does not change historical cash or historical obligation balances',()=>{
  const s=fresh(),before=overview(s);post(s,'payment',{purpose:'Поздняя оплата',amount:'100',currency:'TRY',direction:'out',category:'operating',accountId:'cash-try',obligationId:'pump-payable',date:'2026-10-10'});
  const after=overview(s);assert.equal(after.cashPaidMinor,before.cashPaidMinor);assert.equal(after.payableMinor,before.payableMinor);
});
test('quick harvest stores only greenhouse/count/date with unknown kg and no finance effect',()=>{
  const s=fresh(),before=overview(s),a=post(s,'harvest',{greenhouseId:'imamli',bunches:'18',date:'2026-10-09'}),r=s.daily.fieldReports.find(r=>r.id===a.id);
  assert.equal(r.bunches,18);assert.equal(r.kg,null);assert.equal(r.revenueMinor,null);assert.equal(r.receiptId,null);
  assert.equal(overview(s).revenueMinor,before.revenueMinor);assert.equal(overview(s).receivableMinor,before.receivableMinor);assert.equal(overview(s).cashReceivedMinor,before.cashReceivedMinor);
  assert.equal(createAction(s,'harvest',{greenhouseId:'imamli',bunches:'1.5'}).canConfirm,false);
});
test('same-date depot match remains only a candidate until explicit link confirmation',()=>{
  const s=fresh(),report=s.daily.fieldReports.find(r=>r.id==='field-oct09'),candidates=receiptCandidates(s,report.id);
  assert.ok(candidates.length);assert.equal(candidates[0].provenance,'date-candidate');assert.equal(candidates[0].requiresConfirmation,true);assert.equal(report.receiptId,null);
  const before=overview(s),action=createAction(s,'harvest-link',{reportId:report.id,receiptId:candidates[0].receiptId});assert.equal(report.receiptId,null);
  confirmAction(s,action.id);assert.equal(report.receiptId,'depot-oct09');assert.equal(report.kg,null);assert.equal(report.revenueMinor,null);assert.equal(report.linkProvenance,'owner-confirmed');
  assert.equal(overview(s).revenueMinor,before.revenueMinor);assert.equal(createAction(s,'harvest-link',{reportId:report.id,receiptId:candidates[0].receiptId}).canConfirm,false);
});
test('depot action preserves duplicate quality lines, creates revenue and receivable, never cash',()=>{
  const s=fresh(),before=overview(s),a=post(s,'depot',{party:'TEST Банановое депо',date:'2026-10-09',adet:'12',greenhouseId:'ozvadi',rollingAlacak:'999999',lines:[{quality:'II',netKg:'100.5',price:'35.20'},{quality:'II',netKg:'80',price:'35.20'}]});
  const r=s.daily.receipts.find(r=>r.id===a.id);assert.equal(r.lines.length,2);assert.equal(r.totalMinor,635360);assert.equal(r.rollingAlacakMinor,99999900);
  const after=overview(s);assert.equal(after.revenueMinor,before.revenueMinor+635360);assert.equal(after.receivableMinor,before.receivableMinor+635360);assert.equal(after.cashReceivedMinor,before.cashReceivedMinor);
});
test('depot line arithmetic never silently rounds a fractional minor unit',()=>{
  const s=fresh(),a=createAction(s,'depot',{party:'TEST Банановое депо',adet:'1',lines:[{quality:'I',netKg:'0.001',price:'0.01'}]});
  assert.equal(a.canConfirm,false);assert.throws(()=>confirmAction(s,a.id),/NEEDS_INPUT/);
});
test('arrival changes known physical stock only, never finance or invoices',()=>{
  const s=fresh(),before=overview(s),a=createAction(s,'inventory-arrival',{stockId:'kno3',quantity:'12.125'});
  assert.equal(s.daily.inventory[0].quantity,180);confirmAction(s,a.id);assert.equal(s.daily.inventory[0].quantity,192.125);
  assert.equal(s.stock,s.daily.inventory);for(const key of ['payableMinor','cashPaidMinor','operatingExpenseMinor'])assert.equal(overview(s)[key],before[key]);assert.equal(s.daily.invoices[0].arrivalConfirmed,false);
});
test('arrival against unknown opening stock preserves UNKNOWN instead of inventing a balance',()=>{
  const s=fresh();post(s,'inventory-arrival',{stockId:'film-stock',quantity:'12'});const row=s.daily.inventory.find(s=>s.id==='film-stock');
  assert.equal(row.quantity,null);assert.equal(row.quantityMinor,null);assert.equal(row.movements.at(-1).quantity,12);
});
test('consumption requires a known sufficient balance and uses exact quantity units',()=>{
  const s=fresh();post(s,'inventory-consumption',{stockId:'kno3',quantity:'0.125'});assert.equal(s.daily.inventory[0].quantity,179.875);
  assert.equal(createAction(s,'inventory-consumption',{stockId:'kno3',quantity:'180'}).canConfirm,false);
  assert.equal(createAction(s,'inventory-consumption',{stockId:'film-stock',quantity:'1'}).canConfirm,false);
});
test('transfer has two equal opposite quantity legs, conserves known balances and never posts money',()=>{
  const s=fresh(),before=overview(s),balance=s.daily.inventory.filter(x=>x.name==='KNO₃').reduce((n,x)=>n+x.quantityMinor,0);
  const a=post(s,'inventory-transfer',{stockId:'kno3',quantity:'7.125',toPlace:'Özvadi'});
  const total=s.daily.inventory.filter(x=>x.name==='KNO₃').reduce((n,x)=>n+x.quantityMinor,0);assert.equal(total,balance);
  const legs=s.daily.inventory.flatMap(x=>x.movements).filter(x=>x.transferId===a.id);assert.equal(legs.length,2);assert.equal(legs.reduce((n,x)=>n+x.quantityMinor,0),0);
  assert.equal(overview(s).cashPaidMinor,before.cashPaidMinor);assert.equal(overview(s).payableMinor,before.payableMinor);
});
test('transfer to a newly described place preserves unknown opening balance',()=>{
  const s=fresh();post(s,'inventory-transfer',{stockId:'kno3',quantity:'10',toPlace:'DEMO Новый склад'});
  const target=s.daily.inventory.at(-1);assert.equal(target.quantity,null);assert.equal(target.movements[0].quantity,10);assert.equal(s.daily.inventory[0].quantity,170);
});
test('stock confirmation revalidates availability after another action consumes the balance',()=>{
  const s=fresh(),a=createAction(s,'inventory-consumption',{stockId:'kno3',quantity:'170'});post(s,'inventory-consumption',{stockId:'kno3',quantity:'20'});
  assert.throws(()=>confirmAction(s,a.id),/NEEDS_INPUT/);assert.equal(s.daily.inventory[0].quantity,160);
});
test('season harvest projection excludes September and keeps finance party totals reconciled',()=>{
  const s=fresh(),rows=harvestRows(s);assert.equal(rows.reduce((n,r)=>n+r.kg,0),2260);assert.equal(rows.reduce((n,r)=>n+r.revenueMinor,0),8302000);
  const parties=financeParties(s),o=overview(s);assert.equal(parties.receivables.reduce((n,p)=>n+p.amountMinor,0),o.receivableMinor);assert.equal(parties.payables.reduce((n,p)=>n+p.amountMinor,0),o.payableMinor);assert.equal(parties.receivables[0].id,'depot');
});
test('migration adds daily while preserving old drafts, tasks, review records and failures',()=>{
  const s=initialState();s.draft.text='Незаконченный ввод';s.failNext=true;const before=structuredClone(s);ensureDaily(s);
  for(const key of Object.keys(before))assert.deepEqual(s[key],before[key]);
  const ref=s.daily;ensureDaily(s);assert.equal(s.daily,ref);projectDaily(s);assert.equal(s.stock,s.daily.inventory);assert.equal(s.draft.text,'Незаконченный ввод');
});
test('local reload retains pending actions, immutable source and confirmed ledger, no duplicate initialization',()=>{
  const s=initialState();projectDaily(s);post(s,'expense',expenseInput);const a=createAction(s,'harvest',{greenhouseId:'imamli',bunches:'16'});
  const memory=new Map(),storage={setItem:(k,v)=>memory.set(k,v),getItem:k=>memory.get(k)};assert.equal(persist(storage,s),true);
  const restored=loadState(storage);projectDaily(restored);assert.deepEqual(overview(restored),overview(s));assert.equal(restored.daily.actions.find(x=>x.id===a.id).status,'pending');assert.equal(restored.stock,restored.daily.inventory);
});
test('legacy harvest confirmation bridges exactly once into the coherent daily ledger',()=>{
  const s=initialState();projectDaily(s);const before=overview(s),p=s.proposals.find(p=>p.id==='depot-review');
  legacyConfirm(s,p.id,p.revision);syncLegacyConfirmation(s,p);const after=overview(s);
  assert.equal(after.revenueMinor,before.revenueMinor+3720000);assert.equal(after.receivableMinor,before.receivableMinor+3720000);assert.equal(after.cashReceivedMinor,before.cashReceivedMinor);
  assert.equal(harvestRows(s).find(r=>r.id==='ozvadi').kg,2560);syncLegacyConfirmation(s,p);assert.deepEqual(overview(s),after);
});
test('legacy stock confirmation bridges already-mutated alias without a second arrival',()=>{
  const s=initialState();projectDaily(s);const p=s.proposals.find(p=>p.id==='stock-review');legacyConfirm(s,p.id,p.revision);syncLegacyConfirmation(s,p);
  const row=s.daily.inventory.find(r=>r.id==='kno3');assert.equal(row.quantity,280);assert.equal(row.quantityMinor,280000);syncLegacyConfirmation(s,p);assert.equal(row.quantity,280);
});
test('legacy payable confirmation bridges debt once and does not change cash or expense',()=>{
  const s=initialState();projectDaily(s);const before=overview(s),p=s.proposals.find(p=>p.id==='clarify');p.amount=7400;legacyConfirm(s,p.id,p.revision);syncLegacyConfirmation(s,p);
  const after=overview(s);assert.equal(after.payableMinor,before.payableMinor+740000);assert.equal(after.cashPaidMinor,before.cashPaidMinor);assert.equal(after.operatingExpenseMinor,before.operatingExpenseMinor);
});
test('all source references in confirmed ledger and field reports are present',()=>{
  const d=createDailyDemo(),ids=new Set(d.sources.map(s=>s.id));assert.equal(ids.size,d.sources.length);
  for(const r of [...d.receipts,...d.expenses,...d.payments,...d.obligations,...d.fieldReports,...d.invoices])assert.ok(ids.has(r.sourceId),r.sourceId);
});


test('structured source preserves original harvest count and event date across review corrections',()=>{
  const s=fresh(),values={greenhouseId:'imamli',bunches:'18',date:'2026-10-08'},a=createAction(s,'harvest',values);
  const src=s.daily.sources.find(x=>x.id===a.sourceId),original=structuredClone(src);
  values.bunches='999';updateAction(s,a.id,{bunches:'21',greenhouseId:'ozvadi',date:'2026-10-09',dateAssumed:false});
  assert.deepEqual(src,original);assert.equal(src.structuredOriginal.bunches,'18');assert.equal(src.structuredOriginal.greenhouseId,'imamli');
  assert.equal(src.eventDateOriginal,'2026-10-08');assert.equal(src.eventDateCandidate,'2026-10-08');assert.equal(src.date,'2026-10-08');
  assert.match(src.capturedAt,/^\d{4}-\d{2}-\d{2}T/);assert.equal(src.captureClock,'browser-local-capture');
  confirmAction(s,a.id);assert.equal(s.daily.fieldReports.at(-1).bunches,21);assert.equal(s.daily.fieldReports.at(-1).date,'2026-10-09');
});
test('structured source retains original material and quantity independently of reviewed inventory selection',()=>{
  const s=fresh(),a=createAction(s,'inventory-arrival',{stockId:'kno3',quantity:'25'}),src=s.daily.sources.find(x=>x.id===a.sourceId),original=structuredClone(src);
  updateAction(s,a.id,{stockId:'calcium',quantity:'8'});confirmAction(s,a.id);
  assert.deepEqual(src,original);assert.equal(src.structuredOriginal.stockId,'kno3');assert.equal(src.structuredOriginal.quantity,'25');
  assert.equal(s.daily.inventory.find(r=>r.id==='kno3').quantity,180);assert.equal(s.daily.inventory.find(r=>r.id==='calcium').quantity,83);
});
test('structured source deep-clones original receipt lines before caller mutation and review edits',()=>{
  const s=fresh(),input={party:'TEST Банановое депо',adet:'12',lines:[{quality:'I',netKg:'120',price:'30'}]},a=createAction(s,'depot',input);
  const src=s.daily.sources.find(x=>x.id===a.sourceId),original=structuredClone(src);input.lines[0].netKg='999';
  updateAction(s,a.id,{adet:'14',lines:[{quality:'II',netKg:'110',price:'28'}]});confirmAction(s,a.id);
  assert.deepEqual(src,original);assert.equal(src.structuredOriginal.lines[0].netKg,'120');assert.equal(src.structuredOriginal.lines[0].quality,'I');
  assert.equal(s.daily.receipts.at(-1).lines[0].netKg,110);assert.equal(s.daily.receipts.at(-1).lines[0].quality,'II');
});
test('assumed business date is an explicit candidate, separate from an original date and capture clock',()=>{
  const s=fresh(),a=createAction(s,'expense',expenseInput),src=s.daily.sources.find(x=>x.id===a.sourceId),timestamp=src.capturedAt;
  assert.equal(src.eventDateOriginal,null);assert.equal(src.eventDateCandidate,'2026-10-09');assert.equal(src.dateAssumed,true);
  updateAction(s,a.id,{date:'2026-10-07',dateAssumed:false});assert.equal(src.eventDateOriginal,null);assert.equal(src.eventDateCandidate,'2026-10-09');assert.equal(src.capturedAt,timestamp);
});
test('payment without an explicit classification remains ambiguous and cannot move cash',()=>{
  const s=fresh(),before=overview(s),input={purpose:'Деньги переданы',amount:'1200',currency:'TRY',direction:'out',accountId:'cash-try'};
  for(const category of [undefined,'unclassified','']) {const a=createAction(s,'payment',{...input,category});assert.equal(a.canConfirm,false);assert.throws(()=>confirmAction(s,a.id),/NEEDS_INPUT/);}
  assert.equal(overview(s).cashPaidMinor,before.cashPaidMinor);const a=createAction(s,'payment',input);updateAction(s,a.id,{category:'advance'});confirmAction(s,a.id);
  assert.equal(overview(s).cashPaidMinor,before.cashPaidMinor+120000);assert.equal(overview(s).operatingExpenseMinor,before.operatingExpenseMinor);
});
test('wholly empty expense does not create a meaningless source or consume an action identity',()=>{
  const s=fresh(),sourceCount=s.daily.sources.length,nextId=s.daily.nextId;
  for(const input of [{},{raw:'  ',purpose:' ',currency:'TRY',date:'2026-10-09'}])assert.throws(()=>createAction(s,'expense',input),/Добавьте исходное сообщение/);
  assert.equal(s.daily.sources.length,sourceCount);assert.equal(s.daily.actions.length,0);assert.equal(s.daily.nextId,nextId);
  assert.equal(createAction(s,'expense',{raw:'Нужна запись расхода, сумму уточню'}).status,'pending');
});


test('quality-line ADET stays explicitly UNKNOWN and is never distributed from receipt total or weight',()=>{
  const d=createDailyDemo();for(const receipt of d.receipts){assert.ok(receipt.adet>0);for(const line of receipt.lines){assert.equal(line.adet,null);assert.equal(line.adetProvenance,'unknown');}}
  const s=fresh(),a=post(s,'depot',{party:'TEST Банановое депо',adet:'10',lines:[{quality:'I',netKg:'100',price:'30'},{quality:'II',netKg:'50',price:'25'}]});
  const r=s.daily.receipts.find(r=>r.id===a.id);assert.equal(r.adet,10);assert.ok(r.lines.every(l=>l.adet===null&&l.adetProvenance==='unknown'));
});
test('only explicit confirmed quality-line ADET is retained, without filling missing neighboring lines',()=>{
  const s=fresh(),a=post(s,'depot',{party:'TEST Банановое депо',adet:'10',lines:[{quality:'I',netKg:'100',price:'30',adet:'6'},{quality:'II',netKg:'50',price:'25'}]});
  const r=s.daily.receipts.find(r=>r.id===a.id);assert.equal(r.lines[0].adet,6);assert.equal(r.lines[0].adetProvenance,'source-fact');assert.equal(r.lines[1].adet,null);
  const invalid=createAction(s,'depot',{party:'TEST Банановое депо',adet:'10',lines:[{quality:'I',netKg:'100',price:'30',adet:'1.5'}]});assert.equal(invalid.canConfirm,false);
});
