/** Focused authenticated synthetic DayWorkers proof. Actual scenario writes use
 * real browser controls. API calls below only read results or verify rejection.
 * Run only on the approved CI browser route, never a borrowed owner preview.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import {randomUUID} from 'node:crypto';
import {chromium, webkit} from 'playwright';
import {ownerLogin, workerLogin, pilotCredentials, pilotMode, redactPilot, expiredCookie} from './helpers/pilot-auth.mjs';
import {installClickAudit} from './friction-metrics.mjs';
import {observeBrowserDiagnostics} from './helpers/browser-diagnostics.mjs';
import {observeDiscardedReadTerminal} from './helpers/discarded-read-terminal.mjs';

const base = process.env.PREVIEW_URL || 'http://127.0.0.1:4173';
const out = process.env.E2E_ARTIFACTS;
const engine = process.env.DAY_WORKERS_ENGINE, phase = process.env.DAY_WORKERS_PHASE;
assert.ok(['chromium', 'webkit'].includes(engine));
assert.ok(['request', 'full'].includes(phase), 'Phase must be explicit; request PASS is not full completion');
assert.equal(pilotMode, true);
assert.ok(['127.0.0.1', 'localhost'].includes(new URL(base).hostname));
assert.ok(out);
const setup = JSON.parse(await fs.readFile(path.join(out, 'setup.json'), 'utf8'));
assert.equal(setup.sha, process.env.OWNER_PREVIEW_FEATURE_SHA);
assert.equal(setup.engine, engine); assert.equal(setup.phase, phase);
const credentials = await pilotCredentials();
const root = '/v1/entities/' + setup.entity_id, dayRoot = root + '/day-workers';
const result = {sha:setup.sha, engine, phase, entity_id:setup.entity_id, status:'RUNNING',
  errors:[], checks:[], layouts:[], screenshots:[], operations:[], retained_routes:[], stage_files:{}, actual_ids:[], primary_actions:[],
  coverage:phase === 'request' ? 'Focused request/decision/actual checkpoint only' : 'Focused complete vertical flow; not full regression'};
let browser, active;
const contexts = [], diagnostics = [];
const get = async(context, url) => {const r = await context.request.get(base + url); assert.equal(r.status(), 200, await r.text()); return r.json();};
const readWorker = context => get(context, '/v1/worker/day-workers');
const readOwner = context => get(context, dayRoot);
const ownerReady = (page, route) => page.waitForFunction(r => document.querySelector('#main')?.dataset.renderedRoute === r && location.hash === '#' + r, route);
const click = async(page, locator, label) => {
  await locator.scrollIntoViewIfNeeded();
  const before = await page.evaluate(() => window.ownerFrictionClicks.length);
  await locator.click();
  const events = await page.evaluate(start => window.ownerFrictionClicks.slice(start), before);
  assert.ok(events.some(e => e.trusted), 'Expected real trusted browser click: ' + label);
  result.operations.push({kind:'click', label, trusted:true, count:events.filter(e => e.trusted).length});
};
const field = async(page, name, value, scope = page) => {
  const locator = scope.locator('[name="' + name + '"]');
  const ancestors = locator.locator('xpath=ancestor::details');
  for (let i=0; i<await ancestors.count(); i++) {
    const item=ancestors.nth(i); if(!await item.evaluate(n=>n.open)) await click(page,item.locator(':scope > summary'),'Open optional field details');
  }
  const tag=await locator.evaluate(n=>n.tagName);
  if(tag==='SELECT') await locator.selectOption(String(value));
  else await locator.fill(String(value));
  assert.equal(await locator.inputValue(),String(value));
  result.operations.push({kind:tag==='SELECT'?'select':'fill',name,value:String(value)});
};
const ownerNav = async(page, route) => {const target=base+'/r3/connected.html#'+route; if(page.url()===target) await page.reload(); else await page.goto(target); await ownerReady(page,route);};
const submit = async(page, formSelector, suffix, label) => {
  const reply=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname.endsWith(suffix));
  await click(page,page.locator(formSelector).locator('button[type=submit]'),label);
  const response=await reply; assert.ok([200,201].includes(response.status()), await response.text()); return response.json();
};
// Keep Chromium Fetch interception enabled through the owned case boundary.
// Auto-removing times:1 routes previously disturbed sibling bootstrap requests.
async function installRetainedFault(page, matcher, method, first, evidence) {
  let consumed=false, firstRequest;
  Object.assign(evidence,{lifecycle:'retained_until_explicit_cleanup',first_calls:0,passthrough_calls:0,observed_statuses:[]});
  const response=reply=>{if(reply.request()===firstRequest)evidence.observed_statuses.push(reply.status());};
  const handler=async route=>{
    if(route.request().method()!==method||consumed){evidence.passthrough_calls++;return route.continue();}
    consumed=true; firstRequest=route.request(); evidence.first_calls++;
    return first(route);
  };
  page.on('response',response); await page.route(matcher,handler);
  return async()=>{await page.unroute(matcher,handler);page.off('response',response);evidence.cleaned_up=true;};
}
async function bounded(promise,label,milliseconds=20000) {
  let timer;
  try{return await Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error(label+' timed out')),milliseconds);})]);}
  finally{clearTimeout(timer);}
}
function assertWorkerFinancialPrivacy(value) {
  const forbidden=new Set(['accrual','accruals','counterparty_id','counterparty_name','daily_rate','rate_basis','currency','amount','original_amount','remaining_amount','obligation_id','payment_id','expense_account_id','posting_id','references']);
  const visit=node=>{
    if(!node||typeof node!=='object')return;
    for(const [key,item] of Object.entries(node)){assert.ok(!forbidden.has(key),'Worker financial disclosure: '+key);visit(item);}
  };
  visit(value);
}
async function submitWithLostReply(page, formSelector, suffix, label, reopen=null) {
  let committed, originalBody, attempts=0;
  const pattern='**'+suffix;
  const interceptor=async route=>{
    if(route.request().method()!=='POST') return route.continue();
    attempts++;
    const body=route.request().postDataJSON();
    if(attempts===1) {
      originalBody=body;
      const response=await route.fetch(); assert.ok([200,201].includes(response.status()),await response.text());
      committed=await response.json(); await route.abort('failed');
    } else { assert.deepEqual(body,originalBody,'Lost-response retry changed original intent'); await route.continue(); }
  };
  await page.route(pattern,interceptor);
  try {
    await click(page,page.locator(formSelector).locator('button[type=submit]'),label+' first committed response is lost');
    await page.waitForFunction(selector=>{
      const error=document.querySelector('#worker-error')||document.querySelector(selector+' .form-error');
      return !!error?.textContent.trim();
    },formSelector);
    assert.ok(committed,'Fault injection must occur after a real successful server commit');
    if(reopen) await reopen();
    const replay=await submit(page,formSelector,suffix,label+' retries original saved intent');
    assert.equal(replay.id,committed.id); assert.equal(attempts,2);
    check('Lost response after real commit recovered exactly once',{operation:label,reloaded:!!reopen,same_original:true});
    return replay;
  } finally { await page.unroute(pattern,interceptor); }
}
const observe = async(name) => {
  const filename=path.join(out,'stage-'+name+'.json');
  execFileSync(process.env.PYTHON||'python',['scripts/day_workers_sql_evidence.py','--probe','--output',filename],{env:process.env,timeout:30000,stdio:['ignore','pipe','pipe']});
  result.stage_files[name]=filename;
  return JSON.parse(await fs.readFile(filename,'utf8'));
};
const source = (kind, fields) => ({schema_version:1,form_kind:kind,form_id:randomUUID(),captured_at:new Date().toISOString(),timezone:'Europe/Istanbul',fields});
const check = (name, detail={}) => result.checks.push({name,pass:true,...detail});
async function layout(page,name) {
  for(const width of [320,390,768,1280]) {
    await page.setViewportSize({width,height:844});
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    const bounds=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}));
    assert.ok(bounds.scroll<=bounds.width+1,name+' overflow '+JSON.stringify(bounds));
    const visible=await page.locator('button:visible,input:visible,textarea:visible,select:visible').evaluateAll(nodes=>nodes.map(n=>({tag:n.tagName,name:n.name||'',left:n.getBoundingClientRect().left,right:n.getBoundingClientRect().right})).filter(n=>n.right>innerWidth+1||n.left< -1));
    assert.deepEqual(visible,[],name+' clipped controls');
    const filename=engine+'-'+width+'-'+name+'.png';
    await page.screenshot({path:path.join(out,filename),fullPage:true,mask:[page.locator('#username,#password,#secret,#worker-key,#worker-test-key')]});
    result.screenshots.push(filename); result.layouts.push({name,...bounds});
  }
  await page.setViewportSize({width:390,height:844});
}
// BEGIN_PRIMARY_ACTION_PROOF_HELPERS
// Viewport evidence only. The scenario's existing trusted click remains the
// only activation; this helper does not click, force, dispatch, or submit.
const primaryProofScreens = new Set();
function primaryActionIssues(evidence) {
  const issues=[],r=evidence.rect,v=evidence.viewport,edgeTolerance=0.5;
  if(!evidence.visible)issues.push('Target is not visible');
  if(r.width<44||r.height<44)issues.push('Target is smaller than 44 x 44 CSS pixels');
  if(r.left<v.left-edgeTolerance||r.top<v.top-edgeTolerance||r.right>v.right+edgeTolerance||r.bottom>v.bottom+edgeTolerance)issues.push('Target is not contained in the visible viewport');
  if(evidence.hits.length!==5||evidence.hits.some(hit=>!hit.target_or_descendant))issues.push('Center or inset-quarter point is occluded');
  if(!evidence.enabled||evidence.pending||evidence.inert)issues.push('Target is disabled, pending, or inert; not proven tappable');
  return issues;
}
async function primaryActionProof(page,name,locator,{intent,exercise='existing_trusted_click_follows'}={}) {
  if(phase!=='full'||primaryProofScreens.has(name))return;
  assert.match(name,/^[a-z0-9-]+$/,'Stable primary proof filename required');
  await locator.waitFor({state:'attached'});
  assert.equal(await locator.count(),1,'Primary proof must identify one control: '+name);
  primaryProofScreens.add(name);
  const originalViewport=page.viewportSize(),issues=[];
  try {
    for(const width of [320,390,768,1280]) {
      await page.setViewportSize({width,height:844});
      // Ordinary centered scrolling only; never alter styles or force a click.
      await locator.evaluate(node=>node.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'}));
      await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
      const visible=await locator.isVisible(),enabled=await locator.isEnabled();
      const observed=await locator.evaluate(node=>{
        const r=node.getBoundingClientRect(),visual=window.visualViewport;
        const left=visual?.offsetLeft??0,top=visual?.offsetTop??0;
        const viewport={left,top,right:left+(visual?.width??innerWidth),bottom:top+(visual?.height??innerHeight),layout_width:innerWidth,layout_height:innerHeight};
        const points=[['center',0.5,0.5],['inset-top-left',0.25,0.25],['inset-top-right',0.75,0.25],['inset-bottom-left',0.25,0.75],['inset-bottom-right',0.75,0.75]];
        const hits=points.map(([point,x,y])=>{
          const px=r.left+r.width*x,py=r.top+r.height*y,hit=document.elementFromPoint(px,py);
          return {point,x:px,y:py,target_or_descendant:!!hit&&(hit===node||node.contains(hit)),hit:hit?{tag:hit.tagName,id:hit.id||null,role:hit.getAttribute('role')}:null};
        });
        return {rect:{left:r.left,top:r.top,right:r.right,bottom:r.bottom,width:r.width,height:r.height},viewport,
          scroll:{x:scrollX,y:scrollY},hits,tag:node.tagName,
          pending:!!node.closest('[aria-busy="true"],[data-state="pending"],[data-state="sending"]'),
          inert:!!node.closest('[inert]'),aria_disabled:node.getAttribute('aria-disabled'),native_disabled:node.matches(':disabled'),
          invalid_required_controls:node.closest('form')?.querySelectorAll('[required]:invalid').length??0};
      });
      const evidence={screen:name,intent,exercise,requested_width:width,visible,enabled,...observed};
      evidence.issues=primaryActionIssues(evidence);
      evidence.state=!enabled||observed.pending||observed.inert?'DISABLED_OR_PENDING':evidence.issues.length?'GEOMETRY_OR_OCCLUSION_FAILURE':'READY_TARGET';
      evidence.tappable=evidence.issues.length===0;
      evidence.screenshot=engine+'-'+width+'-primary-'+name+'.png';
      result.primary_actions.push(evidence);
      // Capture the current viewport after positioning, even when a gate fails.
      await page.screenshot({path:path.join(out,evidence.screenshot),fullPage:false,mask:[page.locator('#username,#password,#secret,#worker-key,#worker-test-key')]});
      result.screenshots.push(evidence.screenshot);
      if(evidence.issues.length)issues.push({width,issues:evidence.issues});
    }
  } finally {
    if(originalViewport)await page.setViewportSize(originalViewport);
    await locator.evaluate(node=>node.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'}));
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  }
  assert.deepEqual(issues,[],'Primary target visibility/touch/occlusion proof failed: '+name);
  check('Primary action viewport proof',{screen:name,widths:[320,390,768,1280],intent,exercise,minimum_css_pixels:44,hit_points:5});
}
// END_PRIMARY_ACTION_PROOF_HELPERS
async function newContext() {
  const context=await browser.newContext({viewport:{width:390,height:844}}),page=await context.newPage(); contexts.push(context);
  page.setDefaultTimeout(20000); page.on('pageerror',e=>result.errors.push(redactPilot(e.message)));
  await installClickAudit(page); diagnostics.push({name:contexts.length,observer:await observeBrowserDiagnostics(page)});
  return {context,page};
}
try {
  await fs.mkdir(out,{recursive:true});
  browser=await ({chromium,webkit}[engine]).launch();
  const {context:oc,page:op}=await newContext(),{context:wc,page:wp}=await newContext();
  active=op;
  assert.equal((await oc.request.get(base+dayRoot)).status(),401);
  await ownerLogin(op,base); await ownerReady(op,'home');
  const config=await get(oc,'/preview/config'); assert.equal(config.feature_sha,setup.sha); assert.equal(config.daily_pilot,true);
  active=wp; await workerLogin(wp,base,credentials[engine].worker);
  const initial=await readWorker(wc);
  assert.ok(initial.greenhouses.some(g=>g.id===setup.greenhouse.id));
  assert.ok(!initial.greenhouses.some(g=>g.id===setup.unpermitted_greenhouse.id));
  assert.ok(!('references' in initial),'Worker read must not contain owner financial references');
  assert.equal((await wc.request.get(base+root+'/finance-overview')).status(),403);
  assert.ok([403,404].includes((await wc.request.get(base+dayRoot)).status()));
  check('Private worker cannot read owner financial or DayWorkers routes');

  await click(wp,wp.locator('#worker-day-open'),'Worker opens DayWorkers');
  await click(wp,wp.locator('[data-day-action=new]'),'Worker starts three-field request');
  if(await wp.locator('[name=greenhouse_id]').count()) {
    await field(wp,'greenhouse_id',setup.greenhouse.id);
    await click(wp,wp.locator('#worker-day-form button[type=submit]'),'Worker selects verified greenhouse context');
  }
  const requestForm=wp.locator('#worker-day-form');
  await requestForm.locator('[name=people]').waitFor();
  await field(wp,'people','5',requestForm); await field(wp,'days','2',requestForm); await field(wp,'work',setup.work,requestForm);
  const primary=await requestForm.locator('input:visible,textarea:visible,select:visible').evaluateAll(nodes=>nodes.map(n=>n.name).filter(n=>n!=='greenhouse_id'));
  assert.deepEqual(primary.sort(),['days','people','work'],'Only the three required business fields are visible');
  await layout(wp,'worker-request');
  await primaryActionProof(wp,'worker-new-request',requestForm.locator('button[type=submit]'),{intent:'submit_original_worker_request'});
  const created=await (phase==='full'?submitWithLostReply:submit)(wp,'#worker-day-form','/day-workers/requests','Worker sends original request');
  const requestId=created.id; assert.ok(requestId);
  let state=await readWorker(wc),request=state.requests.find(r=>r.id===requestId); assert.ok(request);
  assert.equal(request.requested_people_days,10); assert.equal(request.planned_people_days,null); assert.equal(request.actual_people_days,0);
  assert.equal(request.original.fields.people,'5'); assert.equal(request.original.fields.days,'2'); assert.equal(request.original.fields.work,setup.work);
  assert.equal(request.original.fields.start_date,''); assert.equal(request.original.fields.note,'');
  await observe('request'); check('Worker request preserves exact three-field original and plan 10');

  // Rejected direct writes are independent of menu visibility.
  const deniedPayload={greenhouse_id:setup.unpermitted_greenhouse.id,source:source('day_request',{people:'5',days:'2',work:'TEST denied cross-greenhouse',start_date:'',note:''})};
  const denied=await wc.request.post(base+'/v1/worker/day-workers/requests',{data:deniedPayload,headers:{Origin:base,'X-Pilot-Participant':credentials[engine].worker.participant_id,'Idempotency-Key':randomUUID()}});
  assert.ok([403,404].includes(denied.status()),'A valid request must fail scope authorization, not payload validation');
  const {context:xc,page:xp}=await newContext(); await workerLogin(xp,base,credentials[engine].other_worker);
  const other=await readWorker(xc); assert.ok(!other.requests.some(r=>r.id===requestId));
  assert.ok([403,404].includes((await xc.request.get(base+'/v1/worker/day-workers/requests/'+requestId)).status()));
  check('Cross-greenhouse direct request and other-worker read are fenced',{cross_greenhouse_status:denied.status()});

  let staleOwnerPage,staleEditedWork;
  if(phase==='full') {
    const secondOwner=await newContext(); staleOwnerPage=secondOwner.page;
    await ownerLogin(staleOwnerPage,base); await ownerReady(staleOwnerPage,'home');
    await ownerNav(staleOwnerPage,'day-decision/'+requestId+'/REVISE_APPROVE');
    staleEditedWork=setup.work+' · TEST reviewed updated instructions';
    await field(staleOwnerPage,'work',staleEditedWork,staleOwnerPage.locator('[data-form=day-decision]'));
    await field(staleOwnerPage,'reason','TEST owner reviewed revised instruction before competing approval');
    assert.equal(await staleOwnerPage.locator('[name=people]').inputValue(),'5');
    assert.equal(await staleOwnerPage.locator('[name=days]').inputValue(),'2');
  }
  active=op; await ownerNav(op,'day-workers');
  await click(op,op.locator('a[href="#day-request/'+requestId+'"]'),'Owner opens exact worker request'); await ownerReady(op,'day-request/'+requestId);
  await layout(op,'owner-request');
  await click(op,op.locator('a[href="#day-decision/'+requestId+'/APPROVE"]'),'Owner reviews approval'); await ownerReady(op,'day-decision/'+requestId+'/APPROVE');
  await field(op,'reason','TEST owner approves plan only');
  await primaryActionProof(op,'owner-decision',op.locator('[data-form=day-decision] button[type=submit]'),{intent:'save_owner_plan_decision'});
  await submit(op,'[data-form=day-decision]','/requests/'+requestId+'/decisions','Owner saves explicit plan decision');
  await ownerReady(op,'day-request/'+requestId);
  request=(await readOwner(oc)).requests.find(r=>r.id===requestId); assert.equal(request.state,'APPROVED'); assert.equal(request.decisions.length,1); assert.equal(request.planned_people_days,10);
  if(phase==='full') {
    active=staleOwnerPage;
    const rejected=staleOwnerPage.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname===dayRoot+'/requests/'+requestId+'/decisions');
    await click(staleOwnerPage,staleOwnerPage.locator('[data-form=day-decision] button[type=submit]'),'Stale owner submits the preserved edited instruction');
    assert.equal((await rejected).status(),409);
    assert.equal(await staleOwnerPage.locator('[name=work]').inputValue(),staleEditedWork,'Rejected intent must remain visible before recovery');
    const refreshedRequest=staleOwnerPage.waitForResponse(r=>r.request().method()==='GET'&&new URL(r.url()).pathname===dayRoot+'/requests/'+requestId);
    await click(staleOwnerPage,staleOwnerPage.locator('[data-action=refresh]'),'Owner loads current request through the displayed conflict recovery action');
    assert.equal((await refreshedRequest).status(),200);
    await staleOwnerPage.locator('[name=reviewed_current]').waitFor();
    assert.equal(await staleOwnerPage.locator('[name=work]').inputValue(),staleEditedWork,'Known conflict must retain edited visible intent');
    await layout(staleOwnerPage,'owner-stale-decision-review');
    await click(staleOwnerPage,staleOwnerPage.locator('[name=reviewed_current]'),'Owner explicitly reviews the now-current request');
    const revised=await submit(staleOwnerPage,'[data-form=day-decision]','/requests/'+requestId+'/decisions','Owner resubmits explicitly reviewed current instruction');
    await ownerReady(staleOwnerPage,'day-request/'+requestId);
    request=(await readOwner(oc)).requests.find(r=>r.id===requestId);
    assert.equal(request.decisions.length,2); assert.equal(request.version,3);
    assert.equal(request.current_plan.work,staleEditedWork); assert.equal(request.planned_people_days,10);
    assert.equal(request.original.fields.work,setup.work,'Owner revision cannot change worker original');
    result.stale_review={rejected_status:409,revised_decision_id:revised.id,work:staleEditedWork,version:request.version,explicit_current_review:true};
    check('Real stale owner form recovers after explicit current review without losing edited intent');
    active=op;
  }
  await observe('decision'); check('Owner approval has no money effect');
  const stale=await oc.request.post(base+dayRoot+'/requests/'+requestId+'/decisions',{data:{expected_version:1,source:source('day_decision',{decision:'APPROVE',people:'5',days:'2',work:setup.work,start_date:'',note:'',reason:'TEST stale owner decision must not append'})},headers:{Origin:base,'X-Pilot-Participant':credentials.owner.participant_id,'Idempotency-Key':randomUUID()}});
  assert.equal(stale.status(),409); check('Stale owner decision rejects obsolete version');

  // Submit each date separately. No automatic plan-to-fact conversion.
  for(const [index,people] of ['4','3'].entries()) {
    active=wp; await wp.reload(); await wp.locator('#worker-app h1').waitFor();
    await click(wp,wp.locator('#worker-day-open'),'Worker reopens DayWorkers');
    await click(wp,wp.locator('[data-day-request="'+requestId+'"]'),'Worker opens approved request');
    await wp.getByRole('heading',{level:1,name:request.current_plan.work,exact:true}).waitFor({state:'visible'});
    await click(wp,wp.locator('[data-day-action=actual]'),'Worker records actual dated work');
    const actualForm=wp.locator('#worker-day-form');
    await field(wp,'event_date',setup.dates[index],actualForm); await field(wp,'people',people,actualForm);
    await field(wp,'work',setup.work+' день '+(index+1),actualForm); await field(wp,'full_day','true',actualForm);
    await field(wp,'allocation_mode','same',actualForm);
    await field(wp,'note','TEST actual '+people+' includes team lead',actualForm);
    if(index===0){await layout(wp,'worker-actual');await primaryActionProof(wp,'worker-actual',actualForm.locator('button[type=submit]'),{intent:'submit_one_actual_work_day'});}
    const reopen=async()=>{
      await wp.reload(); await wp.locator('#worker-day-open').waitFor();
      await click(wp,wp.locator('#worker-day-open'),'Reopen DayWorkers after lost actual response');
      await click(wp,wp.locator('[data-day-request="'+requestId+'"]'),'Reopen original request after lost actual response');
      await wp.getByRole('heading',{level:1,name:request.current_plan.work,exact:true}).waitFor({state:'visible'});
      await click(wp,wp.locator('[data-day-action=actual]'),'Recover saved pending original actual');
      assert.equal(await wp.locator('#worker-day-form [name=people]').inputValue(),people);
    };
    const actual=phase==='full'&&index===0?await submitWithLostReply(wp,'#worker-day-form','/day-workers/actuals','Worker sends actual '+people+' people',reopen):await submit(wp,'#worker-day-form','/day-workers/actuals','Worker sends actual '+people+' people');
    result.actual_ids.push(actual.id); assert.ok(actual.id);
    request=(await readOwner(oc)).requests.find(r=>r.id===requestId);
    assert.equal(request.actual_people_days,index===0?4:7); assert.equal(request.planned_people_days,10);
    await observe('actual-'+(index+1));
  }
  result.request={id:requestId,planned_people_days:request.planned_people_days,actual_people_days:request.actual_people_days};
  state=await readWorker(wc);
  for(const actual of state.actuals.filter(a=>result.actual_ids.includes(a.id))) {
    for(const key of ['accrual','counterparty_id','daily_rate','amount','obligation_id']) assert.ok(!(key in actual),'Worker financial disclosure: '+key);
  }
  check('Daily facts total actual 7 and worker payload omits finance');
  active=op; await ownerNav(op,'day-request/'+requestId);
  const summaryValue=async label=>{const value=op.getByText(label,{exact:true}).locator('..').locator('dd'); await value.waitFor({state:'visible'}); return value.innerText();};
  assert.equal(await summaryValue('Одобрено в плане, человеко-дней'),'10');
  assert.equal(await summaryValue('Известно фактически, человеко-дней'),'7');
  assert.equal(await op.locator('a[href^="#day-actual-detail/"]').count(),2);
  for(const [index,people] of ['4','3'].entries()) {
    const actualLink=op.locator('a[href="#day-actual-detail/'+result.actual_ids[index]+'"] strong');
    await actualLink.waitFor({state:'visible'});
    assert.equal(await actualLink.innerText(),setup.dates[index]+' · '+people+' человек');
  }
  result.owner_rendered_summary={planned_people_days:10,actual_people_days:7,dated_people:[4,3]};
  check('Owner sees fresh plan 10 and both dated actuals totaling 7 after reopening');
  await layout(op,'owner-actual-summary');

  if(phase==='full') {
    // Deliver a genuine older read after the owner chose a newer destination.
    let releaseRead,enteredRead,terminalObserver;
    const gate=new Promise(r=>releaseRead=r),entered=new Promise(r=>enteredRead=r);
    const readEvidence={label:'held-day-workers-read'}; result.retained_routes.push(readEvidence);
    const cleanupRead=await installRetainedFault(op,'**'+dayRoot,'GET',async route=>{
      const response=await route.fetch(); assert.equal(response.status(),200);
      terminalObserver=observeDiscardedReadTerminal(op,route.request()); enteredRead(); await gate;
      await route.fulfill({response});
    },readEvidence);
    try {
      await click(op,op.locator('a[href="#day-workers"]').first(),'Owner starts DayWorkers list read');
      await bounded(entered,'Held DayWorkers read entry');
      await click(op,op.locator('[data-testid=nav-home]'),'Owner navigates home during older read'); await ownerReady(op,'home');
      releaseRead(); readEvidence.terminal=await bounded(terminalObserver.promise,'Released DayWorkers read terminal event');
      assert.equal(readEvidence.terminal.status,200);
      await op.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
      assert.equal(new URL(op.url()).hash,'#home'); assert.equal(await op.locator('#main').getAttribute('data-rendered-route'),'home');
      check('Older DayWorkers read cannot overwrite newer owner navigation');
    } finally {releaseRead(); terminalObserver?.dispose(); await cleanupRead();}
    // Inspect the separate operational confirmation control without posting
    // another fact. Accrual below retains its existing authoritative workflow.
    const confirmationActualId=result.actual_ids[0];
    const confirmationBefore=(await readOwner(oc)).actuals.find(a=>a.id===confirmationActualId).confirmation?.id??null;
    await ownerNav(op,'day-actual-detail/'+confirmationActualId);
    await click(op,op.locator('a[href="#day-confirmation/'+confirmationActualId+'"]'),'Owner opens separate fact confirmation for viewport-only inspection');
    await ownerReady(op,'day-confirmation/'+confirmationActualId);
    const factReview=op.locator('[data-form=day-confirmation]');
    await click(op,factReview.locator('[name=confirmed]'),'Owner checks local fact review without submitting');
    await primaryActionProof(op,'owner-fact-confirmation',factReview.locator('button[type=submit]'),{intent:'confirm_fact_without_accrual',exercise:'view_and_local_review_only_not_submitted'});
    await click(op,op.locator('[data-action=back]'),'Owner returns without submitting fact confirmation');
    await ownerReady(op,'day-actual-detail/'+confirmationActualId);
    const confirmationAfter=(await readOwner(oc)).actuals.find(a=>a.id===confirmationActualId).confirmation?.id??null;
    assert.equal(confirmationAfter,confirmationBefore,'Viewport-only fact review must not create a server confirmation');
    check('Separate fact-confirmation target inspected without submission',{actual_id:confirmationActualId,submitted:false});
    result.obligation_ids=[];
    for(const actualId of result.actual_ids) {
      await ownerNav(op,'day-actual-detail/'+actualId);
      await click(op,op.locator('a[href="#day-accrual/'+actualId+'"]'),'Owner reviews actual-day accrual'); await ownerReady(op,'day-accrual/'+actualId);
      const form=op.locator('[data-form=day-accrual]');
      assert.equal(await form.locator('[name=daily_rate]').inputValue(),'','No guessed default rate');
      assert.equal(await form.locator('[name=counterparty_id]').inputValue(),'','No guessed default creditor');
      for(const [name,value] of Object.entries({counterparty_id:setup.counterparty_id,daily_rate:setup.rate,rate_basis:'TEST verified 100 TRY per person per full day on 2026-10-11 and 2026-10-12',currency:'TRY',expense_account_id:setup.expense_account_id})) await field(op,name,value,form);
      await click(op,form.locator('[name=confirmed]'),'Owner explicitly confirms rate creditor and full-day facts');
      await layout(op,'owner-accrual-'+result.obligation_ids.length);
      await primaryActionProof(op,'owner-accrual',form.locator('button[type=submit]'),{intent:'create_explicit_actual_day_payable'});
      await (result.obligation_ids.length===0?submitWithLostReply:submit)(op,'[data-form=day-accrual]','/actuals/'+actualId+'/accruals','Owner creates actual-day payable');
      await ownerReady(op,'day-actual-detail/'+actualId);
      const actual=(await readOwner(oc)).actuals.find(a=>a.id===actualId); assert.ok(actual.accrual?.obligation_id); result.obligation_ids.push(actual.accrual.obligation_id);
    }
    await observe('accruals');
    await ownerNav(op,'settle/payable/'+setup.counterparty_id+'/TRY');
    await field(op,'account',setup.account_id); await field(op,'date',setup.dates[1]);
    await field(op,'allocation_'+result.obligation_ids[0],setup.payment);
    await click(op,op.getByRole('button',{name:'Проверить оплату',exact:true}),'Owner reviews separate partial payment');
    await op.waitForFunction(()=>location.hash.startsWith('#payment/')&&document.querySelector('#main')?.dataset.renderedRoute===location.hash.slice(1));
    result.payment_id=new URL(op.url()).hash.slice('#payment/'.length);
    await layout(op,'owner-separate-partial-payment');
    await primaryActionProof(op,'owner-separate-payment',op.getByRole('button',{name:'Подтвердить оплату в TEST',exact:true}),{intent:'post_separate_partial_payment'});
    let paymentCommitted=false;
    const paymentEvidence={label:'lost-payment-commit-response'}; result.retained_routes.push(paymentEvidence);
    const paymentPattern='**/v1/payments/'+result.payment_id+'/post?*';
    const cleanupPayment=await installRetainedFault(op,paymentPattern,'POST',async route=>{
      const response=await route.fetch(); assert.equal(response.status(),200);
      paymentCommitted=true; paymentEvidence.committed_status=response.status(); await route.abort('failed');
    },paymentEvidence);
    try {
      await click(op,op.getByRole('button',{name:'Подтвердить оплату в TEST',exact:true}),'Owner explicitly posts separate partial payment');
      await op.locator('#action-error').filter({hasText:'Ответ сервера не получен'}).waitFor(); assert.equal(paymentCommitted,true);
      await op.reload(); await ownerReady(op,'payment/'+result.payment_id);
      await op.getByText('Сервер подтвердил платёж. Повтор не создаёт вторую проводку.',{exact:true}).waitFor();
      check('Lost payment commit response recovers after reload without another payment');
    } finally {await cleanupPayment();}
    await observe('payment');
    const analytics=await get(oc,root+'/greenhouse-daily?date_from='+setup.dates[0]+'&date_to='+setup.dates[1]+'&currency=TRY&greenhouse_id='+setup.greenhouse.id);
    result.analytics=analytics;
    assert.equal(analytics.selected.id,setup.greenhouse.id);
    assert.equal(analytics.selected.work.day_workers.reported_people_days,7);
    assert.equal(analytics.selected.work.day_workers.confirmed_people_days,7);
    assert.equal(analytics.selected.work.day_workers.accrued_amount,'700.00');
    assert.equal(analytics.selected.work.day_workers.paid_amount,'200.00');
    assert.equal(analytics.selected.work.day_workers.current_debt,'500.00');
    assert.equal(analytics.selected.work.day_workers.payment_allocation_unknown,false);
    assert.equal(analytics.selected.finance.operating_cost,'700.00');
    assert.equal(analytics.selected.finance.exact_cost,'700.00');
    assert.equal(analytics.selected.finance.per_da.operating_cost,'350.0000');
    assert.equal(Number(analytics.selected.area.area_da),2);
    assert.equal(analytics.selected.area.status,'VERIFIED_FULL_PERIOD');
    assert.ok(analytics.selected.area.reference_ids.includes(setup.area_reference_id));
    check('Separate partial payment and greenhouse analytics match authoritative actuals and costs');
    const greenhouseBase='greenhouses/'+setup.greenhouse.id;
    const greenhouseTail='/'+setup.dates[0]+'/'+setup.dates[1]+'/TRY/all/all/kg';
    await ownerNav(op,'greenhouses/all/overview'+greenhouseTail);
    await field(op,'greenhouse_id',setup.greenhouse.id,op.locator('[data-form=greenhouse-filters]'));
    await click(op,op.locator('[data-form=greenhouse-filters] button[type=submit]'),'Owner selects verified greenhouse and dates');
    await ownerReady(op,greenhouseBase+'/overview'+greenhouseTail);
    await layout(op,'greenhouse-overview');
    // Additional read-only cockpit coverage: the existing fixture has work and
    // money sources, but deliberately no harvest. Do not call this yield proof.
    const cockpitValue=async(label,kind='greenhouse')=>{
      const row=op.locator('.'+kind+'-fact').filter({has:op.getByText(label,{exact:true})});
      assert.equal(await row.count(),1,'Unique rendered fact: '+label);
      return row.locator(':scope > strong').innerText();
    };
    const assertCockpitFilters=async()=>{
      const form=op.locator('[data-form=greenhouse-filters]');
      for(const [name,value] of Object.entries({greenhouse_id:setup.greenhouse.id,date_from:setup.dates[0],date_to:setup.dates[1],currency:'TRY',allocation:'all'}))
        assert.equal(await form.locator('[name='+name+']').inputValue(),value,'Retained cockpit filter '+name);
    };
    const tab=label=>op.getByRole('navigation',{name:'Разделы теплицы',exact:true}).getByRole('link',{name:label,exact:true});
    const readonlyCockpitWrites=[];
    const watchCockpitWrite=request=>{if(!['GET','HEAD'].includes(request.method())&&new URL(request.url()).pathname.startsWith('/v1/'))readonlyCockpitWrites.push({method:request.method(),path:new URL(request.url()).pathname});};
    op.on('request',watchCockpitWrite);
    try {
      await assertCockpitFilters();
      assert.equal(await cockpitValue('Ветки · полевые отчёты'),'Неизвестно');
      assert.equal(await cockpitValue('Вес · подтверждённое депо'),'Неизвестно');
      assert.equal(await cockpitValue('Учтённые затраты'),'700 TRY');
      assert.equal(analytics.selected.harvest.branches.value,null);
      assert.equal(analytics.selected.harvest.depot_kg.value,null);
      assert.deepEqual(analytics.selected.harvest.events,[]);
      assert.deepEqual(analytics.selected.harvest.field_events,[]);
      assert.equal(analytics.selected.harvest.weighted_price_per_kg,null);
      const comparison=analytics.selected.comparison;
      assert.ok(['NO_PRIOR_SOURCES','UNAVAILABLE'].includes(comparison.status));
      assert.equal(comparison.previous??null,null);
      const comparisonText=comparison.status==='NO_PRIOR_SOURCES'?'Сопоставимого прошлого сезона нет: подтверждённых источников недостаточно.':'Сравнение с прошлым сезоном недоступно для этого периода.';
      await op.getByText(comparisonText,{exact:true}).waitFor({state:'visible'});

      await click(op,tab('Урожай'),'Owner opens greenhouse Harvest tab with retained filters');
      await ownerReady(op,greenhouseBase+'/harvest'+greenhouseTail);await assertCockpitFilters();
      await op.getByRole('heading',{name:'Месяцы выбранного периода',exact:true}).waitFor();
      assert.equal(await cockpitValue('Средневзвешенная цена'),'Неизвестно');
      await op.getByText('В выбранном месяце и охвате источников нет.',{exact:true}).waitFor({state:'visible'});
      assert.equal(await op.locator('a[href^="#depot/"],a[href^="#report/"]').count(),0);
      const qualityCard=op.locator('.greenhouse-card').filter({has:op.getByRole('heading',{name:'Качество депо',exact:true})});
      // Empty source buckets are mathematical sums, not evidence of zero yield.
      assert.equal(analytics.selected.harvest.quality_rows.length,4);
      for(const quality of analytics.selected.harvest.quality_rows){assert.equal(quality.share_percent,null);assert.equal(quality.weighted_price_per_kg,null);assert.deepEqual(quality.line_ids,[]);}
      assert.equal(await qualityCard.getByText('Доля неизвестна · Неизвестно',{exact:true}).count(),4);
      assert.ok(!(await qualityCard.innerText()).includes('%'),'Empty quality has no invented percentages');
      const month=setup.dates[0].slice(0,7),monthTail=greenhouseTail.replace('/all/all/kg','/all/'+month+'/kg');
      const monthData=analytics.selected.harvest.months.find(value=>value.month===month);
      assert.ok(monthData);assert.equal(monthData.state,'NO_DATA');assert.equal(monthData.depot_kg,null);assert.equal(monthData.source_count,0);
      await click(op,op.locator('a.harvest-month[href="#'+greenhouseBase+'/harvest'+monthTail+'"]'),'Owner inspects a source-empty harvest month');
      await ownerReady(op,greenhouseBase+'/harvest'+monthTail);await assertCockpitFilters();
      assert.equal(await cockpitValue('Выбранный месяц · '+month),'Неизвестно');
      await op.getByText('Нет источников',{exact:true}).waitFor({state:'visible'});
      await layout(op,'greenhouse-harvest-empty-sources');
      await click(op,op.getByRole('link',{name:'Весь выбранный период',exact:true}),'Owner restores full selected harvest period');
      await ownerReady(op,greenhouseBase+'/harvest'+greenhouseTail);await assertCockpitFilters();

      await click(op,tab('Финансы'),'Owner opens greenhouse Finance tab with retained filters');
      const financeRoute=greenhouseBase+'/finance'+greenhouseTail;
      await ownerReady(op,financeRoute);await assertCockpitFilters();
      for(const [label,value] of Object.entries({'Прямые затраты':'700 TRY','GENERAL · аналитическая доля':'0 TRY','Все учтённые затраты':'700 TRY','Учтённые затраты на кг':'Неизвестно','Проверенная площадь':'2 дёнюм','Затраты на дёнюм':'350 TRY/дёнюм'}))assert.equal(await cockpitValue(label),value);
      await op.getByText('Оплаты показывают движение денег и не добавляют затраты второй раз. Банковские остатки относятся ко всему хозяйству.',{exact:true}).waitFor({state:'visible'});
      const financeEvents=op.locator('.greenhouse-card .money-event');
      assert.equal(await financeEvents.count(),2,'Only two actual-day accruals are economic costs');
      assert.ok(analytics.selected.finance.economic_events.every(event=>event.classification==='OPERATING_COST'&&event.source.kind==='obligation'));
      assert.equal(await op.locator('a[href^="#payment/"]').count(),0,'Separate payment is not another greenhouse cost event');
      await layout(op,'greenhouse-finance-known-cost');
      const documentRoute='money-document/'+result.obligation_ids[0]+'/'+encodeURIComponent(financeRoute);
      await click(op,financeEvents.locator('a[href="#'+documentRoute+'"]'),'Owner opens the existing first actual-day finance source');
      await ownerReady(op,documentRoute);
      for(const [label,value] of Object.entries({'Первоначальная сумма':'400 TRY','Погашено':'200 TRY','Текущий остаток':'200 TRY'}))assert.equal(await cockpitValue(label,'money'),value);
      await click(op,op.getByRole('link',{name:'Назад к финансам',exact:true}),'Owner returns from financial source to the same greenhouse Finance filters');
      await ownerReady(op,financeRoute);await assertCockpitFilters();
      assert.equal(await cockpitValue('Все учтённые затраты'),'700 TRY');
      result.cockpit_tabs={harvest:{coverage:'EMPTY_SOURCE_UI_ONLY_NOT_NUMERIC_YIELD_PROOF',weight:null,price:null,quality_shares_known:false,comparison_status:comparison.status},finance:{cost:'700.00',source_document:{original:'400.00',paid:'200.00',remaining:'200.00'},return_filters_preserved:true}};
      check('Greenhouse Harvest honest empty states and Finance known costs/source return retain filters',{numeric_harvest_proof:false,added_layouts:['greenhouse-harvest-empty-sources','greenhouse-finance-known-cost']});
    } finally {op.off('request',watchCockpitWrite);}
    assert.deepEqual(readonlyCockpitWrites,[],'Read-only cockpit tab/source inspection must not create authoritative writes');
    await click(op,op.locator('a[href="#'+greenhouseBase+'/work'+greenhouseTail+'"]').first(),'Owner opens greenhouse work and financial facts');
    await ownerReady(op,greenhouseBase+'/work'+greenhouseTail);
    for(const label of ['Сообщено полных человеко-дней','Принято владельцем, человеко-дней','Начислено в периоде','Выплачено в периоде','Текущий долг связанных документов']) await op.getByText(label,{exact:true}).waitFor();
    for(const [label,value] of Object.entries({'Начислено в периоде':'700 TRY','Выплачено в периоде':'200 TRY','Текущий долг связанных документов':'500 TRY'}))assert.equal(await cockpitValue(label),value);
    await layout(op,'greenhouse-work-and-debt');
    await primaryActionProof(op,'cockpit-source-drilldown',op.locator('a[href^="#day-actual-detail/'+result.actual_ids[0]+'/"]').first(),{intent:'open_source_actual_from_greenhouse'});
    await click(op,op.locator('a[href^="#day-actual-detail/'+result.actual_ids[0]+'/"]').first(),'Owner drills down from greenhouse to original actual');
    await op.waitForFunction(id=>location.hash.startsWith('#day-actual-detail/'+id)&&document.querySelector('#main')?.dataset.renderedRoute===location.hash.slice(1),result.actual_ids[0]);
    await primaryActionProof(op,'cockpit-source-back',op.getByRole('link',{name:'Назад к работам',exact:true}),{intent:'return_to_same_greenhouse_filters'});
    await click(op,op.getByRole('link',{name:'Назад к работам',exact:true}),'Owner returns to same greenhouse selection');
    await ownerReady(op,greenhouseBase+'/work'+greenhouseTail);
    check('Greenhouse period selection, source drilldown and return preserve context');
    const workerOverviewAfterFinance=await readWorker(wc);
    const workerRequestAfterFinance=await get(wc,'/v1/worker/day-workers/requests/'+requestId);
    const workerActualsAfterFinance=await Promise.all(result.actual_ids.map(id=>get(wc,'/v1/worker/day-workers/actuals/'+id)));
    for(const value of [workerOverviewAfterFinance,workerRequestAfterFinance,...workerActualsAfterFinance]) assertWorkerFinancialPrivacy(value);
    assert.equal(workerRequestAfterFinance.actual_people_days,7);
    assert.equal(workerActualsAfterFinance.length,2); assert.ok(workerActualsAfterFinance.every(a=>a.confirmation));
    active=wp; await wp.reload(); await wp.locator('#worker-day-open').waitFor();
    await click(wp,wp.locator('#worker-day-open'),'Worker reopens work after owner accrual and payment');
    await click(wp,wp.locator('[data-day-request="'+requestId+'"]'),'Worker opens original request after finance exists');
    await wp.locator('#worker-app h1').filter({hasText:workerRequestAfterFinance.current_plan.work}).waitFor();
    const workerRendered=await wp.locator('#worker-app').innerText();
    for(const privateValue of ['TEST DayWorkers '+engine+' known creditor','TEST verified 100 TRY','700.00','200.00','500.00','TRY']) assert.ok(!workerRendered.includes(privateValue),'Rendered worker disclosure: '+privateValue);
    await layout(wp,'worker-after-owner-finance');
    result.worker_privacy_after_finance={overview:true,request_detail:true,actual_details:workerActualsAfterFinance.length,rendered_detail:true};
    check('Worker views remain financial-data-free after actual accrual and payment'); active=op;
    // A separate nonfinancial request exercises the real worker clarification
    // conflict/review flow without changing the main 5 x 2 / 4 + 3 proof.
    await observe('clarification-before'); active=wp;
    await click(wp,wp.locator('[data-day-action=list]'),'Worker returns to requests for clarification case');
    await click(wp,wp.locator('[data-day-action=new]'),'Worker creates separate clarification request');
    await wp.locator('#worker-day-form [name=people]').waitFor();
    const clarificationWork=setup.work+' · TEST clarification';
    for(const [name,value] of Object.entries({people:'2',days:'1',work:clarificationWork})) await field(wp,name,value,wp.locator('#worker-day-form'));
    const clarificationRequest=await submit(wp,'#worker-day-form','/day-workers/requests','Worker sends clarification-case original');
    const question1='TEST question one: which work should be done?';
    const question2='TEST updated question: confirm the same work and two people';
    const ask=async question=>{
      active=op; await ownerNav(op,'day-request/'+clarificationRequest.id);
      await click(op,op.locator('a[href="#day-decision/'+clarificationRequest.id+'/CLARIFY"]'),'Owner opens clarification question');
      await ownerReady(op,'day-decision/'+clarificationRequest.id+'/CLARIFY');
      await field(op,'reason',question);
      const decision=await submit(op,'[data-form=day-decision]','/requests/'+clarificationRequest.id+'/decisions','Owner sends explicit clarification question');
      await ownerReady(op,'day-request/'+clarificationRequest.id); return decision;
    };
    const firstQuestion=await ask(question1);
    active=wp; await wp.reload(); await wp.locator('#worker-day-open').waitFor();
    await click(wp,wp.locator('#worker-day-open'),'Worker opens current requests');
    await click(wp,wp.locator('[data-day-request="'+clarificationRequest.id+'"]'),'Worker reads original owner question');
    await wp.getByText(question1,{exact:true}).waitFor();
    await click(wp,wp.locator('[data-day-action=clarify]'),'Worker starts clarification answer');
    const answer='TEST worker confirms the same work with two people';
    await field(wp,'text',answer,wp.locator('#worker-day-form'));
    const secondQuestion=await ask(question2);
    active=wp;
    const clarificationPath='/v1/worker/day-workers/requests/'+clarificationRequest.id+'/clarifications';
    const conflict=wp.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname===clarificationPath);
    await click(wp,wp.locator('#worker-day-form button[type=submit]'),'Worker submits answer to now-obsolete question');
    assert.equal((await conflict).status(),409);
    await wp.locator('[name=reviewed_current]').waitFor();
    await wp.getByText(question2,{exact:true}).waitFor();
    assert.equal(await wp.locator('[name=text]').inputValue(),answer);
    await layout(wp,'worker-stale-clarification-review');
    await click(wp,wp.locator('[name=reviewed_current]'),'Worker explicitly reviews updated owner question');
    const clarification=await submit(wp,'#worker-day-form','/requests/'+clarificationRequest.id+'/clarifications','Worker resubmits preserved answer after current review');
    await wp.getByText(answer,{exact:true}).waitFor();
    const answered=await get(oc,dayRoot+'/requests/'+clarificationRequest.id);
    assert.equal(answered.state,'PENDING'); assert.equal(answered.version,4);
    assert.equal(answered.decisions.length,2); assert.equal(answered.clarifications.length,1);
    assert.equal(answered.original.fields.work,clarificationWork);
    assert.deepEqual(answered.decisions.map(d=>d.reason),[question1,question2]);
    assert.equal(answered.clarifications[0].body,answer);
    assertWorkerFinancialPrivacy(await get(wc,'/v1/worker/day-workers/requests/'+clarificationRequest.id));
    result.clarification={request_id:clarificationRequest.id,work:clarificationWork,question_ids:[firstQuestion.id,secondQuestion.id],questions:[question1,question2],answer_id:clarification.id,answer,rejected_status:409,explicit_current_review:true,version:4};
    await observe('clarification-after');
    check('Real worker clarification conflict preserves both owner questions and reviewed answer'); active=op;
    const workerHeaders={Origin:base,'X-Pilot-Participant':credentials[engine].worker.participant_id,'Idempotency-Key':randomUUID()};
    const forbidden=await wc.request.post(base+dayRoot+'/actuals/'+result.actual_ids[0]+'/accruals',{data:{confirmed:true,source:source('day_accrual',{counterparty_id:setup.counterparty_id,daily_rate:setup.rate,currency:'TRY',expense_account_id:setup.expense_account_id,rate_basis:'TEST forbidden worker accrual',due_date:''})},headers:workerHeaders});
    assert.equal(forbidden.status(),403);
    assert.ok([403,404].includes((await xc.request.get(base+'/v1/worker/day-workers/actuals/'+result.actual_ids[0])).status()));
    const savedCookies=await wc.cookies(); await wc.addCookies(await xc.cookies());
    const switched=await wc.request.post(base+'/v1/worker/day-workers/requests',{data:deniedPayload,headers:workerHeaders});
    assert.equal(switched.status(),409); await wc.addCookies(savedCookies);
    const expired=await expiredCookie(credentials[engine].worker.participant_id); await wc.addCookies([{...expired,url:base}]);
    assert.equal((await wc.request.get(base+'/v1/worker/day-workers')).status(),401);
    await wp.reload(); await wp.waitForURL('**/pilot/**');
    check('Worker finance write, foreign actual, participant switch and expired session are fenced');
  }
  await observe('final');
  assert.deepEqual(result.errors,[]); result.status='PASS';
} catch(error) {
  result.status='FAIL'; result.failure=redactPilot(error.stack||error);
  if(active&&!active.isClosed()) await active.screenshot({path:path.join(out,'failure.png'),fullPage:true,mask:[active.locator('#username,#password,#secret,#worker-key,#worker-test-key')]}).catch(()=>{});
  throw Error(result.failure);
} finally {
  result.diagnostics=[];
  for(const item of diagnostics) result.diagnostics.push({name:item.name,...await item.observer.capture()});
  for(const context of contexts) await context.close().catch(()=>{});
  if(browser) await browser.close().catch(()=>{});
  await fs.writeFile(path.join(out,'browser.json'),JSON.stringify(result,null,2));
}
