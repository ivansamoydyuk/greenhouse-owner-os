import test from 'node:test';
import assert from 'node:assert/strict';
import {initialState} from '../../prototype/owner-r3/fixtures.js';
import {persist,loadState} from '../../prototype/owner-r3/demo-adapter.js';
import {ensureDaily,overview,createAction,confirmAction,receiptCandidates,projectDaily} from '../../prototype/owner-r3/daily-demo.js';
import {ensureRefinement,obligationRemaining,settlementContext,beginSettlement,updateSettlement,settlementFill,previewSettlement,confirmSettlement,harvestPeriod,harvestEvents,monthlyHarvestModel,reportStatus,reportLinks,reportEvidence,candidatesForReport,attachEvidence,beginReceiptLink,confirmReceiptLink,EVIDENCE_MAX_BYTES} from '../../prototype/owner-r3/refinement-demo.js';
import {settlementForm,settlementReview,monthlyHarvest,reportDetail,receiptLinkForm,receiptLinkReview} from '../../prototype/owner-r3/refinement-ui.js';
import {quickForm,dailyReview,depotList,depotDetail,reconcile} from '../../prototype/owner-r3/daily-ui.js';
const fresh=()=>{const s=initialState();ensureDaily(s);projectDaily(s);return s;};
const finance=s=>{const o=overview(s);return Object.fromEntries(['revenueMinor','operatingExpenseMinor','cashReceivedMinor','cashPaidMinor','receivableMinor','payableMinor'].map(k=>[k,o[k]]));};
const start=(s,partyId='depot',direction='receivable',currency='TRY')=>beginSettlement(s,{partyId,direction,currency});
const ready=(s,a,values={})=>{settlementFill(s,a.id,{all:true});updateSettlement(s,a.id,{accountId:'bank-try',...values});return previewSettlement(s,a.id);};
const confirm=(s,a)=>{const p=previewSettlement(s,a.id);assert.equal(p.canConfirm,true,p.errors.join(';'));return confirmSettlement(s,a.id,p);};
const reload=s=>{const storage={value:null,getItem(){return this.value;},setItem(_,value){this.value=value;}};assert.equal(persist(storage,s),true);return loadState(storage);};
const fixture={name:'Synthetic receipt.png',kind:'image',url:'data:image/png;base64,aGVsbG8=',size:5};

