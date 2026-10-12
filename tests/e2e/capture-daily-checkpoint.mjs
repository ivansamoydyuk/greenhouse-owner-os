/** Real Connected UI -> frozen bytes -> local OCR -> owner review -> PostgreSQL.
 * No mocked source, transcript, authorization, recognition or domain response.
 * Run only on the approved disposable CI runner; never against production.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {chromium, webkit} from 'playwright';
import {ownerLogin, pilotMode, redactPilot} from './helpers/pilot-auth.mjs';
import {installClickAudit} from './friction-metrics.mjs';

const mode=process.env.CAPTURE_DAILY_MODE || 'candidate';
assert.ok(['baseline','candidate'].includes(mode));
assert.equal(pilotMode,true,'Private owner login credentials are required');
const base=process.env.PREVIEW_URL || 'http://127.0.0.1:4173';
assert.ok(['127.0.0.1','localhost'].includes(new URL(base).hostname),'Disposable loopback TEST only');
const out=process.env.E2E_ARTIFACTS || 'artifacts/capture-daily';
const benchmarkPath='tests/fixtures/capture_daily_benchmark.json';
const rawBenchmark=await fs.readFile(benchmarkPath);
const benchmark=JSON.parse(rawBenchmark);
const sha256=bytes=>createHash('sha256').update(bytes).digest('hex');
await fs.mkdir(out,{recursive:true});
const setup=JSON.parse(await fs.readFile(path.join(out,'capture-daily-setup.json'),'utf8'));
const result={sha:process.env.OWNER_PREVIEW_FEATURE_SHA,mode,status:'RUNNING',scope:'depot_first_checkpoint',benchmark_sha256:sha256(rawBenchmark),records:[],errors:[],screenshots:[],layouts:[],metric_definitions:{trusted_clicks:'Actual browser clicks with event.isTrusted, excluding private authentication and labelled setup. Native selects, file selection and text fills are separate operations; not physical-device tap counts.',new_required_facts:'Changed business facts absent from frozen source. Explicit source linkage is a new owner decision.',repeated_known_facts:'Changed controls manually supplying a fact already present in the uploaded frozen document.',review_acknowledgements:'Explicit quality/arithmetic/source review gestures. Not numeric re-entry.',source_save_to_confirm_clicks:'Trusted clicks after server source-save until first successful confirmation response, including deliberate lost-response recovery.'}};
assert.ok(result.sha,'OWNER_PREVIEW_FEATURE_SHA required');
assert.equal(setup.sha,result.sha);
const requested=process.env.CAPTURE_DAILY_ENGINE;
assert.ok(!requested || ['chromium','webkit'].includes(requested));
const engines=[['chromium',chromium,'H01','png'],['webkit',webkit,'H02','pdf']].filter(([e])=>!requested || e===requested);
const save=()=>fs.writeFile(path.join(out,'capture-daily-browser.json'),JSON.stringify(result,null,2)+'\n');
const decimal=value=>{assert.match(String(value),/^\d+(?:\.\d+)?$/);const [a,b='']=String(value).split('.');return BigInt(a)*1000000n+BigInt((b+'000000').slice(0,6));};
const exact=(a,b,label)=>assert.equal(decimal(a),decimal(b),label);

for(const [engine,type,caseId,extension] of engines){
 const fixture=benchmark.cases.find(c=>c.id===caseId),doc=fixture.input.document;
 const assetPath=`tests/fixtures/capture_daily_assets/${caseId}.${extension}`;
 const bytes=await fs.readFile(assetPath);
 const rec={engine,case_id:caseId,status:'RUNNING',stage:'launch',operations:[],barriers:[],source_sha256:sha256(bytes),asset_path:assetPath,source_caption:fixture.input.caption,stages:[],probes:{}};
 result.records.push(rec);await save();
 let browser,context,page,screenshotPhase=null,phase='setup',ordinal=0;
 const stage=async name=>{rec.stage=name;rec.stages.push({name,at:new Date().toISOString()});await save();};
 try{
  browser=await type.launch({timeout:30000});
  context=await browser.newContext({viewport:{width:390,height:844},acceptDownloads:true});
  page=await context.newPage();page.setDefaultTimeout(15000);page.setDefaultNavigationTimeout(25000);
  await installClickAudit(page);
  page.on('pageerror',e=>result.errors.push({engine,message:redactPilot(String(e))}));
  page.on('console',m=>{if(/Content Security Policy|Refused to apply/i.test(m.text()))(rec.csp ||= []).push({text:m.text(),screenshot:screenshotPhase});});
  const ready=prefix=>page.waitForFunction(p=>location.hash.slice(1).startsWith(p)&&document.querySelector('#main')?.dataset.renderedRoute===location.hash.slice(1),prefix,{timeout:25000});
  const read=async suffix=>{const response=await context.request.get(base+suffix,{timeout:20000});assert.equal(response.status(),200,`${suffix}: ${response.status()}`);return response.json();};
  const click=async(locator,label,kind='click')=>{
   await locator.scrollIntoViewIfNeeded();
   const start=await page.evaluate(()=>window.ownerFrictionClicks.length);
   const clickOrdinal=++ordinal;
   await locator.click();
   const events=await page.evaluate(s=>window.ownerFrictionClicks.slice(s),start);
   assert.ok(events.some(e=>e.trusted),`No trusted DOM click: ${label}`);
   rec.operations.push({ordinal:clickOrdinal,phase,kind,label,events});
  };
  const reveal=async locator=>{const parents=locator.locator('xpath=ancestor::details');for(let i=0;i<await parents.count();i++){const d=parents.nth(i);if(!await d.evaluate(e=>e.open))await click(d.locator(':scope > summary'),'Show relevant document fields');}};
  const field=async(name,value,origin='source_fact')=>{
   const locator=page.locator(`[name="${name}"]`);await reveal(locator);await locator.waitFor({state:'visible'});
   const before=await locator.inputValue();if(before===String(value))return;
   const tag=await locator.evaluate(e=>e.tagName);
   if(tag==='SELECT')await locator.selectOption(String(value));else{await click(locator,'Focus '+name);await locator.fill(String(value));}
   assert.equal(await locator.inputValue(),String(value));
   rec.operations.push({ordinal:++ordinal,phase,kind:origin==='review'?'review_acknowledgement':'field_edit',name,origin,before,value:String(value),operation:tag==='SELECT'?'selectOption':'fill'});
  };
  const snap=async name=>{
   assert.ok(!page.url().includes('/pilot/'),'Never screenshot authentication');
   await page.locator('#toast.show').waitFor({state:'hidden',timeout:10000});
   const filename=`${engine}-${page.viewportSize().width}-${name}.png`;screenshotPhase=filename;
   try{await page.screenshot({path:path.join(out,filename),fullPage:true,timeout:20000});}finally{screenshotPhase=null;}
   result.screenshots.push(filename);
  };
  const layouts=async name=>{
   for(const width of [390,320,768,1280]){
    await page.setViewportSize({width,height:width===320?568:844});
    const geometry=await page.evaluate(()=>({width:innerWidth,documentWidth:document.documentElement.scrollWidth,controls:[...document.querySelectorAll('#main .btn,#main input:not([type=checkbox]),#main select,#main textarea')].filter(e=>e.getClientRects().length).map(e=>{const r=e.getBoundingClientRect();return {tag:e.tagName,name:e.getAttribute('name'),text:e.textContent?.trim().slice(0,80),x:r.x,width:r.width,height:r.height};})}));
    result.layouts.push({engine,name,...geometry});assert.ok(geometry.documentWidth<=width+2,'Horizontal document overflow');
    for(const c of geometry.controls){assert.ok(c.x>=-2&&c.x+c.width<=width+2,`Control outside viewport: ${JSON.stringify(c)}`);assert.ok(c.height>=44,`Touch control too small: ${JSON.stringify(c)}`);}
    await snap(name);
   }
   await page.setViewportSize({width:390,height:844});
  };
  const probe=async label=>{
   const filename=path.join(out,`${engine}-${label}-sql-probe.json`);
   execFileSync(process.env.PYTHON || 'python',['scripts/capture_daily_sql_evidence.py','--probe','--source',rec.source_id,'--proposal',rec.proposal_id,'--output',filename],{env:process.env,timeout:25000,stdio:['ignore','pipe','pipe']});
   const value=JSON.parse(await fs.readFile(filename,'utf8'));rec.probes[label]=value;return value;
  };
  await stage('private_owner_login');await ownerLogin(page,base);await ready('home');
  const config=await read('/preview/config'),root='/v1/entities/'+config.entity_id;
  assert.equal(config.feature_sha,result.sha);assert.equal(config.backend_connected,true);assert.equal(config.daily_pilot,true);
  rec.entity_id=config.entity_id;
  page.on('response',response=>{if(response.request().method()==='POST'&&response.url()===base+root+'/owner/captures'&&[200,201].includes(response.status()))rec.source_saved_after_operation=ordinal;});
  // This separate owner-authored field observation is setup, not OCR input.
  await stage('create_separate_field_observation');
  await click(page.locator('[data-testid=home-cut]'),'Create separate field report');await ready('quick/harvest');
  await field('greenhouse',config.synthetic_fixture.areas[0].id,'setup');
  await field('date','2026-10-12','setup');await field('count','131','setup');await field('reporter','TEST capture daily '+engine,'setup');
  await click(page.getByRole('button',{name:'Сохранить полевой срез',exact:true}),'Save separate field report');await ready('report/');
  rec.report_id=page.url().split('#report/')[1];
  const report=await read(root+'/harvest/reports/'+rec.report_id);assert.equal(report.cut_count,131);assert.equal(report.field_weight_kg,null);rec.report_evidence_id=report.evidence_id;
  phase='capture';await stage('actual_file_picker');
  await click(page.getByRole('link',{name:'Фото / PDF чека депо',exact:true}),'Open depot capture');await ready('attach-depot/');
  const chooserPromise=page.waitForEvent('filechooser');await click(page.locator('[name=file]'),'Choose original document');const chooser=await chooserPromise;await chooser.setFiles(assetPath);
  rec.operations.push({ordinal:++ordinal,phase,kind:'file_picker_selection',case_id:caseId,sha256:rec.source_sha256});
  await page.waitForFunction(()=>!document.querySelector('[data-form=depot]')?.inert);
  assert.equal(await page.locator('[name=caption]').inputValue(),'','Fixture has no owner caption');
  if(mode==='baseline'){
   await stage('baseline_observed_manual_barrier');
   const fields=await page.locator('[data-form=depot] input,[data-form=depot] select').evaluateAll(es=>es.map(e=>({name:e.name,value:e.value,required:e.required})));
   rec.baseline_fields_before=fields;
   rec.barriers.push({kind:'manual_transcription_before_source_save',observed_controls:fields.filter(x=>['quality','kg','price'].includes(x.name))});
   phase='review';
   await field('depot',setup.depot_id);await field('date','2026-10-12');await field('currency',doc.currency);await field('document_number',doc.document_number);
   await field('link_evidence','TEST owner explicitly links this source with field report '+rec.report_id,'new_required_fact');
   for(let i=0;i<doc.lines.length;i++){
    if(i)await click(page.getByRole('button',{name:'Добавить строку качества',exact:true}),'Add manual line '+(i+1));
    const prefix=i?`line_${i}_`:'',line=doc.lines[i];
    await field(prefix+'quality',line.label);await field(prefix+'quality_category',['HIGHEST','FIRST','SECOND'][i],'review');
    await field(prefix+'kg',line.kg);await field(prefix+'price',line.unit_price);await field(prefix+'stated_amount',line.amount);
   }
   await field('stated_total',doc.total_amount);await field('alacak_value',doc.alacak);
   rec.barriers.push({kind:'document_adet_has_no_typed_control',document_adet:doc.adet,policy:'No invented allocation of document count to quality lines'});
   await layouts('baseline-manual-transcription');
   await click(page.locator('[data-form=depot] button[type=submit]'),'Submit manually transcribed receipt');await ready('review/');
   rec.proposal_id=page.url().split('#review/')[1];
   const proposal=await read(root+'/owner/confirmations/'+rec.proposal_id+'/preview');rec.source_id=proposal.source.id;
  }else{
   await stage('source_saved_before_preparation');
   const responsePromise=page.waitForResponse(r=>r.request().method()==='POST'&&r.url()===base+root+'/owner/captures',{timeout:25000});
   await click(page.locator('[data-form=depot] button[type=submit]'),'Save original without transcription');
   const response=await responsePromise;assert.equal(response.status(),201);rec.source_id=(await response.json()).id;rec.source_saved_after_operation=ordinal;
   await ready('source/');phase='review';
   await stage('wait_actual_local_ocr');
   // Read-only polling observes the worker. It never replaces OCR or creates a proposal.
   const deadline=Date.now()+150000;let source;
   while(Date.now()<deadline){
    source=await read(root+'/owner/captures/'+rec.source_id);
    if(source.job?.state==='FAILED')throw Error('Actual OCR job failed: '+source.job.error_code);
    if(source.job?.state==='SUCCEEDED'&&source.proposals?.length)break;
    await new Promise(r=>setTimeout(r,700));
   }
   assert.equal(source.job?.state,'SUCCEEDED','Actual OCR preparation exceeded 150 seconds');
   assert.equal(source.recognition.provider,'local');assert.equal(source.recognition.engine_version,'local-tesseract-tur-eng-v1');assert.match(source.recognition.engine,/tesseract/i);
   assert.equal(source.recognition.pages.length,engine==='webkit'?2:1);assert.ok(source.recognition.pages.every(p=>p.lines.length));
   rec.recognition=source.recognition;
   const proposals=source.proposals.filter(p=>p.action_type==='DEPOT_RECEIPT');assert.equal(proposals.length,1);assert.equal(source.proposals.length,1);
   rec.proposal_id=proposals[0].id;
   const link=page.locator(`a[href="#review/${rec.proposal_id}"]`).first();await link.waitFor({timeout:20000});await click(link,'Open prepared depot review');await ready('review/');
  }
  await stage('typed_prefill_review');
  let proposal=await read(root+'/owner/confirmations/'+rec.proposal_id+'/preview');
  rec.first_proposal={payload:proposal.revision.payload,basis:proposal.revision.basis,missing:proposal.revision.missing,version:proposal.version,extraction_run_id:proposal.revision.extraction_run_id};
  assert.equal(proposal.action_type,'DEPOT_RECEIPT');assert.equal(proposal.source.text,null);assert.equal(proposal.revision.payload.depot_id,setup.depot_id);
  assert.equal(proposal.revision.payload.event_date,'2026-10-12');assert.equal(proposal.revision.payload.currency,doc.currency);assert.equal(proposal.revision.payload.document_number,doc.document_number);
  const payload=proposal.revision.payload;assert.equal(payload.lines.length,doc.lines.length);
  for(let i=0;i<doc.lines.length;i++){
   const line=payload.lines[i],expected=doc.lines[i];assert.equal(line.original_quality_name,expected.label);exact(line.net_weight_kg,expected.kg,'Prefilled weight');exact(line.unit_price,expected.unit_price,'Prefilled price');exact(line.stated_amount,expected.amount,'Prefilled stated amount');assert.equal(line.count,null,'Never allocate document ADET to a quality line');assert.deepEqual(line.allocations,[]);
  }
  exact(payload.stated_total,doc.total_amount,'Prefilled total');exact(payload.alacak_value,doc.alacak,'Alacak retained as source balance');
  assert.equal(payload.arithmetic_reviewed,false);
  const pre=await probe('before_confirmation');assert.equal(pre.effects.length,0);assert.equal(pre.confirm_actions,0);assert.equal(pre.settlements.length,0);
  if(mode==='candidate'){
   assert.equal(payload.quality_reviewed,false);assert.deepEqual(payload.batch_ids,[],'Field context must not silently establish linkage');
   assert.equal(payload.document_count,93,'Document-level ADET must be preserved without invented per-line allocation');
   await layouts('ocr-prefilled-review');
   assert.equal(await page.locator('[data-action=confirm-proposal]').isDisabled(),true);
   await click(page.locator('[data-action=review-depot-quality]'),'Review displayed depot quality candidates','review_acknowledgement');
   await page.waitForFunction(()=>!document.querySelector('[data-action=review-depot-quality]'));
   await click(page.locator(`a[href="#edit-depot/${rec.proposal_id}"]`),'Explicitly choose field linkage');await ready('edit-depot/');
   const reportCheckbox=page.locator(`[name="report_${rec.report_id}"]`);await reveal(reportCheckbox);assert.equal(await reportCheckbox.isChecked(),false);
   await click(reportCheckbox,'Approve explicit field/source linkage','review_acknowledgement');
   await field('link_evidence','TEST owner explicitly links this source with field report '+rec.report_id,'new_required_fact');
   await click(page.locator('[data-form=depot] button[type=submit]'),'Save explicit linkage without retyping source facts');await ready('review/');
  }
  proposal=await read(root+'/owner/confirmations/'+rec.proposal_id+'/preview');
  assert.equal(proposal.revision.basis.arithmetic.field_count,131);assert.equal(proposal.revision.payload.lines.every(l=>l.count===null),true);
  if(mode==='candidate'){assert.equal(proposal.revision.basis.arithmetic.document_count,93);assert.equal(proposal.revision.basis.arithmetic.depot_count,null);assert.equal(proposal.revision.basis.arithmetic.count_discrepancy,false);}
  await stage('explicit_arithmetic_review');
  await click(page.locator('[data-action=review-depot-arithmetic]'),'Review arithmetic and independent field/document counts','review_acknowledgement');
  await page.getByText('Арифметика проверена владельцем',{exact:true}).waitFor();
  if(await page.locator('[data-action=review-document]').count())await click(page.locator('[data-action=review-document]'),'Review recognition warnings and original','review_acknowledgement');
  proposal=await read(root+'/owner/confirmations/'+rec.proposal_id+'/preview');rec.ready_proposal={payload:proposal.revision.payload,basis:proposal.revision.basis,missing:proposal.revision.missing};
  assert.deepEqual(proposal.revision.missing,[],'Critical facts must be reviewed before confirmation');assert.equal(await page.locator('[data-action=confirm-proposal]').isDisabled(),false);
  await layouts('ready-explicit-review');
  const beforePost=await probe('ready_to_confirm');assert.equal(beforePost.effects.length,0);
  await stage('lost_confirm_response_and_retry');let lost=false;const attempts=[];
  const interception=`**/owner/confirmations/${rec.proposal_id}/confirm`;
  await page.route(interception,async route=>{
   attempts.push({idempotency_key:route.request().headers()['idempotency-key'],body:route.request().postDataJSON()});
   if(!lost){const committed=await route.fetch({timeout:25000});assert.equal(committed.status(),200);rec.committed_response=await committed.json();lost=true;await route.abort('failed');}else await route.continue();
  });
  await click(page.locator('[data-action=confirm-proposal]'),'Confirm one sale and AR','effect_confirmation');
  await page.locator('#action-error').filter({hasText:'Ответ сервера не получен'}).waitFor();assert.equal(lost,true);
  const lostProbe=await probe('response_lost');assert.equal(lostProbe.effects.length,1);assert.equal(lostProbe.confirm_actions,1);assert.equal(lostProbe.settlements.length,1);
  await click(page.locator('[data-action=confirm-proposal]'),'Retry same confirmation after lost response','recovery_click');await ready('depot/');await page.unroute(interception);
  rec.confirmation_completed_after_operation=ordinal;
  assert.ok(attempts.length>=1);if(attempts.length>1){assert.equal(new Set(attempts.map(a=>a.idempotency_key)).size,1);assert.deepEqual(attempts[0].body,attempts[1].body);}
  rec.confirm_attempts=attempts;rec.retry_via_receipt_lookup=attempts.length===1;rec.lost_response=true;
  const retried=await probe('after_retry');assert.deepEqual(retried,lostProbe,'Retry changed persisted source/effect rows');
  rec.settlement_id=page.url().split('#depot/')[1];let settlement=await read(root+'/harvest/settlements/'+rec.settlement_id);rec.obligation_id=settlement.obligation_id;
  assert.equal(settlement.status,'posted');exact(settlement.total_amount,doc.total_amount);assert.equal(settlement.payment_id,null);assert.equal(settlement.lines.length,3);assert.equal(settlement.reconciliation.field_count,131);
  assert.equal(settlement.reconciliation.depot_count,null,'Document ADET does not establish per-quality line coverage');
  await stage('original_browser_download_and_reload');
  const originalURL=base+root+'/owner/captures/'+rec.source_id+'/original';
  const original=await context.request.get(originalURL);assert.equal(original.status(),200);assert.deepEqual(await original.body(),bytes);
  const downloadPromise=page.waitForEvent('download');const originalRoute=page.url();await click(page.getByRole('link',{name:/неизменённые байты/}),'Download preserved original','destination_click');
  const download=await downloadPromise;assert.equal(download.url(),originalURL);const downloadPath=path.join(out,`${engine}-clicked-original.${extension}`);await download.saveAs(downloadPath);
  rec.clicked_original_sha256=sha256(await fs.readFile(downloadPath));assert.equal(rec.clicked_original_sha256,rec.source_sha256);assert.equal(page.url(),originalRoute);rec.original_route_preserved=true;
  await page.reload();await ready('depot/');settlement=await read(root+'/harvest/settlements/'+rec.settlement_id);assert.equal(settlement.obligation_id,rec.obligation_id);
  const afterReport=await read(root+'/harvest/reports/'+rec.report_id);assert.equal(afterReport.cut_count,131);assert.equal(afterReport.field_weight_kg,null);
  await layouts('posted-sale-and-ar');
  rec.status=mode==='baseline'?'BASELINE_RECORDED':'PASS';rec.stage='complete';
 }catch(error){rec.status='FAIL';rec.failure={stage:rec.stage,message:redactPilot(error.message),stack:redactPilot(error.stack || '')};if(page&&!page.isClosed()&&!page.url().includes('/pilot/'))await page.screenshot({path:path.join(out,`${engine}-failure.png`),fullPage:true,timeout:10000}).catch(()=>{});}
 finally{
  const ops=rec.operations.filter(o=>o.phase!=='setup');
  rec.metrics={trusted_clicks:ops.filter(o=>o.events).length,capture_clicks:ops.filter(o=>o.phase==='capture'&&o.events).length,review_clicks:ops.filter(o=>o.phase==='review'&&o.events).length,new_required_facts:ops.filter(o=>o.kind==='field_edit'&&o.origin==='new_required_fact').length,repeated_known_facts:ops.filter(o=>o.kind==='field_edit'&&o.origin==='source_fact').length,review_acknowledgements:ops.filter(o=>o.kind==='review_acknowledgement').length,text_fill_operations:ops.filter(o=>o.operation==='fill').length,native_select_operations:ops.filter(o=>o.operation==='selectOption').length,file_picker_selections:ops.filter(o=>o.kind==='file_picker_selection').length,source_save_to_confirm_clicks:rec.confirmation_completed_after_operation==null?null:ops.filter(o=>o.events&&o.ordinal>rec.source_saved_after_operation&&o.ordinal<=rec.confirmation_completed_after_operation).length,setup_operations:rec.operations.filter(o=>o.phase==='setup').length};
  if(context)await context.close().catch(()=>{});if(browser)await browser.close().catch(()=>{});await save();
 }
}
result.status=result.records.every(r=>r.status===(mode==='baseline'?'BASELINE_RECORDED':'PASS'))&&!result.errors.length?(mode==='baseline'?'BASELINE_RECORDED':'PASS'):'FAIL';
await save();console.log(JSON.stringify({status:result.status,mode,records:result.records.map(r=>({engine:r.engine,stage:r.stage,status:r.status,metrics:r.metrics,failure:r.failure?.message}))}));
if(result.status==='FAIL')process.exitCode=1;
