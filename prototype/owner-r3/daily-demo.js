// Bounded, synthetic frontend ledger. Never a production accounting or parsing service.
// Monetary values are integer minor units. Sources, estimates, obligations and cash stay separate.
export const DEMO_TODAY = '2026-10-09';
export const DAILY_VERSION = 1;
const clone = (x) => structuredClone(x);
const clean = (x) => String(x ?? '').trim();
const sum = (rows, key) => rows.reduce((n, row) => exact(n + (row[key] ?? 0)), 0);
function exact(n) { if (!Number.isSafeInteger(n)) throw Error('UNSAFE_INTEGER'); return n; }
export function toMinor(raw) {
  const value = clean(raw).replace(/\s/g, '').replace(',', '.');
  if (!/^\d{1,12}(?:\.\d{1,2})?$/.test(value)) throw Error('INVALID_AMOUNT');
  const [whole, fraction = ''] = value.split('.');
  const result = exact(Number(whole) * 100 + Number(fraction.padEnd(2, '0')));
  if (result <= 0) throw Error('INVALID_AMOUNT');
  return result;
}
export function fromMinor(value) { return exact(value) / 100; }
export function formatMinor(value, currency = 'TRY') {
  if (value == null) return 'UNKNOWN';
  exact(value);
  return new Intl.NumberFormat('ru-RU', {style: 'currency', currency, maximumFractionDigits: 2}).format(value / 100);
}
function quantityMinor(raw) {
  const value = clean(raw).replace(/\s/g, '').replace(',', '.');
  if (!/^\d{1,9}(?:\.\d{1,3})?$/.test(value)) throw Error('INVALID_QUANTITY');
  const [whole, fraction = ''] = value.split('.');
  const result = exact(Number(whole) * 1000 + Number(fraction.padEnd(3, '0')));
  if (result <= 0) throw Error('INVALID_QUANTITY');
  return result;
}
function validDate(value) {
  return /^2026-\d{2}-\d{2}$/.test(value) && !Number.isNaN(Date.parse(value)) && new Date(value).toISOString().slice(0,10) === value;
}
const greenhouseList = () => [
  {id: 'ozvadi', name: 'Özvadi'}, {id: 'imamli', name: 'İmamlı'}, {id: 'yesiloz', name: 'Yeşilöz'},
];
function source(id, date, raw, extra = {}) {
  return {id, date, raw, synthetic: true, provenance: 'source-fact',
    eventDateOriginal: date, capturedAt: `${date}T12:00:00.000Z`, captureClock: 'synthetic-fixture', ...extra};
}
function receiptLine(id, quality, netKg, price, adet = null) {
  if(adet != null && (!/^\d+$/.test(String(adet)) || !Number.isSafeInteger(Number(adet)) || Number(adet)<=0))throw Error('INVALID_LINE_ADET');
  const quantity = quantityMinor(netKg), priceMinor = toMinor(price);
  const numerator = exact(quantity * priceMinor);
  if (numerator % 1000 !== 0) throw Error('LINE_REQUIRES_EXPLICIT_ROUNDING');
  return {id, quality, adet:adet == null ? null : Number(adet), adetProvenance:adet == null ? 'unknown' : 'source-fact', netKg: quantity / 1000, netGrams: quantity, priceMinor, amountMinor: numerator / 1000, provenance: 'source-fact'};
}
function seededReceipt(id, date, adet, greenhouseId, lineSpecs, rollingAlacakMinor, extra = {}) {
  const lines = lineSpecs.map(([quality, kg, price, adet], index) => receiptLine(`${id}-line-${index+1}`, quality, kg, price, adet));
  return {id, date, partyId:'depot', party: 'TEST Банановое депо', currency: 'TRY', adet, greenhouseId, lines,
    netKg: sum(lines, 'netGrams') / 1000, totalMinor: sum(lines, 'amountMinor'), rollingAlacakMinor,
    sourceId: `source-${id}`, status: 'confirmed', provenance: 'source-fact', allocations: [], ...extra};
}
function stockRow(id, name, quantity, unit, place, movements = []) {
  return {id, name, category: 'Удобрения', quantity, quantityMinor: quantity == null ? null : quantityMinor(quantity), unit, place,
    cost: null, costMinor: null, provenance: quantity == null ? 'unknown' : 'source-fact', movements};
}
export function createDailyDemo() {
  const receipts = [
    seededReceipt('depot-sep04','2026-09-04',82,'ozvadi',[['I',640,'33.50'],['II',180,'24.00']],2576000),
    seededReceipt('depot-sep08','2026-09-08',154,'imamli',[['I',1190,'35.00'],['II',440,'28.00']],7973000, {
      originalAdet: '151? / 154', adetProvenance: 'owner-correction', ownerCorrection: {field:'adet',value:154,greenhouseId:'imamli',originalRetained:true},
    }),
    seededReceipt('depot-sep17','2026-09-17',83,null,[['I',640,'36.00'],['II',220,'29.50'],['II',160,'29.50'],['III',90,'15.00']],7033000, {
      allocations: [
        {greenhouseId:'ozvadi',bunches:29,kg:null,revenueMinor:null,provenance:'derived-estimate'},
        {greenhouseId:'imamli',bunches:38,kg:null,revenueMinor:null,provenance:'derived-estimate'},
        {greenhouseId:'yesiloz',bunches:16,kg:null,revenueMinor:null,provenance:'derived-estimate'},
      ], allocationMethod:'Подтверждённое владельцем расчётное распределение 29 + 38 + 16 = 83 ADET; вес и выручка не распределены.',
    }),
    seededReceipt('depot-sep25','2026-09-25',101,'yesiloz',[['I',830,'36.50'],['II',240,'28.00']],5734500),
    seededReceipt('depot-oct03','2026-10-03',127,'ozvadi',[['I',1080,'38.00'],['II',280,'30.00']],6678500),
    seededReceipt('depot-oct09','2026-10-09',93,'imamli',[['I',710,'39.00'],['II',190,'31.00']],8036500),
  ];
  const expenses = [
    ['transport','2026-09-02','Аренда транспорта',1245000,'DEMO Транспорт','transport'],
    ['fuel','2026-09-06','Топливо',685000,null,'fuel'],
    ['lab','2026-09-11','Лабораторные анализы',480000,'DEMO Агролаб','laboratory'],
    ['pump','2026-09-21','Ремонт насоса',1180000,'DEMO Сервис ручья','repair'],
    ['fertilizer','2026-09-12','Удобрения',3280000,'DEMO Питание сада','fertilizer'],
    ['parts','2026-10-02','Запасные части',960000,'DEMO Детали','parts'],
    ['salary','2026-10-05','Оплата труда: единый расчёт',18890000,'DEMO Бригада Кедр','labor'],
  ].map(([id,date,purpose,amountMinor,party,category]) => ({id:`expense-${id}`,date,purpose,amountMinor,party,category,currency:'TRY',sourceId:`source-${id}`,provenance:'source-fact'}));
  const obligations = receipts.map(r => ({id:`obligation-${r.id}`,direction:'receivable',partyId:'depot',party:r.party,purpose:`Чек ${r.id}`,date:r.date,amountMinor:r.totalMinor,currency:'TRY',sourceId:r.sourceId,receiptId:r.id,provenance:'source-fact'}));
  obligations.push(
    {id:'supplier-purchase',direction:'payable',partyId:'supplier',party:'DEMO Питание сада',purpose:'Одна поставка удобрений',date:'2026-09-12',amountMinor:3280000,currency:'TRY',sourceId:'source-fertilizer',expenseId:'expense-fertilizer',provenance:'source-fact'},
    {id:'pump-payable',direction:'payable',partyId:'repair',party:'DEMO Сервис ручья',purpose:'Ремонт насоса',date:'2026-09-21',amountMinor:1180000,currency:'TRY',sourceId:'source-pump',expenseId:'expense-pump',provenance:'source-fact'},
    {id:'parts-payable',direction:'payable',partyId:'parts',party:'DEMO Детали',purpose:'Запасные части',date:'2026-10-02',amountMinor:960000,currency:'TRY',sourceId:'source-parts',expenseId:'expense-parts',provenance:'source-fact'},
  );
  const payments = [
    {id:'cash-sale-sep13',date:'2026-09-13',direction:'in',amountMinor:4500000,party:'TEST Банановое депо',purpose:'Частичная оплата депо',allocations:[{obligationId:'obligation-depot-sep04',amountMinor:2576000},{obligationId:'obligation-depot-sep08',amountMinor:1924000}]},
    {id:'cash-sale-sep23',date:'2026-09-23',direction:'in',amountMinor:5000000,party:'TEST Банановое депо',purpose:'Частичная оплата депо',allocations:[{obligationId:'obligation-depot-sep08',amountMinor:3473000},{obligationId:'obligation-depot-sep17',amountMinor:1527000}]},
    {id:'cash-sale-oct01',date:'2026-10-01',direction:'in',amountMinor:4000000,party:'TEST Банановое депо',purpose:'Частичная оплата депо',allocations:[{obligationId:'obligation-depot-sep17',amountMinor:2033000},{obligationId:'obligation-depot-sep25',amountMinor:1967000}]},
    {id:'cash-sale-oct07',date:'2026-10-07',direction:'in',amountMinor:2000000,party:'TEST Банановое депо',purpose:'Частичная оплата депо',allocations:[{obligationId:'obligation-depot-sep25',amountMinor:1734500},{obligationId:'obligation-depot-oct03',amountMinor:265500}]},
    {id:'pay-transport',date:'2026-09-02',direction:'out',amountMinor:1245000,purpose:'Аренда транспорта',category:'operating'},
    {id:'pay-fuel',date:'2026-09-06',direction:'out',amountMinor:685000,purpose:'Топливо',category:'operating'},
    {id:'pay-lab',date:'2026-09-11',direction:'out',amountMinor:480000,purpose:'Лаборатория',category:'operating'},
    {id:'pay-fertilizer',date:'2026-09-12',direction:'out',amountMinor:2000000,purpose:'Частичная оплата одной поставки',party:'DEMO Питание сада',allocations:[{obligationId:'supplier-purchase',amountMinor:2000000}],category:'operating'},
    {id:'salary-advance-one',date:'2026-09-10',direction:'out',amountMinor:930000,purpose:'Аванс бригаде',party:'DEMO Бригада Кедр',category:'advance',salarySettlementId:'salary-settlement'},
    {id:'salary-advance-two',date:'2026-09-24',direction:'out',amountMinor:760000,purpose:'Второй аванс бригаде',party:'DEMO Бригада Кедр',category:'advance',salarySettlementId:'salary-settlement'},
    {id:'salary-final-payment',date:'2026-10-05',direction:'out',amountMinor:17200000,purpose:'Доплата по зарплатному расчёту',party:'DEMO Бригада Кедр',category:'operating',salarySettlementId:'salary-settlement'},
    {id:'loan-principal',date:'2026-10-06',direction:'out',amountMinor:2500000,purpose:'Возврат основной суммы займа',party:'DEMO Заимодавец',category:'loan-principal'},
  ].map(p => ({...p,currency:'TRY',accountId:p.direction==='in'?'bank-try':'cash-try',sourceId:`source-${p.id}`,allocations:p.allocations||[],provenance:'source-fact',verification:'owner-reported-not-bank-verified'}));
  const inventory = [
    stockRow('kno3','KNO₃',180,'кг','İmamlı',[
      {id:'stock-arrival',purchaseId:'supplier-purchase',kind:'arrival',label:'Подтверждённый физический приход',date:'2026-09-12',quantity:240},
      {id:'stock-consumption',kind:'consumption',label:'Внесение',date:'2026-09-19',quantity:-40},
      {id:'stock-transfer-out',kind:'transfer',label:'Перемещение в Özvadi',date:'2026-09-28',quantity:-20,transferId:'transfer-sep28'},
    ]),
    stockRow('kno3-sun','KNO₃',20,'кг','Özvadi',[
      {id:'stock-transfer-in',kind:'transfer',label:'Перемещение из İmamlı',date:'2026-09-28',quantity:20,transferId:'transfer-sep28'},
    ]),
    stockRow('calcium','Кальциевая селитра',75,'кг','Yeşilöz',[
      {id:'calcium-arrival',kind:'arrival',label:'Подтверждённый физический приход',date:'2026-09-05',quantity:95},
      {id:'calcium-consumption',kind:'consumption',label:'Внесение',date:'2026-10-04',quantity:-20},
    ]),
    {...stockRow('film-stock','Плёнка усиленная',null,'м²','İmamlı'),category:'Материалы'},
  ];
  const sources = receipts.map(r => source(r.sourceId,r.date,`СИНТЕТИЧЕСКИЙ чек ${r.id}. ${r.originalAdet||r.adet} ADET; строки качества сохранены отдельно; Alacak — накопительный баланс, не поступление денег.`));
  for (const row of [...expenses,...payments]) if (!sources.some(s=>s.id===row.sourceId)) sources.push(source(row.sourceId,row.date,`СИНТЕТИЧЕСКИЙ источник: ${row.purpose}; ${formatMinor(row.amountMinor)}.`));
  sources.push(source('source-supplier-clarification','2026-10-08','СИНТЕТИЧЕСКОЕ уточнение: по той же поставке удобрений осталось 12 800 TRY. Нового прихода и новой оплаты нет.'),
    source('source-invoice','2026-10-08','СИНТЕТИЧЕСКИЙ счёт на сульфат магния. Количество, упаковка, стоимость и физическое прибытие не подтверждены.'));
  sources.push(source('source-field-oct03','2026-10-03','СИНТЕТИЧЕСКИЙ полевой отчёт: Özvadi, 127 веток; вес неизвестен.'),source('source-field-oct09','2026-10-09','СИНТЕТИЧЕСКИЙ полевой отчёт: İmamlı, 93 ветки; вес неизвестен.'));
  for (const row of inventory) for (const movement of row.movements) {
    movement.quantityMinor = exact(Math.round(movement.quantity * 1000));
    movement.sourceId = `source-${movement.id}`;
    sources.push(source(movement.sourceId,movement.date,`СИНТЕТИЧЕСКОЕ движение: ${movement.label}; ${row.name}, ${Math.abs(movement.quantity)} ${row.unit}.`));
  }
  return {
    version:DAILY_VERSION,nextId:1,revision:0,synthetic:true,
    period:{from:'2026-09-01',to:DEMO_TODAY,label:'1 сентября — 9 октября 2026'},
    season:{from:'2026-10-01',to:DEMO_TODAY,label:'1–9 октября 2026'},currency:'TRY',
    completeness:{status:'partial',label:'Только подтверждённые записи DEMO. Это не полный учёт хозяйства.',openingCashKnown:false,operatingCoverageComplete:false,openingReceivableMinor:0,openingPayableMinor:0,greenhouseAllocation:'partial'},
    greenhouses:greenhouseList(),accounts:[{id:'cash-try',name:'DEMO Касса · TRY',currency:'TRY'},{id:'bank-try',name:'DEMO Банк · TRY',currency:'TRY'}],
    sources,actions:[],receipts,expenses,payments,obligations,inventory,
    fieldReports:[
      {id:'field-oct03',date:'2026-10-03',greenhouseId:'ozvadi',bunches:127,kg:null,revenueMinor:null,receiptId:'depot-oct03',linkProvenance:'owner-confirmed',sourceId:'source-field-oct03',provenance:'source-fact'},
      {id:'field-oct09',date:'2026-10-09',greenhouseId:'imamli',bunches:93,kg:null,revenueMinor:null,receiptId:null,sourceId:'source-field-oct09',provenance:'source-fact'},
    ],
    links:[{id:'link-oct03',reportId:'field-oct03',receiptId:'depot-oct03',provenance:'owner-confirmed'}],
    salarySettlements:[{id:'salary-settlement',date:'2026-10-05',party:'DEMO Бригада Кедр',totalMinor:18890000,paymentIds:['salary-advance-one','salary-advance-two','salary-final-payment'],expenseId:'expense-salary',postsCash:false,provenance:'owner-confirmed-summary'}],
    supplierClarifications:[{id:'supplier-balance-note',date:'2026-10-08',obligationId:'supplier-purchase',remainingMinor:1280000,sourceId:'source-supplier-clarification',createsDelivery:false,createsPayment:false}],
    invoices:[{id:'invoice-pending',date:'2026-10-08',product:'Сульфат магния',quantity:null,unit:null,costMinor:null,arrivalConfirmed:false,sourceId:'source-invoice',provenance:'source-fact'}],
    legacyApplied:[],
  };
}
export function ensureDaily(state) {
  if (!state.daily || state.daily.version !== DAILY_VERSION) state.daily = createDailyDemo();
  return state.daily;
}
function dailyOf(state) { return state.daily || state; }
function remaining(d, obligation, asOf = null) {
  const allocated = d.payments.filter(p=>!asOf || p.date<=asOf).flatMap(p=>p.allocations||[]).filter(a=>a.obligationId===obligation.id).reduce((n,a)=>exact(n+a.amountMinor),0);
  return exact(obligation.amountMinor-allocated);
}
export function overview(state) {
  const d = dailyOf(state), inside = r => r.date >= d.period.from && r.date <= d.period.to && r.currency === d.currency;
  const receipts=d.receipts.filter(inside), expenses=d.expenses.filter(inside), payments=d.payments.filter(inside);
  const revenueMinor=sum(receipts,'totalMinor'), operatingExpenseMinor=sum(expenses,'amountMinor');
  const debts = d.obligations.filter(o=>o.currency===d.currency && o.date<=d.period.to);
  return {period:clone(d.period),currency:d.currency,completeness:clone(d.completeness),
    revenueMinor,operatingExpenseMinor,operatingResultMinor:null,recordedOperatingResultMinor:exact(revenueMinor-operatingExpenseMinor),
    cashReceivedMinor:sum(payments.filter(p=>p.direction==='in'),'amountMinor'),cashPaidMinor:sum(payments.filter(p=>p.direction==='out'),'amountMinor'),
    receivableMinor:debts.filter(o=>o.direction==='receivable').reduce((n,o)=>exact(n+remaining(d,o,d.period.to)),0),
    payableMinor:debts.filter(o=>o.direction==='payable').reduce((n,o)=>exact(n+remaining(d,o,d.period.to)),0),cashBalanceMinor:null,
    excludedCurrencies:[...new Set([...d.receipts,...d.expenses,...d.payments,...d.obligations].map(r=>r.currency).filter(c=>c!==d.currency))],
    sourceCount:d.sources.length,pendingCount:d.actions.filter(a=>a.status==='pending').length};
}
export function financeParties(state) {
  const d=dailyOf(state), result={receivables:[],payables:[]};
  for (const o of d.obligations.filter(o=>o.currency===d.currency && o.date<=d.period.to)) {
    const value=remaining(d,o,d.period.to); if (!value) continue;
    const rows=o.direction==='receivable'?result.receivables:result.payables;
    const id=o.partyId||o.party; let row=rows.find(r=>r.id===id);
    if (!row) {row={id,name:o.party,amountMinor:0,amount:0,currency:o.currency,meaning:o.direction==='receivable'?'Подтверждённые неоплаченные продажи':'Подтверждённые обязательства',date:'Срок не указан'};rows.push(row);}
    row.amountMinor=exact(row.amountMinor+value);row.amount=fromMinor(row.amountMinor);
  }
  return result;
}
export function harvestRows(state, {seasonOnly=true}={}) {
  const d=dailyOf(state), period=seasonOnly?d.season:d.period, inside=r=>r.date>=period.from&&r.date<=period.to;
  return d.greenhouses.map(g=>{
    const receipts=d.receipts.filter(r=>inside(r)&&r.greenhouseId===g.id);
    const unknown=d.receipts.some(r=>inside(r)&&r.allocations.some(a=>a.greenhouseId===g.id && (a.kg==null||a.revenueMinor==null)));
    const reports=d.fieldReports.filter(r=>inside(r)&&r.greenhouseId===g.id);
    const quality=['I','II','III','unknown'].map(q=>receipts.flatMap(r=>r.lines).filter(l=>l.quality===q).reduce((n,l)=>n+l.netGrams,0)/1000);
    const knownKg=receipts.reduce((n,r)=>n+Math.round(r.netKg*1000),0)/1000;
    const revenueMinor=sum(receipts,'totalMinor');
    return {id:g.id,name:g.name,kg:knownKg,knownKg,revenue:fromMinor(revenueMinor),revenueMinor,
      countedKg:receipts.filter(r=>r.adet>0).reduce((n,r)=>n+Math.round(r.netKg*1000),0)/1000,
      bunches:receipts.reduce((n,r)=>n+r.adet,0),fieldBunches:reports.reduce((n,r)=>n+r.bunches,0),quality,
      kgComplete:!unknown,revenueComplete:!unknown,totalKg:unknown?null:knownKg,totalRevenueMinor:unknown?null:revenueMinor,provenance:unknown?'partial-source-facts':'source-fact'};
  });
}
export function projectDaily(state) {
  ensureDaily(state);const parties=financeParties(state);
  state.receivables=parties.receivables;state.payables=parties.payables;state.stock=state.daily.inventory;
  return state;
}
function validate(d, kind, input) {
  const errors=[], need=(ok,message)=>{if(!ok)errors.push(message);};
  need(validDate(input.date),'Уточните дату');
  if (['expense','payment','obligation'].includes(kind)) {
    need(!!clean(input.purpose),'Укажите назначение');
    try {input.amountMinor=toMinor(input.amount);} catch {errors.push('Укажите положительную сумму, не более двух знаков после запятой');}
    need(['TRY','EUR','USD'].includes(input.currency),'Выберите валюту TRY, EUR или USD');
  }
  if(kind==='payment') {
    need(['in','out'].includes(input.direction),'Выберите поступление или выплату');
    need(['operating','advance','loan-principal'].includes(input.category),'Уточните вид платежа: обычный платёж, аванс или основная сумма займа');
    const account=d.accounts.find(a=>a.id===input.accountId);
    need(!!account && account.currency===input.currency,'Выберите счёт в валюте платежа');
    if(input.obligationId) {
      const obligation=d.obligations.find(o=>o.id===input.obligationId);
      need(!!obligation,'Выберите существующее обязательство');
      if(obligation) {
        need(obligation.currency===input.currency,'Валюта платежа должна совпадать с обязательством');
        need((obligation.direction==='payable'?'out':'in')===input.direction,'Направление платежа не совпадает с обязательством');
        need(input.amountMinor<=remaining(d,obligation),'Платёж превышает остаток обязательства');
        need(!input.party||input.party===obligation.party,'Контрагент платежа не совпадает с обязательством');
      }
    }
  }
  if(kind==='obligation') {need(!!clean(input.party),'Укажите кредитора или дебитора');need(['payable','receivable'].includes(input.direction),'Выберите направление долга');}
  if(kind==='harvest') {
    need(d.greenhouses.some(g=>g.id===input.greenhouseId),'Выберите теплицу');
    need(/^\d+$/.test(String(input.bunches))&&Number.isSafeInteger(Number(input.bunches))&&Number(input.bunches)>0,'Укажите целое количество веток');
  }
  if(kind==='depot') {
    need(!!clean(input.party),'Укажите депо');need(/^\d+$/.test(String(input.adet))&&Number.isSafeInteger(Number(input.adet))&&Number(input.adet)>0,'Укажите целое ADET');
    need(!input.greenhouseId||d.greenhouses.some(g=>g.id===input.greenhouseId),'Выберите теплицу или оставьте смесь');
    need(Array.isArray(input.lines)&&input.lines.length>0,'Добавьте строки качества');
    if(Array.isArray(input.lines))for(const [i,l]of input.lines.entries()) {
      need(!!clean(l.quality),`Укажите качество строки ${i+1}`);
      try {receiptLine(`line-${i}`,l.quality,l.netKg,l.price,l.adet);}catch{errors.push(`Проверьте вес и цену строки ${i+1}; дробная копейка требует отдельного уточнения`);}
    }
    if(clean(input.rollingAlacak))try{input.rollingAlacakMinor=/^0(?:[.,]0{1,2})?$/.test(clean(input.rollingAlacak))?0:toMinor(input.rollingAlacak);}catch{errors.push('Проверьте Alacak');}
  }
  if(kind.startsWith('inventory-')) {
    const stock=d.inventory.find(r=>r.id===input.stockId);need(!!stock,'Выберите материал и место');
    try{input.quantityMinor=quantityMinor(input.quantity);}catch{errors.push('Укажите количество в единице выбранного материала');}
    if(stock&&kind!=='inventory-arrival')need(stock.quantityMinor!=null&&stock.quantityMinor>=input.quantityMinor,'Нужен известный достаточный остаток');
    if(kind==='inventory-transfer')need(!!clean(input.toPlace)&&input.toPlace!==stock?.place,'Выберите другое место назначения');
  }
  if(kind==='harvest-link') {
    const report=d.fieldReports.find(r=>r.id===input.reportId),receipt=d.receipts.find(r=>r.id===input.receiptId);
    need(!!report&&!!receipt,'Выберите отчёт и чек');need(!report?.receiptId,'Этот срез уже связан с чеком');
  }
  return errors;
}
const ACTION_KINDS=['expense','payment','obligation','harvest','depot','inventory-arrival','inventory-consumption','inventory-transfer','harvest-link'];
export function createAction(state,kind,values={}) {
  if(!ACTION_KINDS.includes(kind))throw Error('UNKNOWN_ACTION');
  if(kind==='expense' && !['raw','purpose','amount','party'].some(key=>clean(values[key])))throw Error('Добавьте исходное сообщение или назначение и сумму расхода.');
  const d=ensureDaily(state),id=`daily-action-${d.nextId++}`;
  const input={...clone(values),date:values.date||DEMO_TODAY,dateAssumed:values.dateAssumed??!values.date};
  const raw=clean(values.raw)||[values.purpose,values.amount,values.currency].filter(Boolean).join(' ')||`Ручной DEMO ввод: ${kind}`;
  const src=source(`source-${id}`,input.date,raw,{provenance:'owner-entry',
    structuredOriginal:clone(input), eventDateOriginal:clean(values.date)||null, eventDateCandidate:input.date,
    dateAssumed:input.dateAssumed, capturedAt:new Date().toISOString(), captureClock:'browser-local-capture'});
  d.sources.push(src);
  const clarifications=validate(d,kind,input);
  const action={id,kind,input,sourceId:src.id,status:'pending',revision:1,clarifications,canConfirm:clarifications.length===0,
    provenance:'manual-candidate',dateAssumed:input.dateAssumed,postsCash:kind==='payment'};
  d.actions.unshift(action);d.revision++;return action;
}
export function updateAction(state,id,patch) {
  const d=ensureDaily(state),a=d.actions.find(a=>a.id===id);
  if(!a||a.status!=='pending')throw Error('READ_ONLY');
  a.input={...a.input,...clone(patch)};a.revision++;a.clarifications=validate(d,a.kind,a.input);a.canConfirm=!a.clarifications.length;
  // Original source deliberately remains unchanged by a correction.
  a.provenance='owner-correction';d.revision++;return a;
}
function addInventory(d,a) {
  const i=a.input,row=d.inventory.find(r=>r.id===i.stockId),quantity=i.quantityMinor/1000;
  const change=(target,delta,suffix,label)=>{
    if(target.quantityMinor!=null){target.quantityMinor=exact(target.quantityMinor+delta);target.quantity=target.quantityMinor/1000;}
    target.movements.push({id:`${a.id}-${suffix}`,kind:a.kind.replace('inventory-',''),label,quantity:delta/1000,quantityMinor:delta,date:i.date,sourceId:a.sourceId,transferId:a.kind==='inventory-transfer'?a.id:null});
  };
  if(a.kind==='inventory-arrival')change(row,i.quantityMinor,'in','Демо-подтверждённый приход');
  if(a.kind==='inventory-consumption')change(row,-i.quantityMinor,'out','Демо-подтверждённое внесение');
  if(a.kind==='inventory-transfer') {
    let target=d.inventory.find(r=>r.name===row.name&&r.unit===row.unit&&r.place===i.toPlace);
    if(!target){target=stockRow(`stock-${a.id}`,row.name,null,row.unit,i.toPlace);target.category=row.category;d.inventory.push(target);}
    change(row,-i.quantityMinor,'out',`Перемещение в ${i.toPlace}`);change(target,i.quantityMinor,'in',`Перемещение из ${row.place}`);
  }
  return {stockId:row.id,quantity};
}
export function confirmAction(state,id,revision) {
  const d=ensureDaily(state),a=d.actions.find(a=>a.id===id);
  if(!a)throw Error('MISSING_ACTION');if(a.status==='confirmed')return a;
  if(a.status!=='pending')throw Error('READ_ONLY');if(revision!=null&&revision!==a.revision)throw Error('STALE');
  a.clarifications=validate(d,a.kind,a.input);a.canConfirm=!a.clarifications.length;
  if(!a.canConfirm)throw Error('NEEDS_INPUT');
  const i=a.input,base={id:a.id,date:i.date,currency:i.currency||d.currency,sourceId:a.sourceId,provenance:'owner-confirmed',dateAssumed:i.dateAssumed};
  if(a.kind==='expense')d.expenses.push({...base,purpose:clean(i.purpose),amountMinor:i.amountMinor,party:clean(i.party)||null,category:i.category||'operating'});
  if(a.kind==='payment') {
    const obligation=d.obligations.find(o=>o.id===i.obligationId);
    d.payments.push({...base,purpose:clean(i.purpose),amountMinor:i.amountMinor,party:clean(i.party)||obligation?.party||null,direction:i.direction,accountId:i.accountId,category:i.category||'unclassified',verification:'owner-reported-not-bank-verified',allocations:obligation?[{obligationId:obligation.id,amountMinor:i.amountMinor}]:[]});
  }
  if(a.kind==='obligation')d.obligations.push({...base,purpose:clean(i.purpose),amountMinor:i.amountMinor,party:clean(i.party),partyId:clean(i.party),direction:i.direction});
  if(a.kind==='harvest')d.fieldReports.push({...base,greenhouseId:i.greenhouseId,bunches:Number(i.bunches),kg:null,revenueMinor:null,receiptId:null});
  if(a.kind==='depot') {
    const lines=i.lines.map((l,index)=>receiptLine(`${a.id}-line-${index+1}`,clean(l.quality),l.netKg,l.price,l.adet));
    const r={...base,party:clean(i.party),partyId:clean(i.party)==='TEST Банановое депо'?'depot':clean(i.party),adet:Number(i.adet),greenhouseId:i.greenhouseId||null,lines,netKg:sum(lines,'netGrams')/1000,totalMinor:sum(lines,'amountMinor'),rollingAlacakMinor:i.rollingAlacakMinor??null,allocations:[],status:'confirmed'};
    d.receipts.push(r);d.obligations.push({...base,id:`obligation-${a.id}`,direction:'receivable',party:r.party,partyId:r.partyId,purpose:'Подтверждённая продажа по чеку',amountMinor:r.totalMinor,receiptId:r.id});
  }
  if(a.kind.startsWith('inventory-'))a.result=addInventory(d,a);
  if(a.kind==='harvest-link') {
    const report=d.fieldReports.find(r=>r.id===i.reportId);report.receiptId=i.receiptId;report.linkProvenance='owner-confirmed';
    d.links.push({id:a.id,reportId:i.reportId,receiptId:i.receiptId,provenance:'owner-confirmed'});
  }
  a.status='confirmed';a.revision++;d.revision++;projectDaily(state);return a;
}
export function receiptCandidates(state,reportId) {
  const d=dailyOf(state),report=d.fieldReports.find(r=>r.id===reportId);if(!report)return [];
  return d.receipts.filter(r=>r.date===report.date).map(r=>({receiptId:r.id,label:`${r.party} · ${r.date} · ${r.adet} ADET`,provenance:'date-candidate',requiresConfirmation:true}));
}
// One-way bridge for the pre-existing, clearly labelled storyboard review cards.
export function syncLegacyConfirmation(state,p) {
  const d=ensureDaily(state);if(d.legacyApplied.includes(p.id))return projectDaily(state);
  if(p.status!=='confirmed')return state;
  const date=DEMO_TODAY,sourceId=`legacy-source-${p.id}`;
  d.sources.push(source(sourceId,date,p.source||p.title,{provenance:'legacy-demo-storyboard'}));
  if(p.type==='payable')d.obligations.push({id:`legacy-${p.id}`,date,direction:'payable',party:p.title,partyId:p.id,purpose:p.meaning,amountMinor:toMinor(p.amount),currency:p.currency||'TRY',sourceId,provenance:'owner-confirmed'});
  if(p.type==='harvest') {
    const greenhouse=d.greenhouses.find(g=>g.id===p.place||g.name===p.place);
    const line=receiptLine(`legacy-${p.id}-line`,'unknown',p.kg,p.price);
    if(line.amountMinor!==toMinor(p.amount))throw Error('LEGACY_RECEIPT_MISMATCH');
    const r={id:`legacy-${p.id}`,date,party:'TEST Банановое депо',partyId:'depot',currency:'TRY',adet:null,greenhouseId:greenhouse?.id||null,lines:[line],netKg:p.kg,totalMinor:line.amountMinor,rollingAlacakMinor:null,allocations:[],sourceId,status:'confirmed',provenance:'owner-confirmed'};
    d.receipts.push(r);d.obligations.push({id:`obligation-${r.id}`,date,direction:'receivable',party:r.party,partyId:'depot',purpose:'Продажа из DEMO review',amountMinor:r.totalMinor,currency:'TRY',receiptId:r.id,sourceId});
  }
  if(p.type==='stock') {
    // Existing confirm() already updates state.stock. Adopt it without posting twice.
    d.inventory=state.stock;
    for(const row of d.inventory)row.quantityMinor=row.quantity==null?null:(row.quantity===0?0:quantityMinor(row.quantity));
  }
  d.legacyApplied.push(p.id);d.revision++;return projectDaily(state);
}