test('refinement migration preserves every existing source and report; 131 is a separate synthetic event and finance unchanged',()=>{
 const s=fresh(),before=finance(s),reports=structuredClone(s.daily.fieldReports),sources=structuredClone(s.daily.sources);ensureRefinement(s);ensureRefinement(s);
 assert.deepEqual(s.daily.fieldReports.slice(0,reports.length),reports);assert.deepEqual(s.daily.sources.slice(0,sources.length),sources);
 assert.equal(s.daily.fieldReports.filter(r=>r.id==='field-refinement-131').length,1);assert.equal(s.daily.fieldReports.find(r=>r.id==='field-oct09').bunches,93);assert.deepEqual(finance(s),before);
});
test('context supplies party/direction/currency/remaining and explicit currency-matched account choice only',()=>{
 const s=fresh(),a=start(s);assert.equal(a.input.partyId,'depot');assert.equal(a.input.direction,'receivable');assert.equal(a.input.cashDirection,'in');assert.equal(a.input.currency,'TRY');assert.equal(a.input.accountId,'');assert.deepEqual(a.input.allocations,[]);assert.equal(settlementContext(s,a.input).availableMinor,8036500);
 const html=settlementForm(s,a.id);assert.match(html,/Выберите счёт явно/);assert.match(html,/Явно|явно/);assert.equal(previewSettlement(s,a.id).canConfirm,false);
});
test('opening same pending context reuses persisted draft and does not recreate source or lose form entries',()=>{
 const s=fresh(),a=start(s);updateSettlement(s,a.id,{amount:'10.25',comment:'retain this'});const count=s.daily.sources.length,revision=s.daily.revision;
 assert.equal(start(s).id,a.id);assert.equal(s.daily.sources.length,count);assert.equal(s.daily.revision,revision);const restored=reload(s);assert.equal(start(restored).input.comment,'retain this');
});
test('partial receipt settlement posts exact cents once, updates all projections, creates no revenue or debt',()=>{
 let s=fresh();const baseline=finance(s),a=start(s),original=structuredClone(s.daily.sources.find(x=>x.id===a.sourceId));
 updateSettlement(s,a.id,{amount:'0,01',accountId:'bank-try',allocations:[{obligationId:'obligation-depot-oct09',amount:'0.01'}]});
 const p=previewSettlement(s,a.id);assert.equal(p.amountMinor,1);assert.equal(p.remainingMinor,8036499);confirmSettlement(s,a.id,p);
 assert.deepEqual(finance(s),{...baseline,cashReceivedMinor:baseline.cashReceivedMinor+1,receivableMinor:baseline.receivableMinor-1});assert.equal(s.receivables[0].amountMinor,baseline.receivableMinor-1);
 assert.deepEqual(s.daily.sources.find(x=>x.id===a.sourceId),original);assert.equal(s.daily.payments.filter(x=>x.id===a.id).length,1);
 s=reload(s);confirmSettlement(s,a.id,p);assert.equal(s.daily.payments.filter(x=>x.id===a.id).length,1);assert.match(settlementReview(s,a.id),/Один платёж сохранён/);
});
test('full receivable uses explicitly displayed multi-document allocations and preserves expense/revenue',()=>{
 const s=fresh(),before=finance(s),a=start(s),p=ready(s,a);assert.equal(p.allocations.length,2);assert.equal(p.amountMinor,8036500);assert.equal(p.remainingMinor,0);assert.equal(p.allocations.reduce((n,x)=>n+x.amountMinor,0),p.amountMinor);
 confirmSettlement(s,a.id,p);assert.equal(s.receivables.length,0);assert.equal(finance(s).receivableMinor,0);assert.equal(finance(s).revenueMinor,before.revenueMinor);assert.equal(finance(s).operatingExpenseMinor,before.operatingExpenseMinor);assert.equal(finance(s).cashReceivedMinor,before.cashReceivedMinor+8036500);
});
test('partial then full payable reduces payable and cash only; source cost recognized just once',()=>{
 const s=fresh(),baseline=finance(s),a=start(s,'supplier','payable');updateSettlement(s,a.id,{amount:'128.25',accountId:'cash-try',allocations:[{obligationId:'supplier-purchase',amount:'128.25'}]});confirm(s,a);
 assert.equal(finance(s).cashPaidMinor,baseline.cashPaidMinor+12825);assert.equal(finance(s).payableMinor,baseline.payableMinor-12825);assert.equal(finance(s).operatingExpenseMinor,baseline.operatingExpenseMinor);
 const b=start(s,'supplier','payable');assert.notEqual(b.id,a.id);const p=ready(s,b);assert.equal(p.amountMinor,1280000-12825);confirmSettlement(s,b.id,p);assert.equal(obligationRemaining(s,'supplier-purchase'),0);assert.equal(s.daily.expenses.filter(x=>x.id==='expense-fertilizer').length,1);
});
test('cannot silently allocate, overpay, duplicate allocation, cross party/direction/currency, or use unknown account',()=>{
 for(const patch of [{allocations:[]},{amount:'999999999'},{accountId:'missing'},{accountId:'eur-account'},{allocations:[{obligationId:'supplier-purchase',amount:'10'}]},{allocations:[{obligationId:'obligation-depot-oct09',amount:'10'},{obligationId:'obligation-depot-oct09',amount:'10'}],amount:'20'},{allocations:[{obligationId:'obligation-depot-sep04',amount:'10'}]},{amount:'10.001'},{date:'2027-02-29'},{date:'2026-01-01'}]){
  const s=fresh();s.daily.accounts.push({id:'eur-account',name:'EUR account',currency:'EUR'});const a=start(s),before=finance(s);
  updateSettlement(s,a.id,{amount:'10',accountId:'cash-try',allocations:[{obligationId:'obligation-depot-oct09',amount:'10'}],...patch});const p=previewSettlement(s,a.id);assert.equal(p.canConfirm,false,JSON.stringify(patch));assert.throws(()=>confirmSettlement(s,a.id,p),/NEEDS_INPUT/);assert.deepEqual(finance(s),before);
 }
});
test('context identity cannot be edited into another party/currency/direction while retained correction history is explicit',()=>{
 const s=fresh(),a=start(s);updateSettlement(s,a.id,{partyId:'supplier',currency:'EUR',cashDirection:'out',direction:'payable',amount:'20'});assert.equal(a.input.partyId,'depot');assert.equal(a.input.currency,'TRY');assert.equal(a.input.cashDirection,'in');assert.equal(a.corrections.length,1);assert.equal(a.corrections[0].input.amount,'');
});
test('stale form revision and ledger revision are rejected before any cash effect',()=>{
 const s=fresh(),a=start(s),p=ready(s,a);updateSettlement(s,a.id,{comment:'new review'});assert.throws(()=>confirmSettlement(s,a.id,p),/STALE/);
 const current=previewSettlement(s,a.id),b=start(s,'supplier','payable');ready(s,b);confirm(s,b);const before=finance(s);assert.throws(()=>confirmSettlement(s,a.id,current),/STALE/);assert.deepEqual(finance(s),before);assert.equal(s.daily.payments.some(x=>x.id===a.id),false);
});
test('availability is rechecked when legacy payment consumed a previously previewed debt',()=>{
 const s=fresh(),a=start(s),p=ready(s,a);const payment=createAction(s,'payment',{purpose:'Другой платёж',amount:'1',currency:'TRY',accountId:'cash-try',direction:'in',category:'operating',obligationId:'obligation-depot-oct09'});confirmAction(s,payment.id,payment.revision);
 assert.throws(()=>confirmSettlement(s,a.id,p),/STALE/);assert.equal(previewSettlement(s,a.id).canConfirm,false);assert.equal(s.daily.payments.some(x=>x.id===a.id),false);
});
test('valid explicit 2027 date remains distinct from capture timestamp and historical overview excludes future effect',()=>{
 const s=fresh(),before=finance(s),a=start(s);const p=ready(s,a,{date:'2027-01-02'});assert.equal(p.canConfirm,true);confirmSettlement(s,a.id,p);assert.equal(s.daily.payments.at(-1).date,'2027-01-02');assert.deepEqual(finance(s),before);assert.match(settlementReview(s,a.id),/исторических итогах/);
 const field=createAction(s,'harvest',{date:'2027-10-01',greenhouseId:'imamli',bunches:'2'});assert.equal(field.canConfirm,true);
});
test('season boundary is October inclusive through next October exclusive; September is not moved',()=>{
 const s=fresh();ensureRefinement(s);s.daily.fieldReports.push({id:'sept',date:'2026-09-30',greenhouseId:'imamli',bunches:5},{id:'next-oct',date:'2027-10-01',greenhouseId:'imamli',bunches:7},{id:'last-sep',date:'2027-09-30',greenhouseId:'imamli',bunches:11});
 assert.deepEqual(harvestPeriod('season'),{id:'season-2026',from:'2026-10-01',toExclusive:'2027-10-01',label:'Сезон 2026/27',asOf:'2026-10-09'});const ids=harvestEvents(s).map(e=>e.id);assert.ok(!ids.includes('sept'));assert.ok(!ids.includes('next-oct'));assert.ok(ids.includes('last-sep'));
 assert.equal(harvestPeriod('last12').from,'2025-11-01');assert.equal(harvestPeriod('last12').toExclusive,'2026-11-01');assert.ok(harvestEvents(s,{period:'last12'}).some(e=>e.id==='sept'));
});
test('monthly metrics and drilldown use correct independent field or confirmed receipt event identities',()=>{
 const s=fresh(),field=monthlyHarvestModel(s,{metric:'field'}),kg=monthlyHarvestModel(s,{metric:'kg'}),revenue=monthlyHarvestModel(s,{metric:'revenue'});
 assert.equal(field.months.length,12);assert.equal(field.knownTotal,351);assert.equal(kg.knownTotal,2260);assert.equal(revenue.knownTotal,8302000);assert.equal(field.months.find(x=>x.key==='2026-11').value,null);
 assert.ok(field.events.every(e=>e.type==='field'&&s.daily.fieldReports.some(r=>r.id===e.id)));assert.ok(kg.events.every(e=>e.type==='depot'&&s.daily.receipts.some(r=>r.id===e.id)));
 assert.equal(harvestEvents(s,{metric:'kg',period:'last12',month:'2026-09'}).length,4);assert.equal(harvestEvents(s,{metric:'field',month:'2026-11'}).length,0);
});
test('each greenhouse excludes other greenhouse receipts and never invents mixed kg/revenue allocations',()=>{
 const s=fresh(),kg=monthlyHarvestModel(s,{greenhouseId:'imamli',metric:'kg',period:'last12'}),revenue=monthlyHarvestModel(s,{greenhouseId:'imamli',metric:'revenue',period:'last12'});
 assert.equal(kg.knownTotal,2530);assert.equal(kg.coverage.unknownAllocation,1);assert.equal(kg.events.find(e=>e.id==='depot-sep17').value,null);assert.equal(revenue.events.find(e=>e.id==='depot-sep17').value,null);assert.ok(!kg.events.some(e=>e.id==='depot-oct03'));
});
test('monthly currency totals do not combine foreign revenue and unknown is never displayed as zero',()=>{
 const s=fresh();s.daily.receipts.push({...structuredClone(s.daily.receipts.at(-1)),id:'eur',currency:'EUR',totalMinor:55555});const m=monthlyHarvestModel(s,{metric:'revenue'});assert.equal(m.knownTotal,8302000);assert.equal(m.coverage.unknownCount,1);assert.match(monthlyHarvest(s,{metric:'kg',greenhouseId:'yesiloz'}),/Нет подтверждённых значений/);
});
test('history filters selected greenhouse/month and sorts quantity/date without losing report identities',()=>{
 const s=fresh();ensureRefinement(s);const rows=harvestEvents(s,{greenhouseId:'imamli',month:'2026-10',sort:'value'});assert.deepEqual(rows.map(r=>r.bunches),[131,93]);assert.ok(rows.every(r=>r.greenhouseId==='imamli'));const older=harvestEvents(s,{metric:'kg',period:'last12',sort:'oldest'});assert.equal(older[0].id,'depot-sep04');
});
test('131 field and 93 ADET are candidate disagreement, not an automatic rejection or overwrite',()=>{
 const s=fresh();ensureRefinement(s);const candidates=candidatesForReport(s,'field-refinement-131');assert.equal(candidates[0].receiptId,'depot-oct09');assert.equal(candidates[0].mismatch,true);assert.equal(candidates[0].requiresConfirmation,true);assert.equal(reportLinks(s,'field-refinement-131').length,0);assert.match(receiptLinkForm(s,'field-refinement-131'),/131 ветка по полю \/ 93 ADET/);
});
test('unknown ADET is readable in all candidate and receipt entry points, never literal null or NaN',()=>{
 const s=fresh();ensureRefinement(s);s.daily.receipts.at(-1).adet=null;
 for(const html of [receiptLinkForm(s,'field-refinement-131'),depotList(s),depotDetail(s,'depot-oct09'),reconcile(s),monthlyHarvest(s,{metric:'kg'})]){assert.match(html,/Количество веток неизвестно/);assert.doesNotMatch(html,/null ADET|NaN|undefined/);}
 assert.match(receiptCandidates(s,'field-refinement-131')[0].label,/Количество веток неизвестно/);
});
test('photo evidence is durable, contextual, keeps caption/source, survives reload, and has no finance/harvest fact effect',()=>{
 let s=fresh();ensureRefinement(s);const before=finance(s),reports=structuredClone(s.daily.fieldReports),evidence=attachEvidence(s,'field-refinement-131',{file:fixture,caption:'131 по полю; проверить чек'},'photo-attempt-1');
 assert.equal(evidence.status,'review');assert.equal(reportStatus(s,evidence.reportId),'review');assert.deepEqual(s.daily.fieldReports,reports);assert.deepEqual(finance(s),before);assert.equal(reportLinks(s,evidence.reportId).length,0);
 s=reload(s);const restored=reportEvidence(s,evidence.reportId)[0];assert.equal(restored.file.url,fixture.url);assert.equal(restored.caption,'131 по полю; проверить чек');assert.match(reportDetail(s,evidence.reportId),/131 по полю; проверить чек/);
 assert.equal(attachEvidence(s,evidence.reportId,{file:fixture,caption:restored.caption},'photo-attempt-1').id,evidence.id);assert.equal(s.daily.refinement.evidence.length,1);
});
test('evidence rejects missing report, volatile blob, oversized file, unsupported types and changed idempotent payload',()=>{
 const s=fresh();ensureRefinement(s);assert.throws(()=>attachEvidence(s,'missing',{file:fixture},'one'),/MISSING_REPORT/);
 for(const file of [{...fixture,url:'blob:temporary'},{...fixture,size:EVIDENCE_MAX_BYTES+1},{...fixture,kind:'video'},{...fixture,url:'https://example.com/photo.png'}])assert.throws(()=>attachEvidence(s,'field-refinement-131',{file},'one'),/DURABLE_FILE_REQUIRED|EVIDENCE_TOO_LARGE|PHOTO_OR_PDF_REQUIRED/);
 attachEvidence(s,'field-refinement-131',{file:fixture,caption:'original'},'one');assert.throws(()=>attachEvidence(s,'field-refinement-131',{file:fixture,caption:'changed'},'one'),/IDEMPOTENCY_CONFLICT/);assert.throws(()=>attachEvidence(s,'field-oct09',{file:fixture,caption:'original'},'one'),/CONTEXT_MISMATCH/);
});
test('existing persisted image source can be attached by identity without recreating the original',()=>{
 const s=fresh();ensureRefinement(s);s.sources.push({id:'capture-local',file:fixture,comment:'caption'});const before=s.daily.sources.length,e=attachEvidence(s,'field-refinement-131',{sourceId:'capture-local',caption:'caption'},'source-link');assert.equal(e.sourceId,'capture-local');assert.equal(s.daily.sources.length,before);assert.equal(e.file.url,fixture.url);
});
test('photo review explicit link changes only relation; rejects no review; retry/reload once, originals unchanged',()=>{
 let s=fresh();ensureRefinement(s);const before=finance(s),source=structuredClone(s.daily.sources.find(s=>s.id==='source-field-refinement-131')),evidence=attachEvidence(s,'field-refinement-131',{file:fixture},'photo'),review=beginReceiptLink(s,'field-refinement-131','depot-oct09',{evidenceId:evidence.id});
 assert.throws(()=>confirmReceiptLink(s,review.id,review.revision),/EXPLICIT_REVIEW_REQUIRED/);assert.equal(reportLinks(s,review.reportId).length,0);assert.match(receiptLinkReview(s,review.id),/131 ветка \/ 93 ADET/);
 confirmReceiptLink(s,review.id,review.revision,{reviewed:true});assert.equal(evidence.status,'linked');assert.equal(s.daily.fieldReports.find(r=>r.id===review.reportId).bunches,131);assert.equal(s.daily.receipts.find(r=>r.id==='depot-oct09').adet,93);assert.deepEqual(finance(s),before);assert.deepEqual(s.daily.sources.find(s=>s.id===source.id),source);
 s=reload(s);confirmReceiptLink(s,review.id,1,{reviewed:true});assert.equal(reportLinks(s,review.reportId).length,1);
});
test('many-to-many report/receipt links allowed explicitly; no forced one-to-one and no automatic allocation',()=>{
 const s=fresh();ensureRefinement(s);const before=finance(s);for(const [report,receipt]of [['field-refinement-131','depot-oct09'],['field-oct09','depot-oct09'],['field-refinement-131','depot-oct03']]){const a=beginReceiptLink(s,report,receipt);confirmReceiptLink(s,a.id,a.revision,{reviewed:true});}
 assert.equal(reportLinks(s,'field-refinement-131').length,2);assert.equal(s.daily.links.filter(l=>l.receiptId==='depot-oct09').length,2);assert.equal(s.daily.fieldReports.find(r=>r.id==='field-refinement-131').receiptId,'depot-oct09');assert.deepEqual(finance(s),before);assert.equal(s.daily.fieldReports.find(r=>r.id==='field-refinement-131').kg,null);
});
test('stale source quantity and missing evidence reject link atomically',()=>{
 const s=fresh();ensureRefinement(s);const e=attachEvidence(s,'field-refinement-131',{file:fixture},'photo'),a=beginReceiptLink(s,'field-refinement-131','depot-oct09',{evidenceId:e.id}),before=s.daily.links.length;
 s.daily.refinement.evidence=[];assert.throws(()=>confirmReceiptLink(s,a.id,a.revision,{reviewed:true}),/CONTEXT_MISMATCH/);assert.equal(s.daily.links.length,before);assert.equal(s.daily.fieldReports.find(r=>r.id===a.reportId).receiptId,null);
 s.daily.refinement.evidence=[e];s.daily.fieldReports.find(r=>r.id===a.reportId).bunches=132;assert.throws(()=>confirmReceiptLink(s,a.id,a.revision,{reviewed:true}),/STALE/);assert.equal(s.daily.links.length,before);
});
test('confirmed cut exposes camera and existing receipt immediately, preserving legacy reconciliation entry',()=>{
 const s=fresh(),a=createAction(s,'harvest',{bunches:'42',greenhouseId:'imamli'});confirmAction(s,a.id,a.revision);const html=dailyReview(s,a.id);assert.match(html,/data-action="report-photo:/);assert.match(html,/data-testid="report-existing"/);assert.match(html,/href="#reconcile"[^>]*data-testid="daily-result-open"/);
});
test('contextual worker entry locks verified worker identity, greenhouse remains editable, generic picker stays available',()=>{
 const s=fresh();s.dailyDrafts={worker:{worker:'muhammed',workerLocked:true,place:'İmamlı'}};const locked=quickForm(s,'worker');assert.match(locked,/worker-context-recipient/);assert.match(locked,/type="hidden" name="worker" value="muhammed"/);assert.doesNotMatch(locked,/<select[^>]+name="worker"/);assert.match(locked,/data-testid="daily-place"/);assert.match(locked,/worker-translation-reviewed/);s.dailyDrafts.worker={};assert.match(quickForm(s,'worker'),/<select[^>]+name="worker"/);
});
test('new renderers escape owner text and neither show raw unknowns nor depend on unsafe inline styles',()=>{
 const s=fresh(),a=start(s);updateSettlement(s,a.id,{comment:'<script>alert(1)</script>'});ready(s,a);assert.doesNotMatch(settlementReview(s,a.id),/<script>/);assert.match(settlementReview(s,a.id),/&lt;script&gt;/);
 for(const html of [monthlyHarvest(s),reportDetail(s,'field-refinement-131'),receiptLinkForm(s,'field-refinement-131')]){assert.doesNotMatch(html,/style=|null ADET|undefined|NaN/);}
});

test('PDF original stays downloadable and MIME-kind mismatch is rejected',()=>{
 const s=fresh();ensureRefinement(s);const file={name:'SYNTHETIC.pdf',kind:'pdf',size:5,url:'data:application/pdf;base64,aGVsbG8='},e=attachEvidence(s,'field-refinement-131',{file},'pdf');assert.match(reportDetail(s,'field-refinement-131'),new RegExp(`data-testid="evidence-download-${e.id}"`));assert.match(reportDetail(s,'field-refinement-131'),/download="SYNTHETIC.pdf"/);assert.throws(()=>attachEvidence(s,'field-refinement-131',{file:{...file,kind:'image'}},'mismatch'),/PHOTO_OR_PDF_REQUIRED/);
});
test('caption correction creates linked evidence revision and preserves original source; retry cannot alter correction identity',()=>{
 const s=fresh();ensureRefinement(s);const original=attachEvidence(s,'field-refinement-131',{file:fixture,caption:'original'},'first'),source=structuredClone(s.daily.sources.find(x=>x.id===original.sourceId));
 const revised=attachEvidence(s,'field-refinement-131',{file:fixture,caption:'revised',replacesEvidenceId:original.id},'second');assert.equal(revised.replacesEvidenceId,original.id);assert.deepEqual(s.daily.sources.find(x=>x.id===source.id),source);assert.equal(reportEvidence(s,'field-refinement-131').length,2);assert.match(reportDetail(s,'field-refinement-131'),/Уточнение источника/);
 assert.throws(()=>attachEvidence(s,'field-refinement-131',{file:fixture,caption:'revised'},'second'),/IDEMPOTENCY_CONFLICT/);assert.throws(()=>attachEvidence(s,'field-oct09',{file:fixture,replacesEvidenceId:original.id},'third'),/CONTEXT_MISMATCH/);
});
