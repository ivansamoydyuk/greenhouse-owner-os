// Owner-selected B refinement. A local, synthetic storyboard; no backend or parser.
import {ensureDaily,projectDaily,toMinor,DEMO_TODAY} from './daily-demo.js';
const clone = value => structuredClone(value);
const clean = value => String(value ?? '').trim();
const exact = value => { if (!Number.isSafeInteger(value)) throw Error('UNSAFE_INTEGER'); return value; };
const sum = values => values.reduce((total,value) => exact(total + value),0);
const validDate = value => /^\d{4}-\d{2}-\d{2}$/.test(value) && !Number.isNaN(Date.parse(value)) && new Date(value).toISOString().slice(0,10) === value;
const decimal = minor => `${Math.floor(minor / 100)}.${String(minor % 100).padStart(2,'0')}`;
export const EVIDENCE_MAX_BYTES = 512 * 1024;
export function ensureRefinement(state) {
  const d = ensureDaily(state);
  d.refinement ??= {version:1,nextId:1,settlements:[],evidence:[],linkReviews:[]};
  // Add a separate example once; never rewrite an older source or its 93-branch fact.
  if (!d.refinement.example131Added) {
    const id='field-refinement-131',sourceId='source-field-refinement-131';
    if (!d.fieldReports.some(r=>r.id===id)) {
      d.fieldReports.push({id,date:DEMO_TODAY,greenhouseId:'imamli',bunches:131,kg:null,revenueMinor:null,receiptId:null,sourceId,provenance:'source-fact',synthetic:true,fixtureLabel:'Отдельный синтетический пример 131 / 93'});
      d.sources.push({id:sourceId,date:DEMO_TODAY,raw:'СИНТЕТИЧЕСКИЙ отдельный полевой срез: İmamlı, 131 ветка. Существующий отчёт 93 ветки сохранён без изменений. Совпадение даты с чеком 93 ADET — только кандидат, не связь.',synthetic:true,provenance:'source-fact',eventDateOriginal:DEMO_TODAY,capturedAt:'2026-10-09T12:00:00.000Z',captureClock:'synthetic-fixture'});
    }
    d.refinement.example131Added=true;
  }
  return d.refinement;
}
export function obligationRemaining(state,obligationId) {
  const d=ensureDaily(state),o=d.obligations.find(o=>o.id===obligationId);
  if (!o) throw Error('MISSING_OBLIGATION');
  return exact(o.amountMinor-sum(d.payments.flatMap(p=>p.allocations||[]).filter(a=>a.obligationId===o.id).map(a=>a.amountMinor)));
}
export function settlementContext(state,{partyId,direction,currency='TRY'}) {
  const d=ensureDaily(state);
  if (!['receivable','payable'].includes(direction)) throw Error('INVALID_DIRECTION');
  const obligations=d.obligations.filter(o=>(o.partyId||o.party)===partyId && o.direction===direction && o.currency===currency)
    .map(o=>({...clone(o),remainingMinor:obligationRemaining(state,o.id)}));
  if (!obligations.length) throw Error('MISSING_COUNTERPARTY');
  return {partyId,party:obligations[0].party,direction,currency,cashDirection:direction==='receivable'?'in':'out',
    obligations,availableMinor:sum(obligations.map(o=>Math.max(0,o.remainingMinor))),accounts:d.accounts.filter(a=>a.currency===currency)};
}
export function beginSettlement(state,context) {
  const r=ensureRefinement(state),d=state.daily,c=settlementContext(state,context);
  const pending=r.settlements.find(a=>a.status==='pending'&&a.input.partyId===c.partyId&&a.input.direction===c.direction&&a.input.currency===c.currency);
  if(pending)return pending;
  const id=`settlement-${r.nextId++}`;
  const input={partyId:c.partyId,party:c.party,direction:c.direction,cashDirection:c.cashDirection,currency:c.currency,date:DEMO_TODAY,amount:'',accountId:'',comment:'',allocations:[]};
  const sourceId=`source-${id}`;
  d.sources.push({id:sourceId,date:input.date,raw:`СИНТЕТИЧЕСКОЕ контекстное погашение: ${c.party}. Направление ${c.cashDirection}. Остаток при открытии: ${c.availableMinor} minor ${c.currency}.`,structuredOriginal:clone(input),synthetic:true,provenance:'owner-entry',capturedAt:new Date().toISOString(),eventDateOriginal:input.date,captureClock:'browser-local-capture'});
  const action={id,kind:'settlement',sourceId,status:'pending',revision:1,input,corrections:[],synthetic:true,provenance:'owner-entry'};
  r.settlements.unshift(action);d.revision++;return action;
}
export function updateSettlement(state,id,patch) {
  const r=ensureRefinement(state),a=r.settlements.find(a=>a.id===id);
  if (!a || a.status!=='pending') throw Error('READ_ONLY');
  // The original party, direction and currency are contextual identities, not editable form guesses.
  const allowed=Object.fromEntries(Object.entries(patch).filter(([key])=>['date','amount','accountId','comment','allocations'].includes(key)));
  a.corrections.push({revision:a.revision,input:clone(a.input),provenance:a.provenance});
  a.input={...a.input,...clone(allowed)};a.revision++;a.provenance='owner-correction';state.daily.revision++;return a;
}
export function settlementFill(state,id,{obligationId,all=false}={}) {
  const a=ensureRefinement(state).settlements.find(a=>a.id===id);
  if (!a || a.status!=='pending') throw Error('READ_ONLY');
  const c=settlementContext(state,a.input),open=c.obligations.filter(o=>o.remainingMinor>0);
  if (all) return updateSettlement(state,id,{amount:decimal(c.availableMinor),allocations:open.map(o=>({obligationId:o.id,amount:decimal(o.remainingMinor)}))});
  const o=open.find(o=>o.id===obligationId);if(!o)throw Error('MISSING_OBLIGATION');
  const allocations=(a.input.allocations||[]).filter(x=>x.obligationId!==o.id).concat({obligationId:o.id,amount:decimal(o.remainingMinor)});
  const total=sum(allocations.map(a=>toMinor(a.amount)));
  return updateSettlement(state,id,{amount:decimal(total),allocations});
}
export function previewSettlement(state,id) {
  const r=ensureRefinement(state),a=r.settlements.find(a=>a.id===id);
  if(!a)throw Error('MISSING_SETTLEMENT');
  if(a.status==='confirmed')return {...clone(a.preview),confirmed:true,revision:a.revision,ledgerRevision:state.daily.revision};
  const c=settlementContext(state,a.input),i=a.input,errors=[];let amountMinor=null;
  const need=(condition,message)=>{if(!condition)errors.push(message);};
  need(validDate(i.date),'Укажите существующую дату события');
  try{amountMinor=toMinor(i.amount);}catch{errors.push('Укажите положительную сумму, максимум две цифры после запятой');}
  need(c.availableMinor>0,'У контрагента нет открытого остатка в этой валюте');
  need(amountMinor==null||amountMinor<=c.availableMinor,'Сумма превышает доступный остаток');
  const account=state.daily.accounts.find(x=>x.id===i.accountId);
  need(!!account&&account.currency===c.currency,'Выберите денежный счёт в валюте платежа');
  const allocations=[],seen=new Set();
  need(Array.isArray(i.allocations)&&i.allocations.length>0,'Явно выберите обязательства и сумму для каждого');
  for(const allocation of Array.isArray(i.allocations)?i.allocations:[]) {
    const o=c.obligations.find(o=>o.id===allocation.obligationId);
    need(!!o,'Обязательство должно принадлежать этому контрагенту, направлению и валюте');
    need(!seen.has(allocation.obligationId),'Одно обязательство нельзя распределить дважды');seen.add(allocation.obligationId);
    let value;try{value=toMinor(allocation.amount);}catch{errors.push('Укажите положительную сумму каждого распределения');continue;}
    if(o){need(value<=o.remainingMinor,`Сумма превышает остаток: ${o.purpose}`);need(i.date>=o.date,'Дата платежа не может быть раньше выбранного обязательства');allocations.push({obligationId:o.id,amountMinor:value,purpose:o.purpose,beforeMinor:o.remainingMinor,afterMinor:exact(o.remainingMinor-value)});}
  }
  const allocatedMinor=sum(allocations.map(a=>a.amountMinor));need(amountMinor===allocatedMinor,'Сумма распределений должна точно совпадать с платежом');
  return {id,revision:a.revision,ledgerRevision:state.daily.revision,context:c,account:account?clone(account):null,date:i.date,comment:i.comment,
    amountMinor,allocatedMinor,remainingMinor:amountMinor==null?null:exact(c.availableMinor-amountMinor),allocations,errors,canConfirm:!errors.length,confirmed:false};
}
export function confirmSettlement(state,id,{revision,ledgerRevision}={}) {
  const r=ensureRefinement(state),a=r.settlements.find(a=>a.id===id);
  if(!a)throw Error('MISSING_SETTLEMENT');if(a.status==='confirmed')return a;
  if(a.status!=='pending')throw Error('READ_ONLY');
  if(revision!==a.revision || ledgerRevision!==state.daily.revision)throw Error('STALE');
  const p=previewSettlement(state,id);if(!p.canConfirm)throw Error('NEEDS_INPUT');
  const i=a.input;
  if(state.daily.payments.some(payment=>payment.id===id))throw Error('DUPLICATE_PAYMENT');
  state.daily.payments.push({id,date:i.date,currency:i.currency,sourceId:a.sourceId,provenance:'owner-confirmed',synthetic:true,
    partyId:i.partyId,party:i.party,purpose:i.direction==='receivable'?'Погашение дебиторской задолженности':'Погашение кредиторской задолженности',
    comment:clean(i.comment),amountMinor:p.amountMinor,direction:p.context.cashDirection,accountId:p.account.id,category:'settlement',
    verification:'owner-reported-not-bank-verified',allocations:p.allocations.map(({obligationId,amountMinor})=>({obligationId,amountMinor}))});
  a.preview=clone(p);a.status='confirmed';a.revision++;a.confirmedAt=new Date().toISOString();state.daily.revision++;projectDaily(state);return a;
}
export function harvestPeriod(period='season',today=DEMO_TODAY) {
  if(!validDate(today))throw Error('INVALID_DATE');
  const year=Number(today.slice(0,4)),month=Number(today.slice(5,7));
  if(period==='last12') {
    const start=new Date(Date.UTC(year,month-12,1)),end=new Date(Date.UTC(year,month,1));
    return {id:'last12',from:start.toISOString().slice(0,10),toExclusive:end.toISOString().slice(0,10),label:'Последние 12 месяцев',asOf:today};
  }
  const explicit=typeof period==='string'&&period.match(/^season-(\d{4})$/),start=explicit?Number(explicit[1]):month>=10?year:year-1;
  return {id:`season-${start}`,from:`${start}-10-01`,toExclusive:`${start+1}-10-01`,label:`Сезон ${start}/${String(start+1).slice(-2)}`,asOf:today};
}
export function reportLinks(state,reportId) {return ensureDaily(state).links.filter(l=>l.reportId===reportId);}
export function reportEvidence(state,reportId) {return ensureRefinement(state).evidence.filter(item=>item.reportId===reportId);}
export function reportStatus(state,reportId) {return reportLinks(state,reportId).length?'linked':reportEvidence(state,reportId).length?'review':'missing';}
export const adetLabel = adet => adet==null?'Количество веток неизвестно':`${adet} ADET`;
export function harvestEvents(state,{greenhouseId=null,metric='field',period='season',month='',sort='newest'}={}) {
  const d=ensureDaily(state),p=harvestPeriod(period),inside=r=>r.date>=p.from&&r.date<p.toExclusive&&(!month||r.date.startsWith(month));
  if(!['field','kg','revenue'].includes(metric))throw Error('INVALID_METRIC');
  let events;
  if(metric==='field')events=d.fieldReports.filter(r=>inside(r)&&(!greenhouseId||r.greenhouseId===greenhouseId)).map(r=>({id:r.id,sourceId:r.sourceId,type:'field',date:r.date,greenhouseId:r.greenhouseId,value:r.bunches??null,bunches:r.bunches??null,status:reportStatus(state,r.id),provenance:r.provenance,fixtureLabel:r.fixtureLabel||null}));
  else events=d.receipts.filter(r=>inside(r)&&r.status==='confirmed').flatMap(r=>{
    const amount=metric==='kg'?r.netKg:r.totalMinor;
    if(!greenhouseId||r.greenhouseId===greenhouseId)return [{id:r.id,sourceId:r.sourceId,type:'depot',date:r.date,greenhouseId:r.greenhouseId,value:amount??null,adet:r.adet??null,currency:r.currency,provenance:r.provenance,mixed:!r.greenhouseId}];
    const allocation=(r.allocations||[]).find(a=>a.greenhouseId===greenhouseId);
    if(!allocation)return [];
    const approved=['owner-confirmed','owner-approved-derived-allocation'].includes(allocation.provenance);
    const value=approved?(metric==='kg'?allocation.kg:allocation.revenueMinor):null;
    return [{id:r.id,sourceId:r.sourceId,type:'depot',date:r.date,greenhouseId,value:value??null,adet:r.adet??null,currency:r.currency,provenance:approved?allocation.provenance:'unknown',mixed:true}];
  });
  return events.sort((a,b)=>sort==='oldest'?a.date.localeCompare(b.date)||a.id.localeCompare(b.id):sort==='value'?((b.value??-1)-(a.value??-1))||b.date.localeCompare(a.date):b.date.localeCompare(a.date)||a.id.localeCompare(b.id));
}
export function monthlyHarvestModel(state,options={}) {
  ensureRefinement(state);const metric=options.metric||'field',period=harvestPeriod(options.period||'season'),events=harvestEvents(state,{...options,metric,month:''}),months=[];
  const start=new Date(`${period.from}T00:00:00Z`);
  for(let i=0;i<12;i++) {
    const date=new Date(Date.UTC(start.getUTCFullYear(),start.getUTCMonth()+i,1)),key=date.toISOString().slice(0,7),rows=events.filter(e=>e.date.startsWith(key));
    const supported=rows.filter(e=>e.value!=null&&(metric!=='revenue'||e.currency==='TRY'));
    const total=metric==='kg'?sum(supported.map(e=>Math.round(e.value*1000)))/1000:sum(supported.map(e=>e.value));
    months.push({key,label:new Intl.DateTimeFormat('ru-RU',{month:'short',timeZone:'UTC'}).format(date).replace('.',''),value:supported.length?total:null,knownCount:supported.length,unknownCount:rows.length-supported.length,eventCount:rows.length,future:key>DEMO_TODAY.slice(0,7)});
  }
  const fieldEvents=harvestEvents(state,{...options,metric:'field',month:''}),kgEvents=harvestEvents(state,{...options,metric:'kg',month:''});
  const known=events.filter(e=>e.value!=null&&(metric!=='revenue'||e.currency==='TRY'));
  return {metric,period,months,events:harvestEvents(state,{...options,metric}),knownTotal:known.length?(metric==='kg'?sum(known.map(e=>Math.round(e.value*1000)))/1000:sum(known.map(e=>e.value))):null,
    coverage:{status:'partial',knownCount:known.length,unknownCount:events.length-known.length,withoutReceipt:fieldEvents.filter(e=>e.status!=='linked').length,
      knownKg:sum(kgEvents.filter(e=>e.value!=null).map(e=>Math.round(e.value*1000)))/1000,unknownAllocation:kgEvents.filter(e=>e.value==null).length}};
}
export function candidatesForReport(state,reportId) {
  const d=ensureDaily(state),report=d.fieldReports.find(r=>r.id===reportId);if(!report)return [];
  return d.receipts.filter(r=>r.status==='confirmed').map(r=>({receiptId:r.id,date:r.date,party:r.party,adet:r.adet,adetLabel:adetLabel(r.adet),netKg:r.netKg,totalMinor:r.totalMinor,currency:r.currency,
    sameDate:r.date===report.date,sameGreenhouse:r.greenhouseId===report.greenhouseId,mismatch:r.adet!=null&&r.adet!==report.bunches,
    alreadyLinked:reportLinks(state,reportId).some(l=>l.receiptId===r.id),otherReportCount:d.links.filter(l=>l.receiptId===r.id&&l.reportId!==reportId).length,
    provenance:'candidate-only',requiresConfirmation:true,distance:Math.abs(Date.parse(r.date)-Date.parse(report.date))}))
    .sort((a,b)=>a.distance-b.distance||Number(b.sameGreenhouse)-Number(a.sameGreenhouse)||a.receiptId.localeCompare(b.receiptId));
}
export function attachEvidence(state,reportId,{sourceId=null,file=null,caption='',replacesEvidenceId=null}={},requestId) {
  const r=ensureRefinement(state),d=state.daily;
  if(!d.fieldReports.some(report=>report.id===reportId))throw Error('MISSING_REPORT');
  if(!clean(requestId))throw Error('MISSING_REQUEST_ID');
  const existing=r.evidence.find(item=>item.requestId===requestId);
  if(existing){
    if(existing.reportId!==reportId)throw Error('CONTEXT_MISMATCH');
    const originalSource=[...(state.sources||[]),...d.sources].find(source=>source.id===sourceId);
    const candidateFile=file||originalSource?.file;
    if((sourceId&&existing.sourceId!==sourceId)||JSON.stringify(candidateFile)!==JSON.stringify(existing.file)||clean(caption)!==existing.caption||(existing.replacesEvidenceId||null)!==replacesEvidenceId)throw Error('IDEMPOTENCY_CONFLICT');
    return existing;
  }
  const persistedSource=[...(state.sources||[]),...d.sources].find(source=>source.id===sourceId);
  const originalFile=file||persistedSource?.file;
  if(!originalFile || !['image','pdf'].includes(originalFile.kind))throw Error('PHOTO_OR_PDF_REQUIRED');
  const url=originalFile.url;
  const localFixture=originalFile.fixture===true&&url==='assets/receipt.svg';
  const inline=typeof url==='string'&&/^data:(image\/(png|jpeg|webp|gif)|application\/pdf);base64,[A-Za-z0-9+/]+={0,2}$/.test(url);
  if(!localFixture&&!inline)throw Error('DURABLE_FILE_REQUIRED');
  if(!localFixture&&((originalFile.kind==='image'&&!url.startsWith('data:image/'))||(originalFile.kind==='pdf'&&!url.startsWith('data:application/pdf;'))))throw Error('PHOTO_OR_PDF_REQUIRED');
  if(replacesEvidenceId&&!r.evidence.some(e=>e.id===replacesEvidenceId&&e.reportId===reportId))throw Error('CONTEXT_MISMATCH');
  if((originalFile.size!=null&&originalFile.size>EVIDENCE_MAX_BYTES)||(url?.length||0)>Math.ceil(EVIDENCE_MAX_BYTES*4/3)+100)throw Error('EVIDENCE_TOO_LARGE');
  if(sourceId&&!persistedSource)throw Error('MISSING_SOURCE');
  const id=`harvest-evidence-${r.nextId++}`;
  if(!sourceId){sourceId=`source-${id}`;d.sources.push({id:sourceId,date:d.fieldReports.find(r=>r.id===reportId).date,file:clone(originalFile),raw:clean(caption),caption:clean(caption),replacesEvidenceId,synthetic:true,provenance:'owner-evidence',capturedAt:new Date().toISOString()});}
  const item={id,reportId,sourceId,file:clone(originalFile),caption:clean(caption),requestId,replacesEvidenceId,status:'review',revision:1,synthetic:true,provenance:'owner-attached-evidence',capturedAt:new Date().toISOString()};
  r.evidence.push(item);d.revision++;return item;
}
export function beginReceiptLink(state,reportId,receiptId,{evidenceId=null}={}) {
  const r=ensureRefinement(state),d=state.daily,report=d.fieldReports.find(x=>x.id===reportId),receipt=d.receipts.find(x=>x.id===receiptId&&x.status==='confirmed');
  if(!report||!receipt)throw Error('MISSING_REPORT_OR_RECEIPT');
  if(evidenceId&&!r.evidence.some(e=>e.id===evidenceId&&e.reportId===reportId))throw Error('CONTEXT_MISMATCH');
  const existing=r.linkReviews.find(x=>x.reportId===reportId&&x.receiptId===receiptId&&x.evidenceId===evidenceId);
  if(existing)return existing;
  const review={id:`receipt-link-${r.nextId++}`,reportId,receiptId,evidenceId,revision:1,status:'pending',original:{reportId,receiptId,fieldBunches:report.bunches,receiptAdet:receipt.adet,reportSourceId:report.sourceId,receiptSourceId:receipt.sourceId},synthetic:true};
  r.linkReviews.push(review);d.revision++;return review;
}
export function confirmReceiptLink(state,id,revision,{reviewed=false}={}) {
  const r=ensureRefinement(state),d=state.daily,a=r.linkReviews.find(x=>x.id===id);
  if(!a)throw Error('MISSING_LINK_REVIEW');if(a.status==='confirmed')return a;
  if(a.revision!==revision)throw Error('STALE');if(!reviewed)throw Error('EXPLICIT_REVIEW_REQUIRED');
  const report=d.fieldReports.find(x=>x.id===a.reportId),receipt=d.receipts.find(x=>x.id===a.receiptId&&x.status==='confirmed');
  if(!report||!receipt)throw Error('MISSING_REPORT_OR_RECEIPT');
  if(report.bunches!==a.original.fieldBunches||receipt.adet!==a.original.receiptAdet)throw Error('STALE');
  const evidence=a.evidenceId?r.evidence.find(e=>e.id===a.evidenceId&&e.reportId===report.id):null;
  if(a.evidenceId&&!evidence)throw Error('CONTEXT_MISMATCH');
  if(!d.links.some(l=>l.reportId===a.reportId&&l.receiptId===a.receiptId))d.links.push({id:a.id,reportId:a.reportId,receiptId:a.receiptId,evidenceId:a.evidenceId,provenance:'owner-confirmed',synthetic:true});
  // Legacy primary receipt stays intact; d.links is the authoritative many-to-many relation.
  if(!report.receiptId)report.receiptId=a.receiptId;
  report.linkProvenance='owner-confirmed';
  if(evidence){evidence.status='linked';evidence.receiptIds=[...new Set([...(evidence.receiptIds||[]),receipt.id])];evidence.revision++;}
  a.status='confirmed';a.revision++;d.revision++;return a;
}
