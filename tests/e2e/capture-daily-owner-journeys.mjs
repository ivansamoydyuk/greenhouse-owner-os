/** Actual owner capture journeys. Synthetic immutable corpus; approved CI only.
 * Fault injection delays/drops real responses, returns an explicit 503 failure, or fails local persistence.
 * It never fabricates OCR, domain data, owner identity, or a successful write.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {chromium,webkit} from 'playwright';
import {ownerLogin,workerLogin,pilotCredentials,pilotMode,expiredCookie,redactPilot,resetFixtureRateLimits} from './helpers/pilot-auth.mjs';
import {readOwnerDrafts} from './helpers/owner-draft-observer.mjs';
import {installClickAudit} from './friction-metrics.mjs';
import {observeBrowserDiagnostics,safePath} from './helpers/browser-diagnostics.mjs';
import {observeDiscardedReadTerminal} from './helpers/discarded-read-terminal.mjs';
const mode=process.env.CAPTURE_DAILY_MODE||'candidate',base=process.env.PREVIEW_URL||'http://127.0.0.1:4173',out=process.env.E2E_ARTIFACTS||'artifacts/capture-daily-owner';
assert.ok(['baseline','candidate'].includes(mode));assert.equal(pilotMode,true);assert.ok(['localhost','127.0.0.1'].includes(new URL(base).hostname));
const sha=process.env.OWNER_PREVIEW_FEATURE_SHA;assert.match(sha||'',/^[0-9a-f]{40}$/);
const digest=value=>createHash('sha256').update(value).digest('hex'),rawCorpus=await fs.readFile('tests/fixtures/capture_daily_benchmark.json'),corpus=JSON.parse(rawCorpus),fixture=id=>corpus.cases.find(c=>c.id===id);
const setup=JSON.parse(await fs.readFile(path.join(out,'capture-daily-owner-setup.json'),'utf8'));assert.equal(setup.sha,sha);
const credentials=await pilotCredentials();await fs.mkdir(out,{recursive:true});
const result={sha,mode,status:'RUNNING',scope:'short_text_and_supplier_invoice_plus_capture_recovery',benchmark_sha256:digest(rawCorpus),records:[],screenshots:[],layouts:[],errors:[],metric_definitions:{trusted_clicks:'Executed Playwright clicks with a trusted DOM event, excluding authentication/setup. Native selects, file selections and fills are separate; these are not physical-device tap counts.',repeated_known_facts:'Changed business controls that re-enter source facts, including correction of a wrong prefill.',new_required_facts:'Changed controls supplying new owner decisions absent from source, such as economic intent or explicit receipt linkage.',review_acknowledgements:'Authority and original-quality checks, not repeated numeric entry.',baseline_barrier:'A witnessed unsupported or manual path; never a positive preparation claim.'}};
const bootstrapArms=mode==='candidate'?['BOOTSTRAP_NORMAL','BOOTSTRAP_ROUTED','BOOTSTRAP_503','BOOTSTRAP_503_PERSISTENT','BOOTSTRAP_TIMEOUT']:[];
result.bootstrap_matrix={cases:bootstrapArms,transport_positions:['after_relogin','after_held_edit'],application_deadline_ms:15000,route_lifecycle:'One failure response; forwarding handler retained through bootstrap readiness or the held-read case boundary. Historical times:1 failures remain archived at5a8a851.',repeat_rationale:'Two fixed positions match the two observed failures. No retry-to-pass; the first failure stops its isolated arm.'};
const write=()=>fs.writeFile(path.join(out,'capture-daily-owner-browser.json'),JSON.stringify(result,null,2)+'\n');
const eqDecimal=(a,b)=>{const exact=v=>{assert.match(String(v),/^\d+(?:\.\d{1,6})?$/);const [i,f='']=String(v).split('.');return BigInt(i)*1000000n+BigInt((f+'000000').slice(0,6));};assert.equal(exact(a),exact(b));};
// Metadata-only observations: no headers, bodies, cookies, query/hash values,
// storage contents, source text, exception text or authentication key material.
async function installCaptureDiagnostics(page){
 const observer=await observeBrowserDiagnostics(page),started=Date.now(),limit=400;
 const history=[],requestNavigations=new Map(),documents=new Map();let dropped=0,navigationId=0,requestId=0,documentId=0;
 const append=row=>{history.push(row);if(history.length>limit){history.shift();dropped++;}};
 const phases=new Set(['config-read','draft-key-read','configure','configured','refresh-data','harvest-navigation-read','finance-navigation-read','generation-mismatch','render','render-returned','error','recovery']);
 page.on('framenavigated',frame=>{if(frame===page.mainFrame())append({event:'navigation',navigation_id:++navigationId,atMs:Date.now()-started,path:safePath(frame.url())});});
 // The reused observer registers its request listener first. Both counters see
 // exactly the same page requests, including requests whose body never ends.
 page.on('request',request=>{
  const documentRequest=request.isNavigationRequest()&&request.frame()===page.mainFrame();
  requestNavigations.set(++requestId,{navigation_id:navigationId+(documentRequest?1:0),document_request:documentRequest});
  if(requestNavigations.size>limit)requestNavigations.delete(requestNavigations.keys().next().value);
 });
 await page.exposeBinding('__captureBootstrapMetadata',(source,value)=>{
  if(source.frame!==page.mainFrame()||!value||!Number.isFinite(value.document_stamp)||!Number.isFinite(value.atMs))return;
  if(!documents.has(value.document_stamp)){documents.set(value.document_stamp,++documentId);if(documents.size>100)documents.delete(documents.keys().next().value);}
  const row={event:value.event==='document'?'document':'bootstrap',document_id:documents.get(value.document_stamp),navigation_id:navigationId,atMs:Date.now()-started,documentAtMs:value.atMs};
  if(phases.has(value.phase))row.phase=value.phase;
  if(phases.has(value.previous_phase))row.previous_phase=value.previous_phase;
  if(Number.isSafeInteger(value.generation)&&value.generation>=0)row.generation=value.generation;
  if(row.event==='document'||row.phase||row.previous_phase)append(row);
 });
 await page.addInitScript(allowed=>{
  const phases=new Set(allowed),events=[];let dropped=0,seenApp=null;
  const record=value=>{
   const row={...value,document_stamp:performance.timeOrigin,atMs:performance.now()};
   events.push(row);if(events.length>100){events.shift();dropped++;}
   // Observation must never block page initialization or application promises.
   Promise.resolve(window.__captureBootstrapMetadata(row)).catch(()=>{});
  };
  window.__captureBootstrapHistory=()=>({dropped,entries:events.map(x=>({...x}))});
  record({event:'document'});
  // Observe settlement without consuming bodies or replacing the returned
  // native promises. The separate ring is document-local and captured at each
  // matrix boundary/failure, before the next document can discard it.
  const fetchEvents=[],responses=new WeakMap();let fetchId=0,bodyId=0,fetchDropped=0;
  const safe=value=>{try{const p=new URL(value,location.href).pathname;return /^\/(?:v1\/|pilot\/(?:session|draft-key|participants|receipts|login|logout)(?:\/|$)|preview\/config$|r3\/[a-z0-9.-]+$|assets\/[a-z0-9.-]+$)/i.test(p)?p.slice(0,240):'/other';}catch{return '/invalid';}};
  const fetchRecord=row=>{fetchEvents.push({...row,atMs:performance.now()});if(fetchEvents.length>200){fetchEvents.shift();fetchDropped++;}};
  window.__captureFetchJsonDiagnostics=()=>({dropped:fetchDropped,entries:fetchEvents.map(row=>({...row}))});
  if(typeof window.fetch==='function'&&typeof Response==='function'){
   const nativeFetch=window.fetch,nativeJson=Response.prototype.json;
   window.fetch=function(...args){
    const input=args[0],method=String(args[1]?.method||input?.method||'GET').toUpperCase();
    const meta={fetch_id:++fetchId,path:safe(typeof input==='string'||input instanceof URL?String(input):input?.url),method:['GET','POST','PUT','PATCH','DELETE','HEAD','OPTIONS'].includes(method)?method:'UNKNOWN'};
    fetchRecord({event:'fetch-start',...meta});let pending;
    try{pending=Reflect.apply(nativeFetch,this,args);}catch(error){fetchRecord({event:'fetch-rejected',...meta});throw error;}
    void pending.then(response=>{responses.set(response,meta);fetchRecord({event:'fetch-resolved',...meta,status:response.status});},()=>fetchRecord({event:'fetch-rejected',...meta}));
    return pending;
   };
   Response.prototype.json=function(...args){
    const meta=responses.get(this),id=meta?++bodyId:null;if(meta)fetchRecord({event:'json-start',...meta,body_id:id});let pending;
    try{pending=Reflect.apply(nativeJson,this,args);}catch(error){if(meta)fetchRecord({event:'json-rejected',...meta,body_id:id});throw error;}
    if(meta)void pending.then(()=>fetchRecord({event:'json-settled',...meta,body_id:id}),()=>fetchRecord({event:'json-rejected',...meta,body_id:id}));
    return pending;
   };
  }
  new MutationObserver(mutations=>{
   for(let i=0;i<mutations.length;i++){
    const mutation=mutations[i];if(mutation.type!=='attributes'||mutation.target.id!=='app'||mutation.attributeName!=='data-bootstrap-stage')continue;
    // Attribute reads alone see only the last value in one microtask. The next
    // mutation's oldValue retains each intermediate phase, including pre-timeout.
    const next=mutations.slice(i+1).find(m=>m.target===mutation.target&&m.attributeName===mutation.attributeName);
    const phase=next?next.oldValue:mutation.target.getAttribute('data-bootstrap-stage');
    const row={event:'bootstrap',generation:Number(mutation.target.getAttribute('data-bootstrap-generation'))};
    if(phases.has(phase))row.phase=phase;if(phases.has(mutation.oldValue))row.previous_phase=mutation.oldValue;
    if(row.phase||row.previous_phase)record(row);
   }
   const app=document.querySelector('#app');if(app&&app!==seenApp){seenApp=app;const phase=app.getAttribute('data-bootstrap-stage');if(phases.has(phase))record({event:'bootstrap',phase,generation:Number(app.getAttribute('data-bootstrap-generation'))});}
  }).observe(document,{subtree:true,childList:true,attributes:true,attributeOldValue:true,attributeFilter:['data-bootstrap-stage']});
 },[...phases]);
 return {async capture(){
  const captured=await observer.capture();
  const local=await page.evaluate(()=>({bootstrap:window.__captureBootstrapHistory?.(),fetchJson:window.__captureFetchJsonDiagnostics?.(),document_stamp:performance.timeOrigin})).catch(()=>null),current=local?.bootstrap;
  // The final document's bounded buffer covers a binding notification still in
  // flight. Sanitize it again; never serialize arbitrary page-supplied fields.
  const currentEntries=[];for(const value of current?.entries||[]){
   const row={event:value.event==='document'?'document':'bootstrap'};
   if(Number.isFinite(value.atMs))row.atMs=value.atMs;
   if(phases.has(value.phase))row.phase=value.phase;if(phases.has(value.previous_phase))row.previous_phase=value.previous_phase;
   if(Number.isSafeInteger(value.generation)&&value.generation>=0)row.generation=value.generation;
   currentEntries.push(row);
  }
  const nativeEntries=[];for(const value of local?.fetchJson?.entries||[]){
   if(!['fetch-start','fetch-resolved','fetch-rejected','json-start','json-settled','json-rejected'].includes(value.event))continue;
   const row={event:value.event,path:safePath('http://diagnostic.invalid'+String(value.path||''))};
   for(const key of ['fetch_id','body_id','atMs','status'])if(Number.isFinite(value[key]))row[key]=value[key];
   if(['GET','POST','PUT','PATCH','DELETE','HEAD','OPTIONS','UNKNOWN'].includes(value.method))row.method=value.method;
   nativeEntries.push(row);
  }
  return {version:2,current_document_fetch_json:{navigation_id:navigationId,document_id:documents.get(local?.document_stamp)||null,dropped:Number.isSafeInteger(local?.fetchJson?.dropped)?local.fetchJson.dropped:0,entries:nativeEntries.slice(-200)},...captured,network:{...captured.network,entries:captured.network.entries.map(row=>({...row,...(requestNavigations.get(row.id)||{})}))},navigation_and_bootstrap:{dropped,entries:history.map(x=>({...x}))},current_document_bootstrap:{dropped:Number.isSafeInteger(current?.dropped)?current.dropped:0,entries:currentEntries.slice(-100)}};
 }};
}
// One fault/held read, stable interception lifetime. Playwright times:1 can
// disable Chromium Fetch while sibling requests are still being dispatched.
// Keep a consumed handler forwarding real traffic until an explicit safe boundary.
async function installRetainedReadRoute(page,matcher,first,evidence){
 let consumed=false,firstRequest;
 Object.assign(evidence,{lifecycle:'retained_until_explicit_cleanup',first_calls:0,passthrough_calls:0,observed_statuses:[]});
 const response=reply=>{if(reply.request()===firstRequest)evidence.observed_statuses.push(reply.status());};
 const handler=async route=>{
  if(consumed){evidence.passthrough_calls++;return route.continue();}
  consumed=true;firstRequest=route.request();evidence.first_calls++;return first(route);
 };
 page.on('response',response);await page.route(matcher,handler);
 return async()=>{await page.unroute(matcher,handler);page.off('response',response);evidence.cleaned_up=true;};
}
async function boundedDiagnosticCapture(observer){
 let timer;try{return await Promise.race([observer.capture(),new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Optional diagnostic deadline')),2000);})]);}finally{clearTimeout(timer);}
}
const engines=[['chromium',chromium],['webkit',webkit]].filter(([name])=>!process.env.CAPTURE_DAILY_ENGINE||process.env.CAPTURE_DAILY_ENGINE===name);
assert.equal(engines.length,1,'T03 requires an isolated engine TEST database');assert.equal(engines[0][0],setup.engine);
for(const [engine,browserType] of engines){
 await resetFixtureRateLimits();const browser=await browserType.launch({timeout:30000});
 const record={engine,cases:[],security:[],stage:'start'};result.records.push(record);
 const run=async(id,work)=>{
  const c={id,status:'RUNNING',stage:'owner_login',operations:[],barriers:[]};record.cases.push(c);await write();
  const context=await browser.newContext({viewport:{width:390,height:844},acceptDownloads:true}),page=await context.newPage();let phase='capture',ordinal=0;const retainedCleanups=[];
  page.setDefaultTimeout(15000);page.setDefaultNavigationTimeout(25000);await installClickAudit(page);
  const diagnostic=await installCaptureDiagnostics(page).catch(()=>null);c.diagnostics_available=Boolean(diagnostic);
  page.on('pageerror',error=>result.errors.push({engine,case_id:id,message:redactPilot(error.message)}));
  const retainRoute=async(matcher,first,label)=>{
   const evidence={label};(c.retained_routes ||= []).push(evidence);
   retainedCleanups.push(await installRetainedReadRoute(page,matcher,first,evidence));return evidence;
  };
  const stage=async value=>{c.stage=value;record.stage=id+':'+value;await write();};
  const ready=prefix=>page.waitForFunction(p=>{const r=location.hash.slice(1)||'home';return r.startsWith(p)&&document.querySelector('#main')?.dataset.renderedRoute===r;},prefix,{timeout:25000});
  const read=async suffix=>{const r=await context.request.get(base+suffix,{timeout:20000});assert.equal(r.status(),200,suffix+' returned '+r.status());return r.json();};
  const visibleAction=async(locator,label)=>{await locator.evaluate(el=>el.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'}));const handle=await locator.elementHandle();await page.waitForFunction(el=>{const r=el.getBoundingClientRect(),points=[[r.left+r.width/2,r.top+r.height/2],[r.left+5,r.top+5],[r.right-5,r.bottom-5]];return r.width>=44&&r.height>=44&&points.every(([x,y])=>x>=0&&x<innerWidth&&y>=0&&y<innerHeight&&el.contains(document.elementFromPoint(x,y)));},handle,{timeout:5000});const geometry=await locator.evaluate(el=>{const r=el.getBoundingClientRect();return {width:innerWidth,height:innerHeight,box:{x:r.x,y:r.y,width:r.width,height:r.height},center_hit:el.contains(document.elementFromPoint(r.left+r.width/2,r.top+r.height/2)),dock_top:document.querySelector('.dock')?.getBoundingClientRect().top};});(c.action_visibility ||= []).push({label,...geometry});};
  const viewportProof=async(locator,label)=>{for(const view of [{width:320,height:568},{width:390,height:844},{width:390,height:430}]){await page.setViewportSize(view);await visibleAction(locator,label);const file=`${engine}-${id}-${label}-${view.width}x${view.height}-viewport.png`;await page.screenshot({path:path.join(out,file),fullPage:false,timeout:20000});result.screenshots.push(file);}c.viewport_limitations='Reduced viewport only; physical keyboard, native safe-area and physical iPhone are not verified.';await page.setViewportSize({width:390,height:844});};
  const click=async(locator,label,kind='click')=>{await locator.scrollIntoViewIfNeeded();if(page.viewportSize().width<=390&&await locator.evaluate(el=>el.matches('.primary,button[type=submit],[data-action=confirm-proposal]')))await visibleAction(locator,label);const start=await page.evaluate(()=>window.ownerFrictionClicks.length),n=++ordinal;await locator.click();const events=await page.evaluate(i=>window.ownerFrictionClicks.slice(i),start);assert.ok(events.some(x=>x.trusted),label+' must emit a trusted click');c.operations.push({ordinal:n,phase,kind,label,events});};
  // A full reload destroys the document-local click array. Transfer only the
  // actual trusted event through a binding before the application navigates.
  const clickReload=async(locator,label)=>{
   let resolve;const observed=new Promise(yes=>resolve=yes);
   await page.exposeBinding('__captureTrustedReloadClick',(source,value)=>{
    if(source.frame===page.mainFrame()&&value?.trusted===true&&value?.action==='bootstrap-retry'&&value?.tag==='BUTTON')resolve({trusted:true,tag:'BUTTON',action:'bootstrap-retry'});
   });
   await locator.evaluate(el=>el.addEventListener('click',event=>{
    void window.__captureTrustedReloadClick({trusted:event.isTrusted,tag:el.tagName,action:el.getAttribute('data-action')}).catch(()=>{});
   },{capture:true,once:true}));
   await locator.scrollIntoViewIfNeeded();await visibleAction(locator,label);await locator.click();
   let timer,event;try{event=await Promise.race([observed,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error(label+' must emit a trusted click before navigation')),15000);})]);}finally{clearTimeout(timer);}
   c.operations.push({ordinal:++ordinal,phase,kind:'recovery_click',label,events:[event]});
  };
  const reveal=async locator=>{const parents=locator.locator('xpath=ancestor::details');for(let i=0;i<await parents.count();i++){const parent=parents.nth(i);if(!await parent.evaluate(e=>e.open))await click(parent.locator(':scope > summary'),'Reveal related fields');}};
  const input=async(locator,value,{key,origin='source_fact',initial=false}={})=>{await reveal(locator);const previous=await locator.inputValue();if(previous===String(value))return;const tag=await locator.evaluate(e=>e.tagName);if(tag==='SELECT')await locator.selectOption(String(value));else{await click(locator,'Focus '+key);await locator.fill(String(value));}assert.equal(await locator.inputValue(),String(value));c.operations.push({ordinal:++ordinal,phase,kind:initial?'initial_capture_input':origin==='review'?'review_acknowledgement':'field_edit',key,origin,previous,value:String(value),operation:tag==='SELECT'?'selectOption':'fill'});};
  const field=(name,value,origin)=>input(page.locator(`[name="${name}"]`),value,{key:name,origin});
  const layouts=async name=>{assert.ok(!page.url().includes('/pilot/'));for(const width of [390,320,768,1280]){await page.setViewportSize({width,height:width===320?568:844});const b=await page.evaluate(()=>({viewport:innerWidth,scroll:document.documentElement.scrollWidth}));assert.ok(b.scroll<=b.viewport+2,JSON.stringify(b));result.layouts.push({engine,case_id:id,name,...b});await page.locator('#toast.show').waitFor({state:'hidden',timeout:10000});const filename=`${engine}-${id}-${width}-${name}.png`;await page.screenshot({path:path.join(out,filename),fullPage:true,timeout:20000});result.screenshots.push(filename);}await page.setViewportSize({width:390,height:844});};
  const observe=async(label,boundary=false)=>{const filename=path.join(out,`${engine}-${id}-${label}-state.json`);execFileSync(process.env.PYTHON||'python',['scripts/capture_daily_owner_sql_evidence.py',boundary?'--probe-boundary':'--probe','--output',filename],{env:process.env,timeout:25000,stdio:['ignore','pipe','pipe']});return JSON.parse(await fs.readFile(filename,'utf8'));};
  const prepare=async sourceId=>{const end=Date.now()+150000;let source;while(Date.now()<end){source=await read(root+'/owner/captures/'+sourceId);if(source.job?.state==='FAILED')throw Error('Real preparation failed: '+source.job.error_code);if(source.job?.state==='SUCCEEDED')return source;await new Promise(r=>setTimeout(r,600));}throw Error('Real source preparation exceeded 150 seconds');};
  const preparedLink=async(source,proposalId=source.proposals[0].id)=>{
   const href='#review/'+proposalId,link=page.locator(`#main a[href="${href}"]`).first();
   if(mode==='candidate'){
    // API preparation may finish before the source's automatic UI refresh.
    // Await that exact terminal render instead of scrolling a soon-detached
    // Refresh button. Manual refresh is reserved for its visible stopped state.
    const waitRendered=allowStopped=>page.waitForFunction(({sourceId,href,allowStopped})=>{
     const expected='source/'+sourceId,main=document.querySelector('#main');
     if(location.hash!=='#'+expected||main?.dataset.renderedRoute!==expected)return false;
     const status=main.querySelector('[data-preparation-refresh]');
     const target=[...main.querySelectorAll('a[href]')].some(node=>node.getAttribute('href')===href);
     return target&&!status||Boolean(allowStopped&&status?.textContent.startsWith('Автообновление остановлено.'));
    },{sourceId:source.id,href,allowStopped},{timeout:20000});
    await waitRendered(true);
    if(await page.locator('#main [data-preparation-refresh]').count()){
     await click(page.locator('[data-action=refresh]'),'Refresh actual prepared source');
     await waitRendered(false);
    }
   }else if(!await link.count())await click(page.locator('[data-action=refresh]'),'Refresh actual prepared source');
   await link.waitFor({timeout:20000});return link;
  };
  const openPrepared=async source=>{await click(await preparedLink(source),'Open prepared action');await ready('review/');};
  const edit=async(key,value,origin='source_fact',invoice=false)=>{
   const locator=page.locator(`[data-action="${invoice?'invoice-edit:':'edit:'}${key}"]`).first();await reveal(locator);await click(locator,'Review or correct '+key);const valueField=page.locator('#sheet-root [name=value]');await input(valueField,value,{key,origin});
   const oldMain=await page.locator('#main').elementHandle(),savedRoute=page.url().split('#')[1];
   const committed=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/owner/confirmations/'+c.proposal_id+'/revise'));
   await click(page.locator('#sheet-root button[type=submit]'),'Save reviewed '+key);const response=await committed;assert.equal(response.status(),200);const revision=await response.json();assert.equal(revision.id,c.proposal_id);if(key==='amount')eqDecimal(revision.revision.payload[key],value);else assert.equal(String(revision.revision.payload[key]),String(value));
   await page.locator('#sheet-root [role=dialog]').waitFor({state:'hidden'});
   // Sheet closure precedes finish()->refreshData()->render(). Wait for that
   // real DOM replacement, not stale same-route controls from the old revision.
   await page.waitForFunction(main=>!main.isConnected,oldMain);await ready(savedRoute);await oldMain.dispose();
   (c.edit_commits ||= []).push({key,proposal_id:revision.id,version:revision.version,value:revision.revision.payload[key],new_render_observed:true});
  };
  const actualFile=async caseId=>{const extension=caseId==='P02'?'pdf':'png',asset=`tests/fixtures/capture_daily_assets/${caseId}.${extension}`,bytes=await fs.readFile(asset);const picker=page.waitForEvent('filechooser');await click(page.locator('[name=file]'),'Choose fixed '+caseId+' original');await (await picker).setFiles(asset);await page.waitForFunction(()=>{const e=document.querySelector('[name=file]');return e?.dataset.attachmentState==='ready';});c.asset_path=asset;c.source_sha256=digest(bytes);c.operations.push({ordinal:++ordinal,phase,kind:'file_selection',case_id:caseId,sha256:c.source_sha256});return bytes;};
  const original=async(sourceId,expected)=>{const response=await context.request.get(base+root+'/owner/captures/'+sourceId+'/original');assert.equal(response.status(),200);assert.deepEqual(await response.body(),Buffer.from(expected));c.original_readback_sha256=digest(await response.body());};
  const lostConfirmation=async(proposalId,button,after)=>{await viewportProof(button,'confirm-ready');const routePattern=`**/owner/confirmations/${proposalId}/confirm`;let lost=false;const attempts=[];await page.route(routePattern,async route=>{attempts.push({key:route.request().headers()['idempotency-key'],body:route.request().postDataJSON()});if(!lost){const response=await route.fetch({timeout:25000});assert.equal(response.status(),200);lost=true;await route.abort('failed');}else await route.continue();});await click(button,'Confirm intended effect','effect_confirmation');await page.locator('#action-error').filter({hasText:'Ответ сервера не получен'}).waitFor();for(const width of [390,320]){await page.setViewportSize({width,height:width===320?568:844});await page.locator('#action-error').evaluate(el=>el.scrollIntoView({block:'center',behavior:'instant'}));const file=`${engine}-${id}-${width}-lost-response-viewport.png`;await page.screenshot({path:path.join(out,file),fullPage:false});result.screenshots.push(file);}await page.setViewportSize({width:390,height:844});c.after_lost_response=await observe('lost-response');await click(button,'Retry same confirmation','recovery_click');await after();await page.unroute(routePattern);c.after_retry=await observe('retried');assert.deepEqual(c.after_retry,c.after_lost_response,'Confirmation retry changed business state');assert.ok(lost);assert.equal(new Set(attempts.map(a=>a.key)).size,1);if(attempts.length>1)assert.deepEqual(attempts[0].body,attempts[1].body);c.confirm_attempts=attempts;c.lost_confirm_response=true;};
  const checkpoint=async label=>{
   assert.match(label,/^[a-z0-9-]+$/);await write();
   if(!diagnostic)return null;
   const captured=await boundedDiagnosticCapture(diagnostic),filename=`${engine}-${id}-${label}-diagnostics.json`;
   await fs.writeFile(path.join(out,filename),JSON.stringify({sha,mode,engine,case_id:id,status:c.status,stage:c.stage,...captured},null,2)+'\n');
   (c.diagnostic_checkpoints ||= []).push(filename);await write();return filename;
  };
  let config,root;
  try{
   await ownerLogin(page,base);await ready('home');config=await read('/preview/config');root='/v1/entities/'+config.entity_id;assert.equal(config.feature_sha,sha);assert.equal(config.daily_pilot,true);assert.equal(config.entity_id,setup.entity_id);record.entity_id=config.entity_id;c.entity_id=config.entity_id;
   await work({c,page,context,config,root,ready,read,stage,click,field,input,layouts,observe,prepare,preparedLink,openPrepared,edit,actualFile,original,lostConfirmation,viewportProof,checkpoint,clickReload,retainRoute,setPhase:p=>phase=p});
   if(c.status==='RUNNING')c.status=mode==='baseline'?'BASELINE_RECORDED':'PASS';c.stage='complete';
  }catch(error){c.status='FAIL';c.failure={stage:c.stage,message:redactPilot(error.message),stack:redactPilot(error.stack||'')};if(!page.isClosed()&&!page.url().includes('/pilot/'))await page.screenshot({path:path.join(out,`${engine}-${id}-failure.png`),fullPage:true,timeout:10000}).catch(()=>{});}
  finally{const ops=c.operations.filter(o=>o.phase!=='setup');c.metrics={trusted_clicks:ops.filter(o=>o.events).length,capture_clicks:ops.filter(o=>o.events&&o.phase==='capture').length,review_clicks:ops.filter(o=>o.events&&o.phase==='review').length,destination_clicks:ops.filter(o=>o.events&&o.phase==='destination').length,repeated_known_facts:ops.filter(o=>o.kind==='field_edit'&&o.origin==='source_fact').length,new_required_facts:ops.filter(o=>o.kind==='field_edit'&&o.origin==='new_required_fact').length,review_acknowledgements:ops.filter(o=>o.kind==='review_acknowledgement').length,native_selects:ops.filter(o=>o.operation==='selectOption').length,text_fills:ops.filter(o=>o.operation==='fill').length,initial_capture_entries:ops.filter(o=>o.kind==='initial_capture_input').length,file_selections:ops.filter(o=>o.kind==='file_selection').length};// Preserve the primary manifest and original failure before optional observations.
   await write();if(diagnostic){try{const captured=await boundedDiagnosticCapture(diagnostic);const filename=`${engine}-${id}-diagnostics.json`;await fs.writeFile(path.join(out,filename),JSON.stringify({sha,mode,engine,case_id:id,status:c.status,stage:c.stage,...captured},null,2)+'\n');c.diagnostic_artifact=filename;}catch{c.diagnostic_capture_failed=true;}}
   for(const cleanup of retainedCleanups.reverse())await cleanup().catch(()=>{c.retained_route_cleanup_failed=true;if(c.status!=='FAIL'){c.status='FAIL';c.failure={stage:'retained_route_cleanup',message:'Owned retained route cleanup failed'};}});
   await context.close().catch(()=>{});await write();}
  return c;
 };
 const paid=await run('T01',async h=>{
  const {c,page,root,ready,read,stage,click,input,layouts,observe,prepare,openPrepared,edit,original,lostConfirmation,setPhase}=h,sourceText=fixture('T01').input;
  await stage('source_capture_and_lost_save');c.before_capture=await observe('before');await click(page.locator('[data-testid=home-expense]'),'Open fast expense');await ready('capture/expense');await input(page.locator('[name=text]'),sourceText,{key:'original',initial:true});
  await page.locator('[data-fertilizer-draft-status][data-state=saved]').waitFor();await page.reload();await ready('capture/expense');assert.equal(await page.locator('[name=text]').inputValue(),sourceText);c.draft_reload_preserved=true;
  let lost=false,receiptLookups=0;const writes=[];page.on('request',r=>{if(r.url().endsWith('/pilot/receipts'))receiptLookups++;});await page.route('**/owner/captures',async route=>{if(route.request().method()==='POST'){writes.push({client_capture_id:route.request().postDataJSON().client_capture_id,key:route.request().headers()['idempotency-key']});if(!lost){const response=await route.fetch();assert.equal(response.status(),201);lost=true;await route.abort('failed');return;}}await route.continue();});
  await click(page.locator('[data-form=capture] button[type=submit]'),'Save immutable text');await page.locator('.form-error').filter({hasText:'Ответ сервера не получен'}).waitFor();await click(page.locator('[data-form=capture] button[type=submit]'),'Retry source save','recovery_click');await ready('source/');await page.unroute('**/owner/captures');c.source_id=page.url().split('#source/')[1];c.lost_source_response=true;c.receipt_lookups=receiptLookups;assert.ok(receiptLookups);assert.equal(new Set(writes.map(x=>x.client_capture_id)).size,1);c.client_capture_id=writes[0].client_capture_id;
  setPhase('review');const source=await prepare(c.source_id);assert.equal(source.text,sourceText);assert.equal(source.proposals.length,1);await openPrepared(source);let p=await read(root+'/owner/confirmations/'+source.proposals[0].id+'/preview');c.initial_proposal={id:p.id,type:p.action_type,payload:p.revision.payload,provenance:p.revision.provenance,missing:p.revision.missing};
  if(p.action_type==='UNRESOLVED'&&mode==='baseline'){c.barriers.push({kind:'unresolved_paid_phrase',payload:p.revision.payload});const originHash=page.url().split('#')[1];await click(page.locator('[data-action^="recover:"]'),'Use existing explicit paid-expense recovery');await page.waitForURL(url=>url.hash.startsWith('#review/')&&url.hash!=='#'+originHash);await ready('review/');p=await read(root+'/owner/confirmations/'+page.url().split('#review/')[1]+'/preview');}
  assert.equal(p.action_type,'DIRECT_EXPENSE');c.proposal_id=p.id;
  if(mode==='candidate'){eqDecimal(p.revision.payload.amount,'650');assert.equal(p.revision.payload.currency,'TRY');assert.match(p.revision.payload.description,/топлив/i);assert.equal(p.revision.payload.money_account_id,setup.cash_id);assert.equal(p.revision.payload.counterparty_id,null);}
  else for(const [key,value] of [['amount','650'],['currency','TRY'],['description','топливо'],['money_account_id',setup.cash_id],['event_date','2026-10-12']])if(p.revision.payload[key]==null){await edit(key,value,key==='event_date'?'review':'source_fact');p=await read(root+'/owner/confirmations/'+c.proposal_id+'/preview');}
  if(p.revision.missing.some(x=>/факт оплаты/i.test(x))){await edit('payment_purpose','expense','review');p=await read(root+'/owner/confirmations/'+c.proposal_id+'/preview');}
  assert.deepEqual(p.revision.missing,[]);c.before_confirm=await observe('prepared');assert.deepEqual(c.before_confirm,c.before_capture);await layouts('paid-known-facts');await stage('confirm_paid_expense');
  await lostConfirmation(c.proposal_id,page.locator('[data-action=confirm-proposal]'),()=>page.getByText('Сервер подтвердил действие. Оригинал и версии сохранены.',{exact:true}).waitFor());
  p=await read(root+'/owner/confirmations/'+c.proposal_id+'/preview');assert.equal(p.state,'POSTED');c.direct_expense_id=p.confirmed_effect.effect.id;await original(c.source_id,sourceText);setPhase('destination');await click(page.getByRole('link',{name:'Финансы',exact:true}),'Open financial destination');await ready('finance');await layouts('expense-destination');
 });
 // The separately seeded repair debt exists before the SQL baseline. Run
 // settlement before T04 creates its distinct, later ambiguous-money payable.
 await run('T03',async h=>{
  const {c,page,root,ready,read,stage,click,input,layouts,observe,prepare,openPrepared,edit,original,lostConfirmation,setPhase}=h,text=fixture('T03').input;
  await stage('capture_existing_debt_payment');c.before_capture=await observe('before');const debts=c.before_capture.tables['finance.obligation_balances'].filter(x=>x.counterparty_id===setup.serkan_id&&x.direction==='payable'&&x.currency==='TRY'&&!/^0(?:\.0+)?$/.test(x.remaining_amount));assert.equal(debts.length,1);assert.equal(debts[0].obligation_id,setup.t03_obligation_id);eqDecimal(debts[0].remaining_amount,'5000');
  await click(page.locator('[data-testid=radial-toggle]'),'Open Capture');await click(page.locator('[data-testid=capture-text]'),'Choose payment text');await ready('capture/text');await input(page.locator('[name=text]'),text,{key:'original',initial:true});await click(page.locator('[data-form=capture] button[type=submit]'),'Save payment original');await ready('source/');c.source_id=page.url().split('#source/')[1];const source=await prepare(c.source_id);assert.equal(source.text,text);assert.equal(source.proposals.length,1);await openPrepared(source);setPhase('review');let p=await read(root+'/owner/confirmations/'+source.proposals[0].id+'/preview');c.proposal_id=p.id;c.initial_proposal={type:p.action_type,payload:p.revision.payload,provenance:p.revision.provenance,missing:p.revision.missing};assert.equal(p.action_type,'PAYMENT');eqDecimal(p.revision.payload.amount,'2000');assert.equal(p.revision.payload.currency,'TRY');assert.equal(p.revision.payload.counterparty_id,setup.serkan_id);assert.equal(p.revision.payload.payment_purpose,'debt');assert.match(p.revision.payload.description,/ремонт/);assert.equal(p.revision.payload.money_account_id,null);assert.equal(p.revision.payload.obligation_id,null);
  await stage('explicit_account_and_existing_payable');await edit('money_account_id',setup.cash_id,'new_required_fact');await edit('obligation_id',setup.t03_obligation_id,'new_required_fact');p=await read(root+'/owner/confirmations/'+c.proposal_id+'/preview');assert.deepEqual(p.revision.missing,[]);assert.equal(p.revision.payload.obligation_id,setup.t03_obligation_id);eqDecimal(p.revision.payload.amount,'2000');assert.equal(p.revision.payload.counterparty_id,setup.serkan_id);assert.equal(p.revision.payload.payment_purpose,'debt');c.obligation_id=setup.t03_obligation_id;c.before_confirm=await observe('prepared');assert.deepEqual(c.before_confirm,c.before_capture);
  if(mode==='candidate'){const shown=await page.locator('#main').innerText();assert.match(shown,/Серкан/);assert.match(shown,/Остаток после этой оплаты/);assert.match(shown,/3[\s\u00a0\u202f]?000/);}
  await layouts('payment-known-facts');await stage('confirm_existing_debt_payment');await lostConfirmation(c.proposal_id,page.locator('[data-action=confirm-proposal]'),()=>page.getByText('Сервер подтвердил действие. Оригинал и версии сохранены.',{exact:true}).waitFor());p=await read(root+'/owner/confirmations/'+c.proposal_id+'/preview');assert.equal(p.state,'POSTED');c.payment_id=p.confirmed_effect.effect.id;await original(c.source_id,text);await page.reload();await ready('review/');const after=c.after_retry.tables['finance.obligation_balances'].find(x=>x.obligation_id===setup.t03_obligation_id);eqDecimal(after.remaining_amount,'3000');await layouts('payment-confirmed');
 });
 await run('T04',async h=>{
  const {c,page,root,ready,read,stage,click,input,field,layouts,observe,prepare,openPrepared,original,setPhase}=h,text=fixture('T04').input;c.before_capture=await observe('before');await click(page.locator('[data-testid=radial-toggle]'),'Open Capture');await click(page.locator('[data-testid=capture-text]'),'Choose text');await ready('capture/text');await input(page.locator('[name=text]'),text,{key:'original',initial:true});await click(page.locator('[data-form=capture] button[type=submit]'),'Save ambiguous original');await ready('source/');c.source_id=page.url().split('#source/')[1];const source=await prepare(c.source_id);assert.equal(source.text,text);await openPrepared(source);setPhase('review');let p=await read(root+'/owner/confirmations/'+source.proposals[0].id+'/preview');c.origin_proposal_id=p.id;c.initial_proposal={type:p.action_type,payload:p.revision.payload,missing:p.revision.missing};assert.equal(p.action_type,'UNRESOLVED');await stage('explicit_intent_only');
  if(mode==='baseline'){assert.equal(await page.locator('[data-form=money-intent]').count(),0);c.barriers.push({kind:'no_supported_ambiguous_money_intent_control',observed_payload:p.revision.payload});await layouts('baseline-ambiguous-block');c.no_effect_snapshot=await observe('unresolved');assert.deepEqual(c.no_effect_snapshot,c.before_capture);await original(c.source_id,text);return;}
  const f=p.revision.payload.money_candidates;assert.ok(f);eqDecimal(f.amount,'5000');assert.equal(f.currency,'TRY');assert.equal(f.counterparty_id,setup.serkan_id);assert.match(f.description,/ремонт.*насос/i);assert.ok(!f.payment_purpose&&!f.money_account_id);assert.match(await page.locator('#main').innerText(),/Не установлен из источника/);await layouts('ambiguous-known-facts');
  await field('intent','operating_payable','new_required_fact');await click(page.locator('[data-form=money-intent] button[type=submit]'),'Choose unpaid operating payable');await page.waitForURL(url=>url.hash.startsWith('#review/')&&url.hash!=='#review/'+c.origin_proposal_id);await ready('review/');c.proposal_id=page.url().split('#review/')[1];assert.notEqual(c.proposal_id,c.origin_proposal_id);p=await read(root+'/owner/confirmations/'+c.proposal_id+'/preview');assert.equal(p.action_type,'PAYABLE');eqDecimal(p.revision.payload.amount,'5000');assert.equal(p.revision.payload.counterparty_id,setup.serkan_id);assert.deepEqual(p.revision.missing,[]);c.before_confirm=await observe('chosen-no-effect');assert.deepEqual(c.before_confirm,c.before_capture);await click(page.locator('[data-action=confirm-proposal]'),'Confirm reviewed unpaid payable','effect_confirmation');await page.getByText('Сервер подтвердил действие. Оригинал и версии сохранены.',{exact:true}).waitFor();p=await read(root+'/owner/confirmations/'+c.proposal_id+'/preview');c.obligation_id=p.confirmed_effect.effect.id;assert.equal(p.state,'POSTED');await page.reload();await ready('review/');await original(c.source_id,text);c.explicit_intent='operating_payable';await layouts('unpaid-confirmed');
 });
 await run('P01',async h=>{
  const {c,page,root,ready,read,stage,click,layouts,observe,prepare,preparedLink,actualFile,original,setPhase}=h;c.before_capture=await observe('before');await click(page.locator('[data-testid=radial-toggle]'),'Open Capture');await click(page.locator('[data-testid=capture-photo]'),'Choose document');await ready('capture/photo');const bytes=await actualFile('P01');assert.equal(await page.locator('[name=text]').inputValue(),'');await click(page.locator('[data-form=capture] button[type=submit]'),'Save standalone invoice original');await ready('source/');c.source_id=page.url().split('#source/')[1];setPhase('review');await stage('real_ocr_without_arrival');const source=await prepare(c.source_id);assert.equal(source.text,null);c.proposals=[];for(const p of source.proposals)c.proposals.push(await read(root+'/owner/confirmations/'+p.id+'/preview'));
  if(mode==='candidate'){assert.equal(source.recognition.provider,'local');assert.equal(source.recognition.engine_version,'local-tesseract-tur-eng-v1');assert.ok(source.recognition.pages[0].lines.length);assert.deepEqual(c.proposals.map(x=>x.action_type).sort(),['INVOICE_MATCH','PAYABLE']);const p=c.proposals.find(x=>x.action_type==='PAYABLE');eqDecimal(p.revision.payload.amount,'500');assert.equal(p.revision.payload.counterparty_id,setup.supplier_id);assert.equal(p.revision.payload.event_date,'2026-10-12');assert.equal(c.proposals.find(x=>x.action_type==='INVOICE_MATCH').revision.payload.receipt_id,null);assert.equal(p.revision.payload.invoice_intent,'undecided');assert.ok(p.revision.missing.length,'Standalone invoice needs explicit economic intent');const link=await preparedLink(source,p.id);await click(link,'Inspect known standalone invoice facts');await ready('review/');const shown=await page.locator('#main').innerText();assert.match(shown,/500/);assert.match(shown,/SYN Gübre Tedarik/);assert.match(shown,/KNO3/);assert.equal(await page.locator('[data-action=confirm-proposal]:not(:disabled)').count(),0);await page.getByRole('link',{name:'Выбрать приход и назначение',exact:true}).waitFor();}
  else c.barriers.push({kind:'baseline_standalone_invoice_observation',actual_types:c.proposals.map(p=>p.action_type),actual_payloads:c.proposals.map(p=>p.revision.payload)});
  c.no_effect_snapshot=await observe('prepared-inert');assert.deepEqual(c.no_effect_snapshot,c.before_capture);await original(c.source_id,bytes);await layouts('standalone-invoice-inert');
 });
 await run('P02',async h=>{
  const {c,page,root,ready,read,stage,click,field,input,layouts,observe,prepare,edit,actualFile,original,lostConfirmation,setPhase}=h;
  setPhase('setup');await stage('independent_physical_arrival');await click(page.locator('[data-testid=nav-more]'),'Open inventory');await ready('more');await click(page.getByRole('link',{name:'Склад',exact:true}),'Open stock');await ready('inventory');await click(page.getByRole('link',{name:'Товар привезли',exact:true}),'Record separate physical arrival');await ready('quick/receipt');await field('product',setup.product_id,'setup');await field('location',setup.location_id,'setup');await field('quantity','100','setup');await field('date','2026-10-12','setup');await click(page.locator('[data-form=receipt] button[type=submit]'),'Review independently observed arrival');await ready('receipt/');c.receipt_id=page.url().split('#receipt/')[1];await click(page.locator('[data-action=post-receipt]'),'Confirm one physical arrival');await page.getByText('Сервер подтвердил приход. Физическое движение создано один раз.',{exact:true}).waitFor();const receipt=await read(root+'/inventory/events/'+c.receipt_id);assert.equal(receipt.movements.length,1);assert.equal(receipt.payload.lines[0].unit_cost,null);c.receipt_original=receipt.evidence.structured_original;c.before_capture=await observe('physical-before-invoice');setPhase('capture');
  await click(page.getByRole('link',{name:'История поставки',exact:true}),'Open purchase history');await ready('purchase/');await click(page.getByRole('link',{name:'Добавить счёт к приходу',exact:true}),'Add later invoice');await ready('invoice-new/');const bytes=await actualFile('P02');assert.equal(await page.locator('[name=text]').inputValue(),'');
  if(mode==='baseline'){assert.ok(await page.locator('[name=intent]').count());await field('intent','operating_expense','new_required_fact');c.barriers.push({kind:'accounting_intent_required_before_original_save'});}else assert.equal(await page.locator('[name=intent]').count(),0);
  await click(page.locator('[data-form=invoice-source] button[type=submit]'),'Save actual invoice file');await ready('invoice-source/');c.source_id=page.url().split('#invoice-source/')[1].split('/')[1];setPhase('review');const source=await prepare(c.source_id);assert.equal(source.text,null);assert.equal(source.invoice_receipt_id,c.receipt_id);const payableRef=source.proposals.find(p=>p.action_type==='PAYABLE'),matchRef=source.proposals.find(p=>p.action_type==='INVOICE_MATCH');assert.ok(payableRef&&matchRef);c.proposal_id=payableRef.id;c.match_proposal_id=matchRef.id;let p=await read(root+'/owner/confirmations/'+c.proposal_id+'/preview');c.initial_proposal={payload:p.revision.payload,provenance:p.revision.provenance,missing:p.revision.missing};
  if(mode==='candidate'){
   assert.equal(source.recognition.provider,'local');assert.equal(source.recognition.engine_version,'local-tesseract-tur-eng-v1');assert.equal(source.recognition.pages.length,1);assert.ok(source.recognition.pages[0].lines.length);eqDecimal(p.revision.payload.amount,'500');assert.equal(p.revision.payload.currency,'TRY');assert.equal(p.revision.payload.counterparty_id,setup.supplier_id);assert.equal(p.revision.payload.event_date,'2026-10-12');assert.match(p.revision.payload.description,/KNO3/i);assert.equal(source.invoice_intent,null);assert.ok(p.revision.missing.some(x=>/расход|назначение|долг/.test(x)));
   await page.getByRole('link',{name:'Выбрать приход и назначение',exact:true}).waitFor({timeout:20000});await click(page.getByRole('link',{name:'Выбрать приход и назначение',exact:true}),'Choose economic intent explicitly');await ready('invoice-select/');assert.equal(await page.locator('[name=receipt]').inputValue(),c.receipt_id);await field('intent','operating_expense','new_required_fact');await click(page.locator('[data-form=invoice-choice] button[type=submit]'),'Review separate operating payable and receipt');await ready('invoice-source/');
  }else{
   // Existing manual source path is measured, not credited as OCR preparation.
   if(!await page.locator('[data-action="invoice-edit:amount"]').count()){await click(page.locator('[data-action=refresh]'),'Refresh prepared invoice');}
   for(const [key,value] of [['amount','500'],['currency','TRY'],['event_date','2026-10-12'],['counterparty_id',setup.supplier_id],['description','FATURA KNO3 100 kg']])await edit(key,value,'source_fact',true);
   c.barriers.push({kind:'manual_invoice_transcription',first_payload:c.initial_proposal.payload});
  }
  if(await page.locator('[data-action=invoice-review-document]').count()){await click(page.locator('[data-action=invoice-review-document]'),'Review original recognition warnings','review_acknowledgement');await page.locator('[data-action=invoice-review-document]').waitFor({state:'hidden'});}
  p=await read(root+'/owner/confirmations/'+c.proposal_id+'/preview');assert.deepEqual(p.revision.missing,[]);c.before_confirm=await observe('invoice-prepared-inert');assert.deepEqual(c.before_confirm,c.before_capture);await layouts('late-invoice-known-facts');await stage('confirm_payable_then_explicit_link');
  await lostConfirmation(c.proposal_id,page.locator('[data-action=invoice-confirm-payable]'),()=>page.getByText('Расход и долг подтверждены сервером. Повтор не создаёт новый документ.',{exact:true}).waitFor());p=await read(root+'/owner/confirmations/'+c.proposal_id+'/preview');c.obligation_id=p.confirmed_effect.effect.id;
  assert.equal(await page.locator('[name=obligation]').inputValue(),c.obligation_id);await click(page.locator('[data-form=invoice-link] button[type=submit]'),'Review exact invoice/receipt/payable link','review_acknowledgement');await page.locator('[data-action=invoice-confirm-link]').waitFor();await click(page.locator('[data-action=invoice-confirm-link]'),'Confirm only reviewed link','effect_confirmation');await page.getByText('Связь подтверждена. Повторного прихода, расхода и оплаты нет.',{exact:true}).waitFor();await page.reload();await ready('invoice-source/');const match=await read(root+'/owner/confirmations/'+c.match_proposal_id+'/preview');assert.equal(match.state,'CONFIRMED_NONPOSTING');c.invoice_link_id=match.confirmed_effect.effect.id;await original(c.source_id,bytes);
  setPhase('destination');await click(page.getByText('Оригинал счёта',{exact:true}),'Show invoice original');const pending=page.waitForEvent('download');await click(page.getByRole('link',{name:'P02.pdf',exact:true}),'Download immutable PDF');const download=await pending;const location=path.join(out,`${engine}-P02-clicked-original.pdf`);await download.saveAs(location);c.clicked_original_sha256=digest(await fs.readFile(location));assert.equal(c.clicked_original_sha256,c.source_sha256);
  await click(page.getByRole('link',{name:'К истории поставки',exact:true}),'Inspect existing purchase');await ready('purchase/');const purchase=await read(root+'/purchases/'+c.receipt_id);assert.equal(purchase.movements.length,1);assert.equal(purchase.lines[0].unit_cost,null);assert.equal(purchase.invoice_links.length,1);assert.equal(purchase.linked_documents.length,1);eqDecimal(purchase.linked_documents[0].remaining_amount,'500');c.purchase= {physical_movements:1,invoice_links:1,cost:null,remaining_payable:'500.00'};await layouts('late-invoice-purchase-destination');
 });
 await run('SECURITY',async h=>{
  const {c,page,context,config,root,ready,read,stage,click,input,observe,retainRoute}=h;const text=fixture('T01').input;c.before=await observe('before');await stage('actual_draft_storage_failure');await click(page.locator('[data-testid=home-expense]'),'Open private capture draft');await ready('capture/expense');
  await page.evaluate(async()=>{const {BrowserStore}=await import('/r3/connected-adapter.js');const original=BrowserStore.prototype.put;window.__restoreCaptureStore=()=>{BrowserStore.prototype.put=original;delete window.__restoreCaptureStore;};BrowserStore.prototype.put=async function(){throw new DOMException('TEST injected local quota','QuotaExceededError');};});
  await input(page.locator('[name=text]'),text,{key:'security-draft',initial:true});await page.locator('[data-fertilizer-draft-status][data-state=error]').waitFor();assert.equal(await page.locator('[name=text]').inputValue(),text);assert.equal(await page.locator('[name=text]').isEditable(),true);c.storage_failure_visible=true;
  await page.evaluate(()=>window.__restoreCaptureStore());await page.locator('[name=text]').fill(text+' ');await page.locator('[name=text]').fill(text);await page.locator('[data-fertilizer-draft-status][data-state=saved]').waitFor();const draft=await readOwnerDrafts(page,base,config,'capture/expense',sha);assert.equal(draft.drafts.length,1);c.client_capture_id=draft.drafts[0].value.id;assert.equal(draft.drafts[0].value.text,text);assert.equal(draft.raw.includes(text),false);c.encrypted_draft=true;
  // The real adapter checks /pilot/session before a write; expiry must stop the capture POST entirely.
  await page.reload();await ready('capture/expense');assert.equal(await page.locator('[name=text]').inputValue(),text);await stage('server_expired_owner_session');const expired=await expiredCookie(config.participant_id);await context.addCookies([{...expired,url:base}]);assert.equal((await context.request.get(base+'/pilot/session')).status(),401);const expiredWrites=[];const observeExpiredWrite=r=>{if(r.method()==='POST'&&r.url().endsWith('/owner/captures'))expiredWrites.push(r.url());};page.on('request',observeExpiredWrite);const denied=page.waitForResponse(r=>r.request().method()==='GET'&&r.url().endsWith('/pilot/session'));await click(page.locator('[data-form=capture] button[type=submit]'),'Attempt save after real expiry');assert.equal((await denied).status(),401);await page.waitForURL('**/pilot/**');page.off('request',observeExpiredWrite);assert.deepEqual(expiredWrites,[],'Expired session must block capture POST before dispatch');c.expiry_preflight={method:'GET',path:'/pilot/session',status:401,capture_posts:expiredWrites.length};await ownerLogin(page,base,{navigate:false});await page.goto(base+'/r3/connected.html#capture/expense');await ready('capture/expense');assert.equal(await page.locator('[name=text]').inputValue(),text);c.expired_session_retained_draft=true;
  await stage('late_real_read_preserves_new_edit');await page.goto(base+'/r3/connected.html#home');await ready('home');const editFault=await retainRoute('**/owner/home',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'TEST transport failure'})}),'before-held-edit');await page.reload();await ready('home');assert.equal(editFault.first_calls,1);assert.deepEqual(editFault.observed_statuses,[503]);let releaseEdit,enteredEdit;const editGate=new Promise(resolve=>releaseEdit=resolve),editStarted=new Promise(resolve=>enteredEdit=resolve);await retainRoute('**/owner/home',async route=>{const response=await route.fetch();enteredEdit();await editGate;await route.fulfill({response});},'held-edit');await click(page.getByRole('button',{name:/^Повторить загрузку/}),'Retry held real read before editing');await Promise.race([editStarted,new Promise((_,reject)=>setTimeout(()=>reject(Error('Held edit read never started')),15000))]);await click(page.locator('[data-testid=radial-toggle]'),'Open a new capture while old read waits');await click(page.locator('[data-testid=capture-text]'),'Enter new text capture');await ready('capture/text');const newEdit=fixture('T04').input;await page.locator('[name=text]').fill(newEdit);await page.locator('[data-fertilizer-draft-status][data-state=saved]').waitFor();const lateEditReply=page.waitForResponse(r=>r.url().endsWith('/owner/home')&&r.status()===200);releaseEdit();await lateEditReply;await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));assert.ok(page.url().endsWith('#capture/text'));assert.equal(await page.locator('[name=text]').inputValue(),newEdit);c.delayed_read_did_not_overwrite_edit=true;
  await stage('late_real_read_after_logout');await page.goto(base+'/r3/connected.html#home');await ready('home');const logoutFault=await retainRoute('**/owner/home',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'TEST transport failure'})}),'before-held-logout');await page.reload();await ready('home');assert.equal(logoutFault.first_calls,1);assert.deepEqual(logoutFault.observed_statuses,[503]);let release,entered;const gate=new Promise(resolve=>release=resolve),started=new Promise(resolve=>entered=resolve);await retainRoute('**/owner/home',async route=>{const response=await route.fetch();entered();await gate;try{await route.fulfill({response});}catch(e){if(!/closed|cancel|disposed|already handled/i.test(e.message))throw e;}},'held-logout');await click(page.getByRole('button',{name:/^Повторить загрузку/}),'Retry actual held owner read');await Promise.race([started,new Promise((_,reject)=>setTimeout(()=>reject(Error('Held read never started')),15000))]);await click(page.locator('[data-testid=nav-more]'),'Keep logout reachable');await ready('more');const logout=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/pilot/logout'));await click(page.getByRole('button',{name:'Выйти',exact:true}),'Logout during held response');assert.equal((await logout).status(),200);release();await page.waitForURL('**/pilot/');assert.equal((await context.request.get(base+'/pilot/session')).status(),401);assert.equal(await page.locator('[data-testid=home-expense]').count(),0);c.late_response_did_not_restore_owner_ui=true;
  await workerLogin(page,base,credentials[engine].worker);assert.equal(await page.locator('[data-testid=home-expense]').count(),0);if(paid.source_id)assert.ok([403,404].includes((await context.request.get(base+root+'/owner/captures/'+paid.source_id)).status()));assert.equal((await page.locator('body').innerText()).includes(text),false);c.worker_cannot_read_owner_source=true;c.after=await observe('after');assert.deepEqual(c.after,c.before,'Capture security faults created business effects');
 });
 // Fixed diagnostic matrix. Fresh context per arm, two observed predecessor
 // positions per transport; an unexpected recovery/failure stops that arm.
 // Original SECURITY actions, waits and assertions above remain unchanged.
 for(const arm of bootstrapArms){
  await resetFixtureRateLimits(); // Existing offline disposable-TEST fixture only.
  await run(arm,async h=>{
   const {c,page,context,config,root,ready,read,stage,click,observe,checkpoint,clickReload,retainRoute}=h;
   assert.ok(c.diagnostics_available,'Matrix requires metadata diagnostics');
   const began=Date.now(),pattern='**/owner/home',text=fixture('T01').input;
   c.business_writes=[];c.route_events=[];c.trials=[];
   const note=(event,extra={})=>{assert.ok(c.route_events.length<100,'Bounded route evidence overflow');c.route_events.push({event,atMs:Date.now()-began,...extra});};
   const writes=request=>{const method=request.method(),pathname=new URL(request.url()).pathname;if(pathname.startsWith('/v1/')&&!['GET','HEAD','OPTIONS'].includes(method))c.business_writes.push({method,path:safePath(request.url())});};
   page.on('request',writes);
   const freezeFailure=async error=>{
    if(c.first_failure)return;c.first_failure={stage:c.stage,message:redactPilot(error.message)};await write();
    try{await checkpoint('first-failure');}catch{c.first_failure_diagnostic_failed=true;}
    if(!page.isClosed()&&!page.url().includes('/pilot/'))await page.screenshot({path:path.join(out,`${engine}-${arm}-first-failure.png`),fullPage:false,timeout:10000}).catch(()=>{});
   };
   const bounded=async(promise,label,ms=15000)=>{let timer;try{return await Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error(label)),ms);})]);}finally{clearTimeout(timer);}};
   const draftAt=async(route,expected)=>{const saved=await readOwnerDrafts(page,base,config,route,sha);assert.equal(saved.drafts.length,1,'One owner-scoped encrypted draft required');assert.ok(saved.drafts[0].value.text===expected,'Owner draft content changed');assert.ok(!saved.raw.includes(expected),'Draft plaintext persisted');return saved.drafts[0];};
   const sameDraft=async(route,expected,prior)=>{const current=await draftAt(route,expected);assert.ok(JSON.stringify(current)===JSON.stringify(prior),'Encrypted draft UUID, content or authenticated owner scope changed');return current;};
   // Capture response and body-terminal timing without adding readiness waits.
   const readEvents=new WeakMap();let readOrdinal=0;
   const onRequest=request=>{if(new URL(request.url()).pathname===root+'/owner/home'){const id=++readOrdinal;readEvents.set(request,id);note('request',{id});}};
   const onResponse=response=>{const id=readEvents.get(response.request());if(id)note('response',{id,status:response.status()});};
   const onFinished=request=>{const id=readEvents.get(request);if(id)note('requestfinished',{id});};
   const onFailed=request=>{const id=readEvents.get(request);if(id)note('requestfailed',{id});};
   page.on('request',onRequest);page.on('response',onResponse);page.on('requestfinished',onFinished);page.on('requestfailed',onFailed);
   const coldReload=async(transport,position)=>{
    const trial={transport,position,status:'RUNNING',atMs:Date.now()-began,interception_at_start:c.retained_routes?.length?'retained_predecessor':'none'};c.trials.push(trial);await stage(position+'_'+transport);
    let entered=0,fulfilled=0,handlerFailure=false;
    const handler=async route=>{entered++;note('handler_entered',{transport,position});try{
     if(['single_503_retained','persistent_503'].includes(transport)&&entered===1)await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'TEST transport failure'})});
     else {const response=await route.fetch({timeout:25000});assert.equal(response.status(),200,'Pass-through must use the actual server 200');note('handler_fetched',{transport,position,status:response.status()});await route.fulfill({response});}
     fulfilled++;note('handler_fulfilled',{transport,position});
    }catch{handlerFailure=true;note('handler_failed',{transport,position});await route.abort('failed').catch(()=>{});}};
    let cleanup;trial.route={};if(transport!=='normal_200')cleanup=await installRetainedReadRoute(page,pattern,handler,trial.route);
    try{
     await page.reload();await ready('home');
     assert.equal(await page.locator('#app').getAttribute('data-bootstrap-stage'),'render-returned');
     assert.equal(handlerFailure,false,'Intercepted read failed');assert.equal(entered,transport==='normal_200'?0:1);assert.equal(fulfilled,entered);
     trial.status='PASS';trial.elapsed_ms=Date.now()-began-trial.atMs;trial.handler_entered=entered;trial.handler_fulfilled=fulfilled;trial.injected_503_count=['single_503_retained','persistent_503'].includes(transport)?1:0;
     if(cleanup){assert.equal(trial.route.first_calls,1);assert.deepEqual(trial.route.observed_statuses,[trial.injected_503_count?503:200]);}
     assert.ok(await checkpoint(position.replaceAll('_','-')+'-'+transport.replaceAll('_','-')),'Missing per-iteration diagnostics');
    }catch(error){trial.status='FAIL';trial.elapsed_ms=Date.now()-began-trial.atMs;await freezeFailure(error);throw error;}
    finally{if(cleanup)await cleanup().catch(()=>{c.route_cleanup_failed=true;});}
   };
   c.boundary_before=await observe('matrix-before',true);let primaryError;
   try{
    await stage('save_encrypted_draft');await click(page.locator('[data-testid=home-expense]'),'Open matrix draft');await ready('capture/expense');
    await page.locator('[name=text]').fill(text);await page.locator('[data-fertilizer-draft-status][data-state=saved]').waitFor();
    const originalDraft=await draftAt('capture/expense',text);c.client_capture_id=originalDraft.value.id;c.encrypted_draft=true;
    if(arm==='BOOTSTRAP_TIMEOUT'){
     await stage('deliberate_bootstrap_timeout');
     let release,enter,heldRequest;const gate=new Promise(resolve=>release=resolve),entered=new Promise(resolve=>enter=resolve);
     const matcher=url=>url.pathname===root+'/owner/captures';
     const handler=async route=>{heldRequest=route.request();note('timeout_handler_entered');enter();await gate;try{await route.continue();note('timeout_handler_released');}catch{note('timeout_handler_release_failed');}};
     await retainRoute(matcher,handler,'held-bootstrap-deadline');
     try{
      const reloadStarted=Date.now();await page.reload();await bounded(entered,'Bootstrap read was not intercepted');
      // Existing application deadline remains 15 seconds. This observation has
      // the original 25-second readiness budget; it does not change that clock.
      const retry=page.getByRole('button',{name:'Повторить загрузку страницы',exact:true});await retry.waitFor({timeout:25000});
      assert.equal(await page.locator('#app').getAttribute('data-bootstrap-stage'),'recovery');assert.equal(await page.locator('form,input,textarea,select,.dock').count(),0);assert.ok(page.url().endsWith('#capture/expense'));
      await page.getByText('Загрузка заняла слишком много времени. Повторите загрузку страницы.',{exact:true}).waitFor();
      c.controlled_timeout_elapsed_ms=Date.now()-reloadStarted;assert.ok(c.controlled_timeout_elapsed_ms>=14500,'Expected the existing 15-second deadline, not an earlier initialization failure');c.controlled_recovery_visible=true;
      const image=`${engine}-${arm}-390-timeout-recovery.png`;await page.screenshot({path:path.join(out,image),fullPage:false});result.screenshots.push(image);
      assert.ok(await checkpoint('timeout-visible'),'Missing timeout diagnostics');
      const terminal=observeDiscardedReadTerminal(page,heldRequest);
      try{release();c.late_read=await bounded(terminal.promise,'Released bootstrap read never reached terminal observation',25000);}finally{terminal.dispose();}
      assert.equal(await page.locator('#app').getAttribute('data-bootstrap-stage'),'recovery');assert.equal(await page.locator('form,input,textarea,select,.dock').count(),0);
      await sameDraft('capture/expense',text,originalDraft);c.late_response_fenced=true;
      await stage('visible_manual_reload');await clickReload(retry,'Use visible cold-bootstrap recovery');await ready('capture/expense');
      assert.ok(await page.locator('[name=text]').inputValue()===text,'Manual recovery lost draft content');await sameDraft('capture/expense',text,originalDraft);
      const authenticated=await read('/pilot/session');assert.equal(authenticated.role,'owner');assert.equal(authenticated.participant_id,config.participant_id);
      const recoveredImage=`${engine}-${arm}-390-manual-recovery.png`;await page.screenshot({path:path.join(out,recoveredImage),fullPage:false});result.screenshots.push(recoveredImage);
      c.same_draft_after_recovery=true;c.authenticated_recovery=true;assert.ok(await checkpoint('manual-recovery-complete'),'Missing recovery diagnostics');
     }catch(error){await freezeFailure(error);throw error;}finally{release();}
    }else{
     // Repeat the actual expiry/re-login predecessor without submitting a
     // business write: the real preflight 401 must stop that request entirely.
     await page.reload();await ready('capture/expense');await stage('matrix_expired_session');
     const expired=await expiredCookie(config.participant_id);await context.addCookies([{...expired,url:base}]);assert.equal((await context.request.get(base+'/pilot/session')).status(),401);
     const denied=page.waitForResponse(r=>r.request().method()==='GET'&&r.url().endsWith('/pilot/session'));await click(page.locator('[data-form=capture] button[type=submit]'),'Attempt matrix save after actual expiry');assert.equal((await denied).status(),401);await page.waitForURL('**/pilot/**');
     await ownerLogin(page,base,{navigate:false});await page.goto(base+'/r3/connected.html#capture/expense');await ready('capture/expense');await sameDraft('capture/expense',text,originalDraft);c.same_draft_after_relogin=true;
     await page.goto(base+'/r3/connected.html#home');await ready('home');
     const transport={BOOTSTRAP_NORMAL:'normal_200',BOOTSTRAP_ROUTED:'routed_real_200',BOOTSTRAP_503:'single_503_retained',BOOTSTRAP_503_PERSISTENT:'persistent_503'}[arm];
     await coldReload(transport,'after_relogin');
     // Controls need the same unavailable-section Retry predecessor. This one
     // extra 503 reload is explicitly recorded as setup, never as a clean200.
     if(!['single_503_retained','persistent_503'].includes(transport))await coldReload('single_503_retained','held_edit_setup');
     await stage('held_read_new_edit_predecessor');
     let release,enter,handlerFailure=false;const gate=new Promise(resolve=>release=resolve),entered=new Promise(resolve=>enter=resolve);
     const handler=async route=>{note('edit_handler_entered');try{const response=await route.fetch({timeout:25000});assert.equal(response.status(),200);note('edit_handler_fetched',{status:200});enter();await gate;await route.fulfill({response});note('edit_handler_fulfilled');}catch{handlerFailure=true;note('edit_handler_failed');enter();await route.abort('failed').catch(()=>{});}};
     await retainRoute(pattern,handler,'held-edit-predecessor');
     try{
      await click(page.getByRole('button',{name:/^Повторить загрузку/}),'Retry held real matrix read');await bounded(entered,'Held matrix edit read never started');assert.equal(handlerFailure,false);
      await click(page.locator('[data-testid=radial-toggle]'),'Open new draft while matrix read waits');await click(page.locator('[data-testid=capture-text]'),'Enter matrix text draft');await ready('capture/text');
      const newText=fixture('T04').input;await page.locator('[name=text]').fill(newText);await page.locator('[data-fertilizer-draft-status][data-state=saved]').waitFor();
      const newDraft=await draftAt('capture/text',newText);
      const response=page.waitForResponse(r=>r.url().endsWith('/owner/home')&&r.status()===200);release();await response;
      // Match the original sequence; observe requestfinished but do not wait for
      // it or add a delay before navigation, which could hide the suspected race.
      await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));assert.ok(page.url().endsWith('#capture/text'));assert.ok(await page.locator('[name=text]').inputValue()===newText,'Late read overwrote newer edit');
      c.delayed_read_did_not_overwrite_edit=true;
      await page.goto(base+'/r3/connected.html#home');await ready('home');await coldReload(transport,'after_held_edit');
      await sameDraft('capture/text',newText,newDraft);await sameDraft('capture/expense',text,originalDraft);c.both_drafts_retained=true;
     }catch(error){await freezeFailure(error);throw error;}finally{release();}
    }
   }catch(error){primaryError=error;}
   finally{
    // Keep first error/stage before optional evidence or SQL observation. Every
    // arm terminates here; no recovery/retry is applied to an unexpected stall.
    if(primaryError)await freezeFailure(primaryError);
    try{assert.ok(await checkpoint('arm-terminal'),'Missing terminal diagnostics');}catch(error){c.terminal_diagnostic_failed=true;if(!primaryError)primaryError=error;}
    try{c.boundary_after=await observe('matrix-after',true);assert.deepEqual(c.boundary_after,c.boundary_before,'Cold-bootstrap diagnostic changed SQL business/source/proposal/job rows');assert.deepEqual(c.business_writes,[],'Diagnostic dispatched a business write');assert.ok(!c.route_cleanup_failed,'Owned diagnostic route cleanup failed');c.exact_sql_no_write=true;}catch(error){c.boundary_verification_failed=true;if(!primaryError)primaryError=error;}
    page.off('request',writes);page.off('request',onRequest);page.off('response',onResponse);page.off('requestfinished',onFinished);page.off('requestfailed',onFailed);await write();
   }
   if(primaryError)throw primaryError;
  });
 }
 record.status=record.cases.every(c=>c.status===(mode==='baseline'?'BASELINE_RECORDED':'PASS'))?'COMPLETE':'FAIL';await browser.close();await write();
}
result.status=result.records.every(r=>r.status==='COMPLETE')&&!result.errors.length?(mode==='baseline'?'BASELINE_RECORDED':'PASS'):'FAIL';await write();console.log(JSON.stringify({status:result.status,mode,records:result.records.map(r=>({engine:r.engine,cases:r.cases.map(c=>({id:c.id,status:c.status,stage:c.stage,metrics:c.metrics,failure:c.failure?.message}))}))}));if(result.status==='FAIL')process.exitCode=1;
