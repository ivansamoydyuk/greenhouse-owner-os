import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {PreparationRefreshWindow,preparationPending,depotEditorFields,mergeDepotEditor,preservesDepotQualityReview,depotReviewGates,approvedDepotDocumentCount} from '../../prototype/owner-r3/capture-daily.js';
const source=fs.readFileSync('prototype/owner-r3/connected.js','utf8');
const extract=(from,to)=>source.slice(source.indexOf(from),source.indexOf(to,source.indexOf(from)));
const proposal=()=>({id:'proposal',version:1,source:{id:'source',text:'separate caption'},revision:{payload:{event_date:'2026-10-12',currency:'TRY',depot_id:'depot',lines:[{original_quality_name:'Grand',net_weight_kg:'100.000',unit_price:'50.00',stated_amount:'5000.00',confirmed_amount:'5000.00',source_line:'Grand 100 50 5000',count:93}],qualities:['HIGHEST'],unreadable_lines:[],batch_ids:[]}}});
function submitRunner(overrides={}) {
 const requests=[],drafts=[],finishes=[];
 const deps={assertSubmissionScope:()=>{},saveDraft:async()=>{},api:{entityPost:async(path,body,key)=>{requests.push({path,body,key});return {id:'source'};},draft:async(key,d)=>drafts.push({key,d:structuredClone(d)})},finish:async(...args)=>finishes.push(args),...overrides};
 const submit=new Function(...Object.keys(deps),extract('async function submitForm(', 'function openSheet(')+';return submitForm;')(...Object.values(deps));
 return {submit,requests,drafts,finishes};
}
const snapshot=(draft,current=null)=>({draft,current,route:'attach-depot/new',scope:'scope',generation:1,fields:{}});
test('real depot submit saves only the original before any line validation or accounting action',async()=>{
 const r=submitRunner();const draft={id:'stable-id',captured_at:'2026-10-12T09:00:00+03:00',caption:'Separate owner comment',file:{filename:'receipt.png',mime_type:'image/png',file_base64:'original'}};
 await r.submit({dataset:{form:'depot'}},snapshot(draft));
 assert.equal(r.requests.length,1);assert.equal(r.requests[0].path,'/owner/captures');assert.equal(r.requests[0].key,'stable-id:source');assert.equal(r.requests[0].body.text,draft.caption);assert.equal(r.requests[0].body.file_base64,'original');assert.equal('lines' in r.requests[0].body,false);assert.equal(draft.source_id,'source');assert.equal(r.finishes[0][0],'source/source');
 assert.equal(r.drafts[0].d.id,'stable-id');
 await r.submit({dataset:{form:'depot'}},snapshot(draft));assert.equal(r.requests.length,1,'resuming saved source never creates another source');
});
test('failed source save does not report server durability or discard the local file',async()=>{
 const draft={id:'stable-id',file:{mime_type:'image/png',file_base64:'original'}};
 const r=submitRunner({api:{entityPost:async()=>{throw Error('offline')},draft:async()=>{}}});
 await assert.rejects(r.submit({dataset:{form:'depot'}},snapshot(draft)),/offline/);assert.equal(draft.source_id,undefined);assert.equal(draft.file.file_base64,'original');assert.equal(r.finishes.length,0);
});
test('source response from an old participant cannot acknowledge or store the new source',async()=>{
 let checks=0;const r=submitRunner({assertSubmissionScope:()=>{if(++checks===3)throw Error('changed participant')}});
 const draft={id:'stable-id',file:{mime_type:'image/png',file_base64:'original'}};
 await assert.rejects(r.submit({dataset:{form:'depot'}},snapshot(draft)),/changed participant/);assert.equal(draft.source_id,undefined);assert.equal(r.drafts.length,0);assert.equal(r.finishes.length,0);
});
test('initial depot form exposes file and optional caption, never asks for OCR facts',()=>{
 const fn=extract('function depotForm(', 'function qualityProvenance(');
 const run=new Function('draft',`const Header=()=>'',panel=x=>x,form=(id,body,label)=>'<form>'+body+'<button>'+label+'</button></form>',fileField=()=>'<input name="file">',notice=x=>x,area=(l,n)=>'<textarea name="'+n+'"></textarea>',link=(label,to)=>label+' '+to;${fn};return depotForm(null);`);
 const html=run({});assert.match(html,/name="file"/);assert.match(html,/name="caption"/);assert.match(html,/Сохранить оригинал/);assert.doesNotMatch(html,/name="(?:kg|price|quality|depot|currency)"/);
 const saved=run({source_id:'saved'});assert.match(saved,/source\/saved/);assert.doesNotMatch(saved,/name="file"/);
});
test('depot editor prefills exact recognized facts and does not silently choose field links',()=>{
 const f=depotEditorFields(proposal());assert.equal(f.kg,'100.000');assert.equal(f.price,'50.00');assert.equal(f.quality,'Grand');assert.equal(f.count,93);assert.equal(f.confirmed_amount,'stated');assert.equal(f.caption,'separate caption');assert.equal(f.source_id,'source');assert.equal(f.proposal_id,'proposal');assert.equal(Object.keys(f).some(k=>k.startsWith('report_')),false);
});
test('new preparation enriches untouched editor facts but preserves unsaved owner changes',()=>{
 const p=proposal(),base=depotEditorFields(p),saved={id:'draft',...base,editor_base:base,kg:'101.000'};
 p.version=2;p.revision.payload.lines[0].unit_price='55.00';
 const d=mergeDepotEditor(saved,p);assert.equal(d.kg,'101.000');assert.equal(d.price,'55.00');assert.equal(d.editor_base.kg,'100.000');assert.equal(d.proposal_version,2);assert.equal(d.id,'draft');
});
test('read-only refresh is bounded and reset only by distinct view/session or explicit refresh',()=>{
 let now=0;const w=new PreparationRefreshWindow({now:()=>now,maxReads:2,durationMs:100});const id={route:'sources',scope:'owner-1',generation:1};w.enter(id);
 assert.equal(w.claim(id),true);w.enter(id);assert.equal(w.claim(id),true);assert.equal(w.claim(id),false);assert.equal(w.matches({...id,scope:'owner-2'}),false);
 w.reset();w.enter(id);now=100;assert.equal(w.claim(id),false);w.enter({...id,route:'review'});assert.equal(w.claim({...id,route:'review'}),true);
 assert.equal(preparationPending({job:{state:'RUNNING'}}),true);assert.equal(preparationPending({job:{state:'RUNNING'},manual_document_recovery_available:true}),false);for(const state of ['FAILED','SUCCEEDED','READY','POSTED'])assert.equal(preparationPending({job:{state}}),false);
});
test('poll integration rejects late reads and never rerenders active forms or dialogs',()=>{
 const code=extract('function schedulePreparationRefresh(', 'async function render(');
 assert.match(code,/token===epoch/);assert.match(code,/api.scope===identity.scope/);assert.match(code,/api.generation===identity.generation/);assert.match(code,/sheetRoot.querySelector/);assert.match(code,/activeElement\?\.closest\('form'\)/);assert.match(code,/if\(!valid\(\)\|\|editing\(\)\)/);assert.doesNotMatch(code,/entityPost|mutate\(/);
});
test('payment exposes purpose and debt selection without offering advance or new expense',()=>{
 const keys=extract('function editableKeys(', 'function finance(');
 const run=new Function('p','needsPaidReview',keys+';return editableKeys(p);');assert.ok(run({action_type:'PAYMENT'},()=>false).includes('payment_purpose'));assert.ok(run({action_type:'PAYMENT'},()=>false).includes('obligation_id'));
 const editor=extract('function editFact(', 'async function recover(');assert.match(editor,/value:"debt"/);assert.doesNotMatch(editor,/value:"advance"/);
});
test('unreadable OCR lines require explicit owner acknowledgment before being cleared',()=>{
 const submit=extract('} else if (name === "depot")', '} else if(name===\'journey-links\'');
 assert.match(submit,/draft.unreadable_reviewed!=='on'/);assert.match(submit,/payload.unreadable_lines=\[\];payload.document_reviewed=true/);
 assert.match(source,/name="unreadable_reviewed"/);assert.match(source,/map\(line=>'<li>'\+e\(line\)/);
});

test('existing-debt invoice choice durably rejects the unposted sibling before preparing a link',async()=>{
 const calls=[],saves=[];let rejected=false;
 const r=submitRunner({api:{entityGet:async path=>path.includes('/captures/')?{proposals:[{id:'payable',action_type:'PAYABLE'}]}:{id:'payable',version:3,state:rejected?'REJECTED':'READY'},entityPost:async(path,body,key)=>{calls.push({path,body,key});if(path.endsWith('/reject'))rejected=true;return{};},draft:async(k,v)=>saves.push({k,v})}});
 const draft={id:'choice',captured_at:'now',receipt:'receipt',intent:'existing_payable'};
 const current={proposal:{id:'match',version:2,source:{id:'source'},revision:{payload:{receipt_id:null}}},receipts:{items:[{id:'receipt',kind:'receipt',status:'posted'}]}};
 const snap={...snapshot(draft,current),route:'invoice-select/match'};
 await r.submit({dataset:{form:'invoice-choice'}},snap);
 assert.equal(calls[0].path,'/owner/confirmations/payable/reject');assert.equal(calls[0].body.expected_version,3);assert.equal(calls[1].path,'/owner/confirmations/match/revise');assert.equal(calls[1].body.payload.receipt_id,'receipt');assert.equal(saves[0].k,'invoice-source/receipt/source');
 calls.length=0;await r.submit({dataset:{form:'invoice-choice'}},snap);assert.equal(calls.some(c=>c.path.endsWith('/reject')),false,'replay does not re-reject retired sibling');
 const view=extract('function invoiceWorkflowView(', 'function transferForm(');assert.match(view,/payable=c.payable\?\.state==='REJECTED'\?null:c.payable/);assert.doesNotMatch(view,/payable=draft.invoice_intent/);
 const choice=extract('function invoiceChoiceView(', 'function invoiceWorkflowView(');assert.match(choice,/отклонить предложение нового/);
});

test('already posted sibling cannot be hidden or rejected by existing-debt invoice choice',async()=>{
 const calls=[];const r=submitRunner({api:{entityGet:async path=>path.includes('/captures/')?{proposals:[{id:'payable',action_type:'PAYABLE'}]}:{id:'payable',version:3,state:'POSTED'},entityPost:async(...args)=>calls.push(args)}});
 const current={proposal:{id:'match',version:2,source:{id:'source'},revision:{payload:{}}},receipts:{items:[{id:'receipt',kind:'receipt',status:'posted'}]}};
 await assert.rejects(r.submit({dataset:{form:'invoice-choice'}},{...snapshot({receipt:'receipt',intent:'existing_payable'},current),route:'invoice-select/match'}),/уже подтверждён/);assert.equal(calls.length,0);
});

function pollHarness() {
 const callbacks=[],renders=[];let release;
 const response=new Promise(resolve=>release=resolve);
 const code=extract('function schedulePreparationRefresh(', 'async function render(');
 const run=new Function('PreparationRefreshWindow','preparationPending','callbacks','renders','response',`
 let route='sources',epoch=1,readTimer=null,ready=true,busy=false,ownerLoggingOut=false,current=null,editing=false,dialog=false;
 const data={sources:{items:[]},queue:{items:[]}},api={scope:'owner',generation:1,entityGet:async()=>response};
 const preparationRefresh=new PreparationRefreshWindow(),clearTimeout=()=>{},setTimeout=fn=>callbacks.push(fn),sheetRoot={querySelector:()=>dialog},main={dataset:{}},status={textContent:''},document={get activeElement(){return {closest:()=>editing}},querySelector:s=>s==='#main'?main:status};
 const shell=html=>renders.push(html),sourceList=async()=>JSON.stringify(data.sources),reviewList=()=>'',sourceDetail=()=>'',render=()=>{};
 ${code};schedulePreparationRefresh();return {data,scope:()=>api.scope='other',generation:()=>api.generation++,route:()=>route='home',epoch:()=>epoch++,edit:()=>editing=true,dialog:()=>dialog=true};
 `);
 return {state:run(PreparationRefreshWindow,preparationPending,callbacks,renders,response),callbacks,renders,release};
}
test('late actual polling responses cannot overwrite a new route, session, form edit, or dialog',async()=>{
 for(const boundary of ['scope','generation','route','epoch','edit','dialog']){
  const h=pollHarness();const checking=h.callbacks.shift()();h.state[boundary]();h.release({items:[{id:'new'}]});await checking;
  assert.equal(h.renders.length,0,boundary);assert.deepEqual(h.state.data.sources,{items:[]},boundary);
 }
 const h=pollHarness();const checking=h.callbacks.shift()();h.release({items:[{id:'new'}]});await checking;assert.equal(h.renders.length,1);assert.equal(h.state.data.sources.items[0].id,'new');
});


test('prefilled quality candidates remain unreviewed after unrelated editor saves or one corrected row',()=>{
 const p=proposal().revision.payload;p.qualities.push('FIRST');p.lines.push({...p.lines[0],original_quality_name:'Birinci'});
 const next=structuredClone(p);assert.equal(preservesDepotQualityReview(p,next),false);
 p.quality_reviewed=true;next.lines[0].net_weight_kg='99.000';next.batch_ids=['explicit-link'];assert.equal(preservesDepotQualityReview(p,next),true,'unrelated numeric or link edits retain real earlier review');
 for(const change of [v=>v.depot_id='another-depot',v=>v.qualities[1]='SECOND',v=>v.lines[1].original_quality_name='Yerli',v=>{v.lines.push(v.lines[0]);v.qualities.push('HIGHEST')}]){
  const changed=structuredClone(next);change(changed);assert.equal(preservesDepotQualityReview(p,changed),false);
 }
 const edit=extract("} else if (name === 'edit-depot-line')", '} else if (name === "depot")');
 assert.match(edit,/quality_reviewed=preservesDepotQualityReview\(current.revision.payload,payload\)/);assert.doesNotMatch(edit,/quality_reviewed=payload.qualities.every/);
 const form=extract('} else if (name === "depot")', "} else if(name==='journey-links'");
 assert.match(form,/quality_reviewed=preservesDepotQualityReview\(proposal.revision.payload,payload\)/);assert.doesNotMatch(form,/quality_reviewed:qualities.every/);
});

test('supplier file saves before intent, with original receipt and no repeated document facts',async()=>{
 const r=submitRunner({clearDraft:async()=>{}});
 const draft={id:'invoice-original',captured_at:'2026-10-12T09:00:00+03:00',text:'separate caption',file:{filename:'invoice.pdf',mime_type:'application/pdf',file_base64:'bytes'}};
 await r.submit({dataset:{form:'invoice-source'}},{...snapshot(draft,{id:'receipt'}),route:'invoice-new/receipt'});
 assert.equal(r.requests.length,1);const body=r.requests[0].body;
 assert.equal(body.capture_context,'supplier_invoice');assert.equal(body.invoice_receipt_id,'receipt');assert.equal(Object.hasOwn(body,'invoice_intent'),false);assert.equal(body.text,'separate caption');assert.equal(body.file_base64,'bytes');assert.equal(Object.hasOwn(body,'amount'),false);assert.equal(r.finishes[0][0],'invoice-source/receipt/source');
 const form=extract('function invoiceNewForm(', 'function invoiceChoiceView(');assert.doesNotMatch(form,/select\(|name="intent"/);
});

test('operating invoice choice preserves every recognized fact and durably records owner intent',async()=>{
 const calls=[],payload={amount:'14000.00',currency:'TRY',counterparty_id:'supplier',event_date:'2026-10-12',description:'Known invoice',invoice_intent:null};
 const r=submitRunner({api:{entityGet:async path=>path.includes('/captures/')?{proposals:[{id:'payable',action_type:'PAYABLE'}]}:{id:'payable',version:3,state:'NEEDS_INPUT',revision:{payload}},entityPost:async(path,body,key)=>{calls.push({path,body,key});return{};},draft:async()=>{}}});
 const current={proposal:{id:'match',version:2,source:{id:'source'},revision:{payload:{receipt_id:'receipt'}}},receipts:{items:[{id:'receipt',kind:'receipt',status:'posted'}]}};
 await r.submit({dataset:{form:'invoice-choice'}},{...snapshot({id:'choice',receipt:'receipt',intent:'operating_expense'},current),route:'invoice-select/match'});
 assert.equal(calls[0].path,'/owner/confirmations/payable/revise');assert.deepEqual(calls[0].body.payload,{...payload,invoice_intent:'operating_expense'});assert.equal(calls.some(c=>c.path.endsWith('/confirm')),false);
 const view=extract('function invoiceWorkflowView(', 'function transferForm(');assert.match(view,/c.payable\?\.revision.payload.invoice_intent!==\'operating_expense\'/);assert.doesNotMatch(view,/!draft.invoice_intent/);
});

test('ambiguous money choice sends only source origin/version/intent and uses returned typed identity',async()=>{
 for(const intent of ['operating_payable','paid_expense','debt_payment']){
  const r=submitRunner();const current={id:'unknown',version:4,source:{id:'original'},revision:{payload:{money_candidates:{amount:'5000.00',description:'ремонт насоса'}}}};
  await r.submit({dataset:{form:'money-intent'}},{...snapshot(null,current),route:'review/unknown',fields:{intent,amount:'untrusted replacement'}});
  assert.equal(r.requests.length,1);assert.equal(r.requests[0].path,'/owner/captures/original/money-recovery');assert.deepEqual(r.requests[0].body,{origin_proposal_id:'unknown',expected_version:4,intent});assert.equal(r.finishes[0][0],'review/source');
 }
 const r=submitRunner();await assert.rejects(r.submit({dataset:{form:'money-intent'}},{...snapshot(null,{id:'p',source:{id:'s'}}),fields:{intent:'advance'}}),/Выберите/);assert.equal(r.requests.length,0);
});

test('ambiguous money review retains known facts and replaces choices with the existing target link',()=>{
 const fn=extract('function moneyIntentReview(', 'function proposalDetail(');
 const run=new Function('p',`const Header=()=>'',panel=x=>x,e=x=>String(x??''),money=(v,c)=>v+' '+c,facts=rows=>rows.map(x=>x.join(':')).join(';'),displayDate=x=>x,refName=x=>x,factOrigin=()=>'',select=()=>'<select name="intent">',form=(name,body)=>'<form data-form="'+name+'">'+body+'</form>',link=(label,to)=>label+' '+to,notice=x=>x;${fn};return moneyIntentReview(p);`);
 const p={id:'p',state:'NEEDS_INPUT',source:{id:'s',text:'Серкан, ремонт насоса, 5 000 TRY.'},revision:{payload:{money_candidates:{amount:'5000.00',currency:'TRY',description:'ремонт насоса',counterparty_id:'Серкан',event_date:'2026-10-12'}}}};
 const html=run(p);assert.match(html,/5000.00 TRY/);assert.match(html,/ремонт насоса/);assert.match(html,/Не установлен из источника/);assert.match(html,/data-form="money-intent"/);
 const reserved=run({...p,recovery:{recovery_proposal_id:'typed-target'}});assert.match(reserved,/review\/typed-target/);assert.doesNotMatch(reserved,/data-form="money-intent"/);
});


test('compact depot gates remove only duplicated action prompts and preserve critical errors',()=>{
 const p=proposal();p.revision.payload.quality_reviewed=false;p.revision.payload.arithmetic_reviewed=false;
 p.revision.missing=['Подтвердите качества именно этого депо','Проверьте строки и сумму; Alacak не является оплатой','Неполные строки: unreadable','Новый критичный серверный запрет'];
 const gates=depotReviewGates(p);assert.equal(gates.qualityAction,true);assert.equal(gates.arithmeticAction,true);assert.deepEqual(gates.missing,['Неполные строки: unreadable','Новый критичный серверный запрет']);assert.equal(p.revision.missing.length,4,'authority validation is not modified');
 p.revision.payload.qualities=[null];assert.ok(depotReviewGates(p).missing.includes('Подтвердите качества именно этого депо'),'missing categories still demand input');
});


test('posted document count is read only from matching approved source basis, never substituted into domain line metrics',()=>{
 const s={status:'posted',owner_source_id:'source',evidence:{source:'owner:r2-action',source_reference:'proposal'},quality_reviews:[{proposal_id:'proposal',version:4,reviewed_by:'owner',basis:{arithmetic:{document_count:93}}}],reconciliation:{depot_count:null,field_count:131},confirmed_count:0,average_weight_kg:null};
 assert.equal(approvedDepotDocumentCount(s),93);assert.equal(s.reconciliation.depot_count,null);assert.equal(s.confirmed_count,0);assert.equal(s.average_weight_kg,null);
 for(const change of [v=>v.status='draft',v=>v.owner_source_id=null,v=>v.quality_reviews[0].proposal_id='other',v=>v.quality_reviews[0].reviewed_by=null,v=>v.quality_reviews[0].basis.arithmetic.document_count=null,v=>v.quality_reviews.push({...v.quality_reviews[0],basis:{arithmetic:{document_count:94}}})]){
  const changed=structuredClone(s);change(changed);assert.equal(approvedDepotDocumentCount(changed),null);
 }
 const code=extract('function depotDetail(', 'function journeyActions(');assert.match(code,/\["ADET по строкам", s.reconciliation.depot_count/);assert.doesNotMatch(code,/\["ADET документа", s.reconciliation.depot_count/);
});

test('generic undecided invoice queue card routes to same-source intent choice and has no confirmation shortcut',()=>{
 const code=extract('function proposalDetail(', 'const labels =');
 const run=new Function('p',`const Header=()=>'',panel=x=>x,compactMoneyReview=()=>'<p>Known invoice facts</p>',link=(label,to)=>label+' '+to;${code};return proposalDetail(p);`);
 const html=run({action_type:'PAYABLE',invoice_match_id:'same-source-match',source:{id:'original'},revision:{payload:{invoice_intent:'undecided'}}});
 assert.match(html,/Known invoice facts/);assert.match(html,/invoice-select\/same-source-match/);assert.doesNotMatch(html,/confirm-proposal|Подтвердить действие/);
});


test('manual exhausted-document recovery is offered only by server capability and reuses preparation identity',()=>{
 const code=extract('function sourceDetail(', 'function factOrigin(');
 const run=new Function('s',`const Header=()=>'',panel=x=>x,e=String,notice=x=>x,recognitionDetails=()=>'',preparationPending=()=>false,canRecoverSourceExpense=()=>false,stateTag=()=>'',statuses={},entityPath=x=>x,btn=(label,action)=>'<button data-action="'+action+'">'+label+'</button>',link=()=>'';${code};return sourceDetail(s);`);
 const s={id:'original',state:'FAILED',job:{state:'FAILED',attempts:3},retryable:false,proposals:[]};
 assert.doesNotMatch(run(s),/Ввести вручную/);assert.match(run({...s,manual_document_recovery_available:true}),/<button data-action="prepare:original">Ввести вручную/);assert.doesNotMatch(run({...s,manual_document_recovery_available:true}),/Повторить подготовку/);
});


test('long invoice description is collapsed accessibly without truncating authoritative text or hiding ordinary purpose',()=>{
 const code=extract('function invoiceDescriptionView(', 'function invoiceWorkflowView(');
 const view=new Function('description','e',code+';return invoiceDescriptionView(description);');
 const escape=s=>s.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
 const short='Ремонт насоса';assert.deepEqual(view(short,escape),{facts:[['За что',short]],detail:''});
 assert.deepEqual(view('',escape),{facts:[['За что','Нужно уточнить']],detail:''});
 const long='FATURA KNO3 100 kg 5 TRY 500 TRY <source>\n'.repeat(10),rendered=view(long,escape);
 assert.deepEqual(rendered.facts,[]);assert.match(rendered.detail,/<details class="source-provenance invoice-document-description">/);assert.match(rendered.detail,/<summary>Описание из документа<\/summary>/);assert.ok(rendered.detail.includes(escape(long)));assert.doesNotMatch(rendered.detail,/<details[^>]*\sopen(?:\s|>)/);
 const workflow=extract('function invoiceWorkflowView(', 'function transferForm(');assert.match(workflow,/description=invoiceDescriptionView\(f.description\)/);assert.match(workflow,/\['Сумма',f.amount/);assert.match(workflow,/\['Дата',displayDate\(f.event_date/);assert.match(workflow,/\['Поставщик',f.counterparty_id/);assert.match(workflow,/\['amount','currency','event_date','counterparty_id','description','due_date'\]/);
});
