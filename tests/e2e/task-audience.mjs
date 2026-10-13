/** Actual authenticated Connected task-audience controls. Approved CI route only.
 * Every source, choice, revision and confirmation uses real browser controls.
 * Read-only API/SQL observations do not replace UI actions. One post-commit
 * response is deliberately lost; its retained route forwards all later traffic.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {chromium,webkit} from 'playwright';
import {ownerLogin,pilotMode,redactPilot,resetFixtureRateLimits} from './helpers/pilot-auth.mjs';
import {installClickAudit} from './friction-metrics.mjs';
import {observeBrowserDiagnostics} from './helpers/browser-diagnostics.mjs';

const base=process.env.PREVIEW_URL||'http://127.0.0.1:4173',out=process.env.E2E_ARTIFACTS;
const engine=process.env.TASK_AUDIENCE_ENGINE,sha=process.env.OWNER_PREVIEW_FEATURE_SHA;
assert.ok(['chromium','webkit'].includes(engine));assert.equal(pilotMode,true);
assert.ok(['127.0.0.1','localhost'].includes(new URL(base).hostname));assert.ok(out);assert.match(sha||'',/^[0-9a-f]{40}$/);
const setup=JSON.parse(await fs.readFile(path.join(out,'setup.json'),'utf8'));
assert.equal(setup.sha,sha);assert.equal(setup.engine,engine);assert.equal(setup.synthetic_only,true);
const root='/v1/entities/'+setup.entity_id;
const result={primary_actions:[],sha,engine,status:'RUNNING',entity_id:setup.entity_id,transport:'actual_browser_controls',scope:'Connected source audience clarification and explicit task confirmation',cases:[],operations:[],screenshots:[],layouts:[],errors:[],limitations:['Primary-action geometry is measured at320px; four-width screenshots and layout checks are separate evidence.','Physical iPhone/native Safari and human-reviewed translations are not exercised. Worker confirmation creates an unassigned not-sent draft, never dispatches it.']};
const write=()=>fs.writeFile(path.join(out,'browser.json'),JSON.stringify(result,null,2)+'\n');
const digest=text=>createHash('sha256').update(text).digest('hex');
await fs.mkdir(out,{recursive:true});await resetFixtureRateLimits();
const browser=await ({chromium,webkit}[engine]).launch({timeout:30000});
let activePage;
try {
 for(const fixture of setup.cases) {
  const c={...fixture,status:'RUNNING',stage:'login',stages:{},edits:[]};result.cases.push(c);await write();
  const context=await browser.newContext({viewport:{width:390,height:844}}),page=await context.newPage();activePage=page;
  page.setDefaultTimeout(20000);page.setDefaultNavigationTimeout(25000);await installClickAudit(page);
  const diagnostics=await observeBrowserDiagnostics(page);
  page.on('pageerror',error=>result.errors.push({case_id:c.id,message:redactPilot(error.message)}));
  const stage=async name=>{c.stage=name;await write();};
  const ready=route=>page.waitForFunction(r=>location.hash==='#'+r&&document.querySelector('#main')?.dataset.renderedRoute===r,route);
  const read=async url=>{const response=await context.request.get(base+url);assert.equal(response.status(),200,url+' '+await response.text());return response.json();};
  const preview=id=>read(root+'/owner/confirmations/'+id+'/preview');
  const click=async(locator,label)=>{
   const count=await page.evaluate(()=>window.ownerFrictionClicks.length);
   await locator.click();
   const events=await page.evaluate(n=>window.ownerFrictionClicks.slice(n),count);
   assert.ok(events.some(e=>e.trusted),'Expected actual trusted browser click: '+label);
   result.operations.push({case_id:c.id,kind:'click',label,trusted:true});
  };
  const reveal=async locator=>{const parents=locator.locator('xpath=ancestor::details');for(let i=0;i<await parents.count();i++){const parent=parents.nth(i);if(!await parent.evaluate(n=>n.open))await click(parent.locator(':scope > summary'),'Open relevant details');}};
  const probe=async label=>{
   const filename=engine+'-'+c.id+'-'+label+'.json';
   execFileSync(process.env.PYTHON||'python',['scripts/task_audience_sql_evidence.py','--probe','--output',path.join(out,filename)],{env:process.env,stdio:['ignore','pipe','pipe'],timeout:30000});
   c.stages[label]=filename;return JSON.parse(await fs.readFile(path.join(out,filename),'utf8'));
  };
  const layout=async label=>{
   for(const width of [320,390,768,1280]){
    await page.setViewportSize({width,height:844});await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    const bounds=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}));assert.ok(bounds.scroll<=bounds.width+1,label+' overflow');
    const clipped=await page.locator('button:visible,input:visible,textarea:visible,select:visible').evaluateAll(nodes=>nodes.filter(n=>n.getBoundingClientRect().left< -1||n.getBoundingClientRect().right>innerWidth+1).map(n=>({tag:n.tagName,name:n.name})));assert.deepEqual(clipped,[]);
    const filename=engine+'-'+c.id+'-'+label+'-'+width+'.png';await page.screenshot({path:path.join(out,filename),fullPage:true});result.screenshots.push(filename);result.layouts.push({case_id:c.id,label,...bounds});
   }
   await page.setViewportSize({width:390,height:844});
  };
  const primary=async(locator,label)=>{
   const original=page.viewportSize();await page.setViewportSize({width:320,height:568});
   await locator.evaluate(n=>n.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'}));
   await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
   const evidence=await locator.evaluate(n=>{const r=n.getBoundingClientRect();return {width:innerWidth,height:innerHeight,rect:{left:r.left,top:r.top,right:r.right,bottom:r.bottom,width:r.width,height:r.height},enabled:!n.matches(':disabled'),hits:[[.5,.5],[.25,.25],[.75,.25],[.25,.75],[.75,.75]].map(([x,y])=>n.contains(document.elementFromPoint(r.left+r.width*x,r.top+r.height*y)))};});
   result.primary_actions.push({case_id:c.id,label,...evidence});const filename=engine+'-'+c.id+'-'+label+'-320-viewport.png';await page.screenshot({path:path.join(out,filename),fullPage:false});result.screenshots.push(filename);
   const r=evidence.rect;assert.ok(evidence.enabled&&r.width>=44&&r.height>=44&&r.left>=0&&r.top>=0&&r.right<=320&&r.bottom<=568&&evidence.hits.every(Boolean),'Primary control obscured or outside 320px viewport: '+label);
   await page.setViewportSize(original);
  };
  const edit=async(key,value)=>{
   const button=page.locator('[data-action="edit:'+key+'"]').first();await reveal(button);await click(button,'Review '+key);
   const field=page.locator('#sheet-root [name=value]');await field.waitFor();const tag=await field.evaluate(n=>n.tagName);
   if(tag==='SELECT')await field.selectOption(String(value));else await field.fill(String(value));assert.equal(await field.inputValue(),String(value));
   const oldMain=await page.locator('#main').elementHandle();
   const saved=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/owner/confirmations/'+c.proposal_id+'/revise'));
   await click(page.locator('#sheet-root button[type=submit]'),'Save reviewed '+key);const response=await saved;assert.equal(response.status(),200,await response.text());const p=await response.json();
   assert.equal(String(p.revision.payload[key]),String(value));await page.locator('#sheet-root [role=dialog]').waitFor({state:'hidden'});
   await page.waitForFunction(node=>!node.isConnected,oldMain);await ready('review/'+c.proposal_id);await oldMain.dispose();
   c.edits.push({key,value,version:p.version,actual_dom_replaced:true});return p;
  };
  let retainedRoute;
  try {
   await ownerLogin(page,base);await ready('home');const config=await read('/preview/config');assert.equal(config.feature_sha,sha);assert.equal(config.entity_id,setup.entity_id);assert.equal(config.daily_pilot,true);
   const before=await probe('before');
   await stage('capture_original');await click(page.locator('[data-testid=radial-toggle]'),'Open Capture');await click(page.locator('[data-testid=capture-text]'),'Choose source text');await ready('capture/text');
   await page.locator('[name=text]').fill(c.text);assert.equal(await page.locator('[name=text]').inputValue(),c.text);
   await click(page.locator('[data-form=capture] button[type=submit]'),'Save exact original');
   await page.waitForURL(url=>url.hash.startsWith('#source/'));c.source_id=page.url().split('#source/')[1];await ready('source/'+c.source_id);
   let source;const deadline=Date.now()+150000;
   do{source=await read(root+'/owner/captures/'+c.source_id);if(source.job?.state==='FAILED')throw Error('Source preparation failed: '+source.job.error_code);if(source.job?.state==='SUCCEEDED')break;await page.waitForTimeout(500);}while(Date.now()<deadline);
   assert.equal(source.job?.state,'SUCCEEDED');assert.equal(source.text,c.text);assert.equal(source.proposals.length,1);c.origin_id=source.proposals[0].id;
   const initial=await preview(c.origin_id);c.initial_type=initial.action_type;c.initial_version=initial.version;c.initial_payload=initial.revision.payload;
   const ambiguous=c.id.startsWith('EXCERPT')||c.id==='KNOWN_FACTS';assert.equal(initial.action_type,ambiguous?'UNRESOLVED':'PERSONAL_TASK');
   const facts=ambiguous?initial.revision.payload.task_candidates:initial.revision.payload;assert.equal(facts.original_instruction,c.text);assert.equal(facts.title,c.text.slice(0,200));
   if(ambiguous){assert.equal(facts.worker_id,null);assert.ok(initial.revision.missing.includes('Для себя или работнику?'));}
   if(c.id==='KNOWN_FACTS'||c.id==='EXPLICIT_PERSONAL'){assert.equal(facts.greenhouse_id,setup.greenhouse_id);assert.match(facts.planned_date,/^\d{4}-\d{2}-\d{2}$/);}
   assert.deepEqual(await probe('prepared'),before);
   const link=page.locator('a[href="#review/'+c.origin_id+'"]').first();if(!await link.count())await click(page.locator('[data-action=refresh]'),'Refresh completed source preparation');await link.waitFor();await click(link,'Open prepared task');await ready('review/'+c.origin_id);
   assert.equal(await page.locator('[data-testid=source-original]').textContent(),c.text);
   c.proposal_id=c.origin_id;
   if(c.audience){
    await stage('audience_clarification');const form=page.locator('[data-form=task-audience]');await reveal(form);
    assert.equal(await form.locator('[name=audience]').inputValue(),'');
    if(ambiguous){await reveal(page.locator('.task-audience-summary .source-text'));assert.equal(await page.locator('.task-audience-summary .source-text').isVisible(),true);assert.equal(await page.locator('.task-audience-summary .source-text').textContent(),c.text);assert.equal(await page.locator('[data-action=confirm-proposal]:not(:disabled)').count(),0);await layout('unresolved-audience');}
    await form.locator('[name=audience]').selectOption(c.audience);assert.equal(await form.locator('[name=audience]').inputValue(),c.audience);
    const choiceButton=form.locator('button[type=submit]');await primary(choiceButton,'choose-audience');let target;
    if(c.id==='KNOWN_FACTS'){
     const pattern='**/owner/captures/'+c.source_id+'/task-recovery';let commits=0;
     const handler=async route=>{if(route.request().method()!=='POST'||commits)return route.continue();const response=await route.fetch();assert.equal(response.status(),200);target=await response.json();commits++;await route.abort('failed');};
     await page.route(pattern,handler);retainedRoute=()=>page.unroute(pattern,handler);
     await click(choiceButton,'Choose audience; lose actual committed preparation reply');await form.locator('.form-error').filter({hasText:'Ответ сервера не получен'}).waitFor();assert.equal(commits,1);assert.ok(target?.id);
     assert.equal(await form.locator('[name=audience]').isDisabled(),true);assert.equal(await form.locator('[name=audience]').inputValue(),c.audience);
     assert.deepEqual(await probe('audience-lost'),before);
     await page.reload();await ready('review/'+c.origin_id);assert.equal(await page.locator('[data-form=task-audience]').count(),0);
     await click(page.locator('a[href="#review/'+target.id+'"]'),'Recover selected audience after reload');await ready('review/'+target.id);
     await retainedRoute();retainedRoute=null;c.audience_lost_reply_reload_same_target=true;
    }else{
     const reply=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/owner/captures/'+c.source_id+'/task-recovery'));
     await click(choiceButton,'Choose '+c.audience+' and open ordinary review');const response=await reply;assert.equal(response.status(),200,await response.text());target=await response.json();
    }
    c.proposal_id=target.id;assert.notEqual(target.id,c.origin_id);await ready('review/'+target.id);
    assert.equal(target.action_type,c.audience==='worker'?'WORKER_TASK':'PERSONAL_TASK');
    if(c.id==='KNOWN_FACTS')assert.deepEqual(target.revision.missing.filter(message=>!message.startsWith('Возможный повтор источника:')),['Работник']);
    for(const key of ['title','original_instruction','greenhouse_id','planned_date','category'])if(key in facts)assert.deepEqual(target.revision.payload[key],facts[key],key+' changed during audience choice');
   }
   await stage('ordinary_review');let p=await preview(c.proposal_id);assert.equal(p.revision.payload.original_instruction,c.text);
   if(c.audience==='worker'){
    assert.equal(p.revision.payload.worker_id,null);assert.equal(await page.locator('[data-action=confirm-proposal]').isDisabled(),true);
    if(!p.revision.payload.greenhouse_id)p=await edit('greenhouse_id',setup.greenhouse_id);
    if(!p.revision.payload.planned_date)p=await edit('planned_date','2026-10-13');
    p=await edit('worker_id',setup.worker_id);assert.equal(p.revision.payload.language,'ru');assert.equal(p.revision.payload.original_instruction,c.text);
   }
   p=await preview(c.proposal_id);
   if(p.revision.missing.some(message=>message.startsWith('Возможный повтор источника:'))){
    assert.ok(p.revision.missing.every(message=>message.startsWith('Возможный повтор источника:')),'Unknown missing fact must not be cleared by duplicate-source review');
    c.duplicate_source_review=p.revision.missing;
    p=await edit('note','TEST independent '+engine+' browser case; original checked explicitly');
   }
   if(c.id==='KNOWN_FACTS')assert.deepEqual(c.edits.map(edit=>edit.key),['worker_id'],'Known date/site must not need repeated entry');
   p=await preview(c.proposal_id);c.reviewed_payload=p.revision.payload;assert.deepEqual(p.revision.missing,[]);assert.equal(p.revision.payload.title,c.text.slice(0,200));assert.equal(p.revision.payload.original_instruction,c.text);
   assert.deepEqual(await probe('ready'),before);await reveal(page.locator('[data-testid=source-original]'));assert.equal(await page.locator('[data-testid=source-original]').isVisible(),true);await layout('ready-review');
   await stage('explicit_confirmation');const button=page.locator('[data-action=confirm-proposal]');assert.equal(await button.isEnabled(),true);await primary(button,'confirm-task');
   if(c.id==='EXCERPT_1'){
    let committed=false;const attempts=[];const pattern='**/owner/confirmations/'+c.proposal_id+'/confirm';
    const handler=async route=>{
     if(route.request().method()!=='POST')return route.continue();
     attempts.push({key:route.request().headers()['idempotency-key'],body:route.request().postDataJSON()});
     if(!committed){const response=await route.fetch();assert.equal(response.status(),200);c.effect=(await response.json()).effect;committed=true;await route.abort('failed');}else await route.continue();
    };
    await page.route(pattern,handler);retainedRoute=()=>page.unroute(pattern,handler);
    await click(button,'Confirm after review; lose real committed reply');await page.locator('#action-error').filter({hasText:'Ответ сервера не получен'}).waitFor();assert.ok(committed);await probe('lost');
    const replay=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/owner/confirmations/'+c.proposal_id+'/confirm'));
    await click(button,'Retry the same explicit confirmation');const reply=await replay;assert.equal(reply.status(),200,await reply.text());assert.deepEqual((await reply.json()).effect,c.effect);
    assert.equal(attempts.length,2);assert.deepEqual(attempts[0],attempts[1]);c.replay_same_command=true;
   }else{
    const reply=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/owner/confirmations/'+c.proposal_id+'/confirm'));
    await click(button,'Explicitly confirm reviewed task');const response=await reply;assert.equal(response.status(),200,await response.text());c.effect=(await response.json()).effect;
   }
   await page.getByText('Сервер подтвердил действие. Оригинал и версии сохранены.',{exact:true}).waitFor();c.task_id=c.effect.id;
   p=await preview(c.proposal_id);assert.equal(p.confirmed_effect.effect.id,c.task_id);assert.equal(p.state,c.audience==='worker'?'CONFIRMED_NONPOSTING':'POSTED');
   if(c.audience==='worker'){assert.equal(c.effect.not_sent,true);assert.equal(c.effect.status,'draft');}
   const confirmed=await probe('confirmed');
   if(retainedRoute){await retainedRoute();retainedRoute=null;}
   await page.reload();await ready('review/'+c.proposal_id);await page.getByText('Сервер подтвердил действие. Оригинал и версии сохранены.',{exact:true}).waitFor();
   assert.equal(await page.locator('[data-action=confirm-proposal]').count(),0);await layout('confirmed-task');
   if(c.audience){
    await page.goto(base+'/r3/connected.html#review/'+c.origin_id);await ready('review/'+c.origin_id);
    assert.equal(await page.locator('[data-form=task-audience]').count(),0);const targetLink=page.locator('a[href="#review/'+c.proposal_id+'"]');await targetLink.waitFor();await click(targetLink,'Reopen the same selected review');await ready('review/'+c.proposal_id);
   }
   assert.deepEqual(await probe('reloaded'),confirmed);
   const original=await context.request.get(base+root+'/owner/captures/'+c.source_id+'/original');assert.equal(original.status(),200);assert.deepEqual(await original.body(),Buffer.from(c.text));c.original_sha256=digest(await original.body());
   const retained=await preview(c.origin_id);assert.equal(retained.action_type,c.initial_type);assert.deepEqual(retained.revision.payload,c.origin_id===c.proposal_id?c.reviewed_payload:c.initial_payload);
   c.status='PASS';c.stage='complete';
  }catch(error){c.status='FAIL';c.failure={stage:c.stage,message:redactPilot(error.message)};throw error;}
  finally{
   if(c.status!=='PASS'&&!page.url().includes('/pilot/'))await page.screenshot({path:path.join(out,engine+'-'+c.id+'-failure.png'),fullPage:true}).catch(()=>{});
   if(retainedRoute)await retainedRoute();
   const captured=await diagnostics.capture();await fs.writeFile(path.join(out,engine+'-'+c.id+'-diagnostics.json'),JSON.stringify(captured,null,2)+'\n');
   await context.close();await write();
  }
 }
 assert.deepEqual(result.errors,[]);result.status='PASS';await write();
}catch(error){result.status='FAIL';result.failure=redactPilot(error.stack||error.message);await write();process.exitCode=1;}
finally{await browser.close();}
console.log('TASK_AUDIENCE_BROWSER_'+result.status+' '+engine);
