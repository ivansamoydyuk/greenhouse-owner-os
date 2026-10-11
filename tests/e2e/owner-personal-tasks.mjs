// AUD-003: real UI state actions and reads against the authenticated API and
// disposable PostgreSQL. Fixtures are explicitly synthetic API-confirmed tasks.
// Only transport outage/response-loss timing is injected, never domain results.
import assert from 'node:assert/strict';
import {chromium} from 'playwright';
import fs from 'node:fs/promises';
import {execFileSync} from 'node:child_process';

const base=process.env.PREVIEW_URL||'http://127.0.0.1:4173';
const artifacts=process.env.E2E_ARTIFACTS||'artifacts';
await fs.mkdir(artifacts,{recursive:true});
const browser=await chromium.launch({headless:true,args:['--no-sandbox'],...(process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{})});
const context=await browser.newContext({viewport:{width:390,height:844}}),page=await context.newPage();
const errors=[],report={browser:browser.version(),fixture_method:'Real API capture/revise/explicit confirm; synthetic tasks only',widths:[320,390,768,1280],journeys:[],stateRequests:[]};
page.on('pageerror',event=>errors.push(event.message));
const bounded=async(promise,label)=>{let timer;try{return await Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Timed out: '+label)),15000);})]);}finally{clearTimeout(timer);}};
const waitReady=()=>page.waitForFunction(()=>document.querySelector('#app').getAttribute('aria-busy')==='false');
const waitList=()=>page.waitForFunction(()=>document.querySelector('#personal-task-panel')?.getAttribute('aria-busy')==='false'&&!!document.querySelector('#personal-task-status'));
const read=async path=>{const response=await context.request.get(base+path);assert.equal(response.status(),200,await response.text());return response.json();};
const post=async(path,body,key=crypto.randomUUID())=>{const response=await context.request.post(base+path,{data:body,headers:{'Idempotency-Key':key}});assert.ok([200,201].includes(response.status()),await response.text());return response.json();};
const screenshots=async name=>{
  for(const width of report.widths){
    await page.setViewportSize({width,height:844});
    if(name==='completed-history'){
      await page.locator('#personal-task-detail details .row').last().scrollIntoViewIfNeeded();
      assert.equal(await page.locator('#personal-task-detail details').evaluate(node=>{const bounds=document.querySelector('#sheet').getBoundingClientRect();return [...node.querySelectorAll('.row')].every(row=>{const rect=row.getBoundingClientRect();return rect.top>=bounds.top&&rect.bottom<=bounds.bottom;});}),true,'Both state-history rows visible '+width);
    }
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'Page overflow '+name+' '+width);
    if(await page.locator('#sheet[open]').count())assert.equal(await page.locator('#sheet').evaluate(node=>node.scrollWidth<=node.clientWidth+1),true,'Sheet overflow '+name+' '+width);
    await page.screenshot({path:`${artifacts}/stage-f-${name}-${width}.png`,fullPage:true});
  }
  await page.setViewportSize({width:390,height:844});
};
function database(entity,ids){
  return JSON.parse(execFileSync(process.env.PYTHON||'python',['-c',`import hashlib,json,os,sys
from sqlalchemy import create_engine,text
from greenhouse_owner_os.infrastructure.test_safety import require_disposable_database
url=os.environ['TEST_DATABASE_URL'];require_disposable_database(url,os.environ.get('APP_ENV'))
e=sys.argv[1];ids=json.loads(sys.argv[2]);out={'tasks':{},'domain_counts':{}}
with create_engine(url).connect() as c:
 c.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
 for i in ids:
  task=dict(c.execute(text('SELECT * FROM core.owner_tasks WHERE entity_id=CAST(:e AS uuid) AND id=CAST(:i AS uuid)'),dict(e=e,i=i)).mappings().one())
  actions=[dict(r) for r in c.execute(text('SELECT * FROM core.owner_task_actions WHERE task_id=CAST(:i AS uuid) ORDER BY version'),dict(i=i)).mappings()]
  audit=[dict(r) for r in c.execute(text("SELECT * FROM audit.events WHERE entity_id=CAST(:i AS uuid) AND action='owner_task_state' ORDER BY occurred_at,id"),dict(i=i)).mappings()]
  replay=[dict(r) for r in c.execute(text("SELECT * FROM audit.idempotency_keys WHERE resource_id=CAST(:i AS uuid) AND operation='owner_task_state' ORDER BY created_at,id"),dict(i=i)).mappings()]
  p=str(task['proposal_id'])
  source=c.execute(text('SELECT to_jsonb(s) FROM core.inbox_items s JOIN core.proposed_actions p ON p.inbox_id=s.id WHERE p.id=CAST(:p AS uuid)'),dict(p=p)).scalar_one()
  revisions=c.execute(text('SELECT to_jsonb(r) FROM core.proposal_revisions r WHERE proposal_id=CAST(:p AS uuid) ORDER BY version'),dict(p=p)).scalars().all()
  effect=c.execute(text('SELECT to_jsonb(c) FROM core.confirmation_effects c WHERE proposal_id=CAST(:p AS uuid)'),dict(p=p)).scalar_one()
  immutable={k:v for k,v in task.items() if k not in ('state','version','updated_at')}
  out['tasks'][i]={'row':task,'actions':actions,'audit':audit,'replay':replay,'immutable_sha256':hashlib.sha256(json.dumps([immutable,source,revisions,effect],sort_keys=True,default=str).encode()).hexdigest()}
 for table in ('workers.tasks','workers.assignments','finance.postings','inventory.movements','harvest.worker_reports','harvest.depot_settlements'):
  out['domain_counts'][table]=c.scalar(text('SELECT count(*) FROM '+table+' WHERE entity_id=CAST(:e AS uuid)'),dict(e=e))
print(json.dumps(out,default=str))`,entity,JSON.stringify(ids)],{encoding:'utf8'}));
}

try{
  await page.goto(base);await page.getByLabel('Ключ доступа').fill(process.env.PREVIEW_SECRET);await page.getByRole('button',{name:'Открыть Finance',exact:true}).click();
  await page.getByRole('heading',{name:'Моя теплица',exact:true}).waitFor();await waitReady();
  const config=await read('/preview/config');assert.equal(config.environment,'TEST_PREVIEW');assert.equal(config.actual_operations,false);
  const entity=config.entity_id,root=`/v1/entities/${entity}/owner`,home=await read(root+'/home');
  assert.equal((await read(root+'/tasks')).total,0,'Focused lifecycle requires a fresh personal-task fixture');
  const greenhouse=home.finance.work_areas.find(area=>area.kind==='greenhouse');assert.ok(greenhouse);
  async function createConfirmedTask(n){
    const raw=`Сегодня проверить давление в Имамлы TEST личная задача ${n+1}`;
    const saved=await post(root+'/captures',{client_capture_id:crypto.randomUUID(),captured_at:'2026-10-10T12:00:00+03:00',timezone:'Europe/Istanbul',mode:'text',text:raw});
    let source;const deadline=Date.now()+15000;
    do{source=await read(root+'/captures/'+saved.id);if(source.proposals.length)break;await page.waitForTimeout(50);}while(Date.now()<deadline);
    assert.equal(source.proposals.length,1);let proposal=await read(root+'/confirmations/'+source.proposals[0].id+'/preview');assert.equal(proposal.action_type,'PERSONAL_TASK');
    proposal=await post(root+'/confirmations/'+proposal.id+'/revise',{expected_version:proposal.version,payload:{title:`TEST личная задача ${n+1}`,original_instruction:raw,greenhouse_id:greenhouse.id,planned_date:n<3?'2026-10-10':null,note:`TEST неизменная заметка ${n+1}`,category:'irrigation',relative_time_context:n<3?'сегодня':'следующая неделя'}});
    assert.equal(proposal.state,'READY');
    const confirmed=await post(root+'/confirmations/'+proposal.id+'/confirm',{expected_version:proposal.version,preview_digest:proposal.revision.preview_digest,confirmed:true});return confirmed.effect;
  }
  const tasks=[];for(let n=0;n<5;n++)tasks.push(await createConfirmedTask(n));
  const ids=tasks.map(task=>task.id),before=database(entity,ids);report.fixtureTasks=tasks;report.before=before;
  await page.locator('#refresh').click();await waitReady();assert.equal(await page.locator('[data-owner-done]').count(),3);assert.equal((await read(root+'/home')).task_count,5);await screenshots('home-three');
  await page.locator('#owner-all-tasks').click();await page.getByRole('heading',{name:'Личные задачи',exact:true}).waitFor();await waitList();
  assert.equal(await page.locator('[data-personal-open]').count(),5);assert.match(await page.locator('#app').innerText(),/следующая неделя/);await screenshots('active-five');
  await page.locator(`[data-personal-open="${ids[3]}"]`).click();await page.locator('#personal-task-detail').waitFor();
  assert.match(await page.locator('#sheet').innerText(),/Без даты/);assert.match(await page.locator('#sheet').innerText(),/Исходная формулировка/);assert.match(await page.locator('#sheet').innerText(),/TEST неизменная заметка 4/);
  assert.equal(await page.locator('#sheet input,#sheet textarea,#sheet select').count(),0);await screenshots('fourth-undated');
  await page.keyboard.press('Shift+Tab');assert.equal(await page.evaluate(()=>document.querySelector('#sheet').contains(document.activeElement)),true);await page.keyboard.press('Tab');assert.equal(await page.locator('#sheet .close').evaluate(node=>node===document.activeElement),true);
  await page.locator('#personal-task-change').click();await page.locator('#sheet').waitFor({state:'hidden'});await waitReady();await waitList();
  const done=database(entity,ids);assert.equal(done.tasks[ids[3]].row.state,'DONE');assert.equal(done.tasks[ids[3]].row.version,2);assert.deepEqual(ids.slice(0,3).map(id=>done.tasks[id].row.state),['TO_DO','TO_DO','TO_DO']);assert.equal(await page.locator('#personal-task-status').evaluate(node=>node===document.activeElement),true);
  await page.reload();await page.getByRole('heading',{name:'Личные задачи',exact:true}).waitFor();await waitReady();await waitList();assert.equal(await page.locator(`[data-personal-open="${ids[3]}"]`).count(),0);
  await page.locator('#personal-completed').click();await waitList();await page.locator(`[data-personal-open="${ids[3]}"]`).click();await page.locator('#personal-task-detail').waitFor();
  assert.match(await page.locator('#personal-task-change').innerText(),/Вернуть в активные/);await page.getByText('История состояния · 2',{exact:true}).click();assert.match(await page.locator('#sheet').innerText(),/Версия 1/);assert.match(await page.locator('#sheet').innerText(),/Версия 2/);await screenshots('completed-history');
  await page.locator('#personal-task-change').click();await page.locator('#sheet').waitFor({state:'hidden'});await waitReady();await waitList();assert.equal(await page.locator('#personal-task-status').evaluate(node=>node===document.activeElement),true);
  await page.locator('#personal-active').click();await waitList();await page.reload();await waitReady();await waitList();assert.equal(await page.locator(`[data-personal-open="${ids[3]}"]`).count(),1);
  report.afterFourthRestore=database(entity,ids);assert.equal(report.afterFourthRestore.tasks[ids[3]].row.version,3);assert.deepEqual(report.afterFourthRestore.tasks[ids[3]].actions.map(a=>[a.version,a.state]),[[1,'TO_DO'],[2,'DONE'],[3,'TO_DO']]);report.journeys.push('Home3 → all5 → fourth undated → DONE → reload → completed history → explicit restore → reload');

  let lose=true;
  await page.route(`**/owner/tasks/${ids[4]}/state`,async route=>{const response=await route.fetch();report.stateRequests.push({id:ids[4],body:route.request().postDataJSON(),key:route.request().headers()['idempotency-key'],status:response.status()});if(lose){lose=false;assert.equal(response.status(),200);return route.abort('failed');}return route.fulfill({response});});
  await page.locator(`[data-personal-open="${ids[4]}"]`).click();await page.locator('#personal-task-detail').waitFor();await page.locator('#personal-task-change').click();await page.locator('#personal-task-error').filter({hasText:/Связь прервалась/}).waitFor();
  const lost=database(entity,ids);assert.equal(lost.tasks[ids[4]].row.version,2);assert.equal(lost.tasks[ids[4]].actions.length,2);await page.locator('#personal-task-change').click();await page.locator('#sheet').waitFor({state:'hidden'});await waitReady();await waitList();
  const replayed=database(entity,ids);assert.deepEqual(replayed.tasks[ids[4]],lost.tasks[ids[4]]);assert.equal(report.stateRequests.length,2);assert.equal(report.stateRequests[0].key,report.stateRequests[1].key);assert.deepEqual(report.stateRequests[0].body,report.stateRequests[1].body);report.journeys.push('response-loss retry keeps same key/body and one DB transition/audit/replay');await page.unroute(`**/owner/tasks/${ids[4]}/state`);

  await page.locator(`[data-personal-open="${ids[0]}"]`).click();await page.locator('#personal-task-detail').waitFor();await post(root+`/tasks/${ids[0]}/state`,{expected_version:1,state:'DONE'});
  const concurrent=database(entity,ids);await page.locator('#personal-task-change').click();await page.locator('#personal-task-error').filter({hasText:/Задача изменилась/}).waitFor();assert.equal(await page.locator('#personal-task-change').isDisabled(),true);assert.deepEqual(database(entity,ids).tasks[ids[0]],concurrent.tasks[ids[0]]);
  await page.locator('#personal-task-refresh').click();await page.locator('#personal-task-change').filter({hasText:/Вернуть в активные/}).waitFor();await page.locator('#personal-task-change').click();await page.locator('#sheet').waitFor({state:'hidden'});await waitReady();await waitList();report.journeys.push('stale version denied without new audit; refresh precedes explicit restore');

  let unavailable=true;
  await page.route('**/owner/tasks?**',async route=>{if(unavailable){const response=await route.fetch();assert.equal(response.status(),200);return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:{message:'TEST personal list outage'}})});}return route.continue();});
  await page.locator('#personal-completed').click();await page.getByRole('heading',{name:'Задачи не загружены',exact:true}).waitFor();assert.equal(await page.locator('#login').count(),0);assert.equal(await page.locator('[data-personal-open]').count(),0);assert.equal(await page.locator('#personal-list-retry').evaluate(node=>node===document.activeElement),true);await screenshots('list-unavailable');unavailable=false;await page.locator('#personal-list-retry').click();await waitList();assert.equal(await page.locator('#personal-completed').getAttribute('aria-pressed'),'true');await page.unroute('**/owner/tasks?**');
  await page.locator('#nav [data-page=home]').click();await page.getByRole('heading',{name:'Моя теплица',exact:true}).waitFor();await waitReady();await page.goBack();await page.getByRole('heading',{name:'Личные задачи',exact:true}).waitFor();await waitList();assert.equal(await page.locator('#personal-completed').getAttribute('aria-pressed'),'true');await page.goForward();await page.getByRole('heading',{name:'Моя теплица',exact:true}).waitFor();await waitReady();await page.goBack();await page.getByRole('heading',{name:'Личные задачи',exact:true}).waitFor();await waitList();report.journeys.push('service failure/retry and Back/Forward retain completed filter without login or false empty state');
  await page.locator(`[data-personal-open="${ids[4]}"]`).click();await page.locator('#personal-task-detail').waitFor();await page.getByRole('button',{name:'Закрыть',exact:true}).click();assert.equal(await page.locator(`[data-personal-open="${ids[4]}"]`).evaluate(node=>node===document.activeElement),true);await page.locator(`[data-personal-open="${ids[4]}"]`).click();await page.locator('#personal-task-detail').waitFor();assert.match(await page.locator('#sheet').innerText(),/TEST original|TEST личная задача 5/);await page.getByRole('button',{name:'Закрыть',exact:true}).click();
  await page.locator('#personal-active').click();await waitList();let release;const held=new Promise(resolve=>release=resolve);let requested;const seen=new Promise(resolve=>requested=resolve);
  await page.route(`**/owner/tasks/${ids[1]}`,async route=>{const response=await route.fetch();requested();await held;return route.fulfill({response});});
  await page.locator(`[data-personal-open="${ids[1]}"]`).click();await bounded(seen,'held personal detail reached real transport');await page.getByRole('button',{name:'Закрыть',exact:true}).click();const responseArrived=page.waitForResponse(response=>response.url().endsWith('/tasks/'+ids[1]));release();await responseArrived;await page.waitForTimeout(200);assert.equal(await page.locator('#sheet').evaluate(node=>node.open),false);await page.unroute(`**/owner/tasks/${ids[1]}`);report.journeys.push('closed detail remains closed after a delayed real response; close/reopen preserves focus');
  // Exercise the real 20-row boundary without consuming any earlier task. All
  // additional fixtures still use the ordinary explicit confirmation pipeline.
  const pageFixtures=[];for(let n=5;n<22;n++)pageFixtures.push(await createConfirmedTask(n));
  report.paginationFixtures=pageFixtures;
  await page.locator('#refresh').click();await waitReady();await waitList();
  const firstPage=await page.locator('[data-personal-open]').evaluateAll(nodes=>nodes.map(node=>node.dataset.personalOpen));assert.equal(firstPage.length,20);assert.match(await page.locator('#personal-task-status').innerText(),/1–20 из 21/);
  await page.locator('#personal-next').click();await waitList();const lastId=pageFixtures.at(-1).id;
  assert.deepEqual(await page.locator('[data-personal-open]').evaluateAll(nodes=>nodes.map(node=>node.dataset.personalOpen)),[lastId]);assert.equal(await page.locator('#personal-task-status').evaluate(node=>node===document.activeElement),true);
  await page.reload();await waitReady();await waitList();assert.equal(await page.locator(`[data-personal-open="${lastId}"]`).count(),1);assert.match(await page.locator('#personal-task-status').innerText(),/21–21 из 21/);
  await page.locator(`[data-personal-open="${lastId}"]`).click();await page.locator('#personal-task-detail').waitFor();assert.match(await page.locator('#sheet').innerText(),/TEST личная задача 22/);await page.getByRole('button',{name:'Закрыть',exact:true}).click();await screenshots('second-page');
  await page.locator('#personal-prev').click();await waitList();assert.deepEqual(await page.locator('[data-personal-open]').evaluateAll(nodes=>nodes.map(node=>node.dataset.personalOpen)),firstPage);report.paginationRows=database(entity,pageFixtures.map(task=>task.id));assert.ok(Object.values(report.paginationRows.tasks).every(task=>task.row.state==='TO_DO'&&task.row.version===1&&task.actions.length===1&&task.audit.length===0));report.journeys.push('real 21 active tasks paginate 20+1, retained second page after reload, detail, previous page; no completion needed');
  // A replay acknowledges history, not necessarily the current state. Exercise
  // both directions with real commits by a second API client before UI retry.
  report.delayedReplay=[];
  for(const [index,initial] of [[22,'TO_DO'],[23,'DONE']]){
    const fixture=await createConfirmedTask(index),id=fixture.id;
    if(initial==='DONE')await post(root+`/tasks/${id}/state`,{expected_version:1,state:'DONE'});
    const version=initial==='TO_DO'?1:2,requests=[];let loseReply=true;
    await page.locator('#refresh').click();await waitReady();await waitList();await page.locator(initial==='TO_DO'?'#personal-active':'#personal-completed').click();await waitList();
    while(!await page.locator(`[data-personal-open="${id}"]`).count()&&await page.locator('#personal-next').count()){await page.locator('#personal-next').click();await waitList();}
    await page.route(`**/owner/tasks/${id}/state`,async route=>{const response=await route.fetch();requests.push({body:route.request().postDataJSON(),key:route.request().headers()['idempotency-key'],status:response.status()});assert.equal(response.status(),200);if(loseReply){loseReply=false;return route.abort('failed');}return route.fulfill({response});});
    await page.locator(`[data-personal-open="${id}"]`).click();await page.locator('#personal-task-detail').waitFor();await page.locator('#personal-task-change').click();await page.locator('#personal-task-error').filter({hasText:/Связь прервалась/}).waitFor();
    const lost=database(entity,[id]);assert.equal(lost.tasks[id].row.version,version+1);
    await post(root+`/tasks/${id}/state`,{expected_version:version+1,state:initial});const authoritative=database(entity,[id]);
    await page.locator('#personal-task-change').click();await page.locator('#sheet').waitFor({state:'hidden'});await waitReady();await waitList();
    assert.deepEqual(database(entity,[id]),authoritative);assert.equal(authoritative.tasks[id].row.state,initial);assert.equal(authoritative.tasks[id].row.version,version+2);
    assert.equal((await read(root+`/tasks/${id}`)).state,initial);assert.equal(await page.locator('#notice').innerText(),'Действие сохранено в истории.');
    assert.equal(requests.length,2);assert.deepEqual(requests[0].body,requests[1].body);assert.equal(requests[0].key,requests[1].key);
    report.delayedReplay.push({initial,fixture,lost,authoritative,requests});await page.unroute(`**/owner/tasks/${id}/state`);
  }
  report.journeys.push('delayed DONE and restore replays preserve later authoritative state, add no DB effects, and announce only saved history');
  await page.locator('#personal-active').click();await waitList();
  await screenshots('final-active');
  report.after=database(entity,ids);assert.deepEqual(report.after.domain_counts,before.domain_counts);for(const id of ids)assert.equal(report.after.tasks[id].immutable_sha256,before.tasks[id].immutable_sha256,id);
  assert.equal(report.after.tasks[ids[3]].row.version,3);assert.equal(report.after.tasks[ids[3]].audit.length,2);assert.equal(report.after.tasks[ids[3]].replay.length,2);assert.equal(report.after.tasks[ids[4]].row.version,2);assert.equal(report.after.tasks[ids[4]].audit.length,1);assert.equal(report.after.tasks[ids[4]].replay.length,1);
  assert.deepEqual(errors,[]);report.errors=errors;report.passed=true;await fs.writeFile(artifacts+'/stage-f-browser-report.json',JSON.stringify(report,null,2));console.log('PASS personal tasks real browser/API/PostgreSQL: all5, fourth DONE/restore, reload/history, retry/stale/focus/navigation and immutable domain isolation');
}catch(error){report.passed=false;report.error=error.message;report.errors=errors;await fs.writeFile(artifacts+'/stage-f-browser-report.json',JSON.stringify(report,null,2));await page.screenshot({path:artifacts+'/stage-f-failure.png',fullPage:true}).catch(()=>{});throw error;}finally{await browser.close();}
