// AUD-004/005: actual authenticated browser -> API -> disposable PostgreSQL.
// Only outage/lost-response transport faults are injected. Capture validation,
// persistence, originals and idempotent replay run through the real server.
import assert from 'node:assert/strict';
import {chromium} from 'playwright';
import fs from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
const base=process.env.PREVIEW_URL||'http://127.0.0.1:4173';
const artifacts=process.env.E2E_ARTIFACTS||'artifacts';
await fs.mkdir(artifacts,{recursive:true});
const browser=await chromium.launch({headless:true,args:['--no-sandbox'],...(process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{})});
const context=await browser.newContext({viewport:{width:390,height:844},acceptDownloads:true});
const page=await context.newPage(),errors=[],transport=[],report={browser:browser.version(),actions:[]};page.on('pageerror',e=>errors.push(e.message));
const records=()=>page.evaluate(async()=>{const {pendingStore}=await import('/assets/owner-r2.js'),db=await pendingStore();return new Promise((resolve,reject)=>{const tx=db.transaction('sources'),request=tx.objectStore('sources').getAll();request.onsuccess=()=>resolve(request.result.sort((a,b)=>a.body.captured_at.localeCompare(b.body.captured_at)||a.key.localeCompare(b.key)));request.onerror=reject;tx.oncomplete=()=>db.close();});});
// Playwright 1.61 waitForFunction treats an async predicate's Promise as
// truthy before its value. Poll awaited read-only IndexedDB snapshots in Node.
const waitForRecords=async predicate=>{const deadline=Date.now()+30000;let latest;while(Date.now()<deadline){latest=await records();if(predicate(latest))return latest;await page.waitForTimeout(100);}assert.fail('Timed out waiting for local queue: '+JSON.stringify(latest));};
const screenshot=async name=>{for(const width of [390,320]){await page.setViewportSize({width,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);await page.screenshot({path:`${artifacts}/${name}-${width}.png`,fullPage:true});}await page.setViewportSize({width:390,height:844});};
const openLocal=async(selector='#owner-local')=>{await page.locator(selector).click();await page.getByRole('heading',{name:'Ещё не в системе',exact:true}).waitFor();await page.locator('[data-local-export="0"]').waitFor();};
const waitReady=()=>page.waitForFunction(()=>document.querySelector('#app').getAttribute('aria-busy')==='false');
const read=async path=>{const response=await context.request.get(base+path);assert.equal(response.status(),200);return response.json();};
try{
 await page.goto(base);await page.getByLabel('Ключ доступа').waitFor();report.cold401=true;
 let inventoryUnavailable=true;
 await page.route('**/inventory',async route=>{if(inventoryUnavailable){const actual=await route.fetch();assert.equal(actual.status(),200);return route.fulfill({status:503,contentType:'text/plain',body:'TEST injected Inventory outage'});}return route.continue();});
 await page.getByLabel('Ключ доступа').fill(process.env.PREVIEW_SECRET);await page.getByRole('button',{name:'Открыть Finance',exact:true}).click();
 await page.locator('[data-load-state=unavailable]').waitFor();await waitReady();assert.equal(await page.locator('#login').count(),0);assert.match(await page.locator('#app').innerText(),/Склад.*503/);assert.equal(await page.locator('.finance-kpis,.owner-kpi').count(),0);
 assert.equal((await context.request.get(base+'/preview/config')).status(),200);await screenshot('stage-d-cold-inventory503');report.cold200Inventory503={loginShown:false,balancesShown:false,sessionRetained:true};
 inventoryUnavailable=false;await page.locator('#load-retry').click();await page.getByRole('heading',{name:'Моя теплица',exact:true}).waitFor();await waitReady();report.recoveredWithoutNewKey=true;
 const config=await read('/preview/config');assert.equal(config.actual_operations,false);assert.equal(config.environment,'TEST_PREVIEW');report.environment=config.environment;const entity=config.entity_id,home=await read(`/v1/entities/${entity}/owner/home`),scope=entity+':'+home.owner_id,root=`/v1/entities/${entity}/owner`;
 // Capture all originals through the actual visible forms while offline. Read
 // IndexedDB only for proof; never seed or mutate application state from evaluate.
 const captureLocal=async(text,{file=false}={})=>{
  await page.locator(`[data-r2-mode=${file?'photo':'text'}]`).click();
  if(file)await page.locator('#r2-file').setInputFiles({name:'TEST-malformed.png',mimeType:'image/png',buffer:Buffer.from('TEST bytes are not PNG')});
  if(file)await page.locator('#r2-text-summary').click();
  await page.locator('#sheet textarea[name=text]').fill(text);await page.getByRole('button',{name:'Сохранить источник',exact:true}).click();
  await page.locator('#r2-source-status[data-state=pending]').waitFor();report.actions.push({action:'offline-form-capture',mode:file?'photo':'text',text});
  await page.getByRole('button',{name:'Закрыть',exact:true}).click();
 };
 await context.setOffline(true);
 report.serviceWorkers=context.serviceWorkers().length;
 const marker=crypto.randomUUID();await captureLocal(`TEST AUD004 malformed original ${marker}`,{file:true});await captureLocal(`TEST AUD004 independent original one ${marker}`);await captureLocal(`TEST AUD004 independent original two ${marker}`);
 const bodies=(await records()).map(r=>r.body),ids=bodies.map(b=>b.client_capture_id);assert.equal(bodies.length,3);assert.equal(bodies[0].file_base64,Buffer.from('TEST bytes are not PNG').toString('base64'));
 let lost=false;
 await page.route('**/owner/captures',async route=>{if(route.request().method()!=='POST')return route.continue();const body=route.request().postDataJSON(),response=await route.fetch();transport.push({id:body.client_capture_id,status:response.status(),key:route.request().headers()['idempotency-key']});if(body.client_capture_id===ids[2]&&!lost){lost=true;assert.equal(response.status(),201);return route.abort('failed');}return route.fulfill({response});});
 await context.setOffline(false);
await waitForRecords(list=>list.length===2&&list.every(r=>r.upload_error));
 assert.deepEqual(transport.map(r=>r.status),[422,201,201]);
 let local=await records();assert.deepEqual(local.map(r=>r.body.client_capture_id),[ids[0],ids[2]]);assert.deepEqual(local[0].body,bodies[0]);assert.equal(local[0].upload_error.kind,'validation');assert.equal(local[0].upload_error.status,422);assert.match(local[0].upload_error.message,/JPEG\/PNG\/PDF/);assert.equal(local[1].upload_error.kind,'retry');
 const firstSaved=(await read(root+'/captures')).items.filter(s=>ids.includes(s.client_capture_id));assert.deepEqual(firstSaved.map(s=>s.client_capture_id).sort(),ids.slice(1).sort());report.queueAfterLostResponse={serverSaved:firstSaved.map(s=>({id:s.id,client_capture_id:s.client_capture_id})),localIds:local.map(r=>r.body.client_capture_id)};
 await openLocal();assert.match(await page.locator('#sheet').innerText(),/422/);const downloadPromise=page.waitForEvent('download');await page.locator('[data-local-export="0"]').click();const download=await downloadPromise;await download.saveAs(artifacts+'/stage-d-malformed-original.json');assert.deepEqual(JSON.parse(await fs.readFile(artifacts+'/stage-d-malformed-original.json','utf8')),bodies[0]);await screenshot('stage-d-retained-invalid-source');await page.getByRole('button',{name:'Закрыть',exact:true}).click();
 await page.locator('#owner-sync').click();await waitForRecords(list=>list.length===1);
 local=await records();assert.deepEqual(local[0].body,bodies[0]);assert.equal(transport.filter(x=>x.id===ids[0]).length,1,'Permanent 422 must not auto-retry');assert.equal(transport.filter(x=>x.id===ids[2]).length,2);assert.equal(new Set(transport.filter(x=>x.id===ids[2]).map(x=>x.key)).size,1);
 const finalSaved=(await read(root+'/captures')).items.filter(s=>ids.includes(s.client_capture_id));assert.equal(finalSaved.length,2);assert.deepEqual(finalSaved.map(s=>s.id).sort(),firstSaved.map(s=>s.id).sort());
 for(const source of finalSaved){const original=await context.request.get(base+root+`/captures/${source.id}/original`);assert.equal(original.status(),200);assert.equal(await original.text(),bodies[ids.indexOf(source.client_capture_id)].text);}
 // Direct database evidence is mandatory. Do not mark this script passed when
 // TEST_DATABASE_URL is missing, and never substitute mocked API persistence.
 const pg=JSON.parse(execFileSync(process.env.PYTHON||'python',['-c',`import json,os,sys
from sqlalchemy import create_engine,text
from greenhouse_owner_os.infrastructure.test_safety import require_disposable_database
url=os.environ['TEST_DATABASE_URL'];require_disposable_database(url,os.environ.get('APP_ENV'))
ids=json.loads(sys.argv[1]);e=sys.argv[2]
with create_engine(url).connect() as c:
 rows=c.execute(text("SELECT id::text,locator,raw_text,encode(file_bytes,'hex') AS bytes FROM core.inbox_items WHERE entity_id=CAST(:e AS uuid) AND locator IN (:a,:b,:c)"),dict(e=e,a=ids[0],b=ids[1],c=ids[2])).mappings().all()
 print(json.dumps([dict(row) for row in rows]))`,JSON.stringify(ids),entity],{encoding:'utf8'}));
 assert.equal(pg.length,2);assert.deepEqual(pg.map(r=>r.locator).sort(),ids.slice(1).sort());assert.deepEqual(pg.map(r=>r.id).sort(),firstSaved.map(r=>r.id).sort());report.postgresqlRows=pg;
 await openLocal();await page.locator('[data-local-retry="0"]').click();await page.waitForFunction(()=>document.querySelector('[data-local-retry="0"]')&&!document.querySelector('[data-local-retry="0"]').disabled);assert.equal(transport.filter(x=>x.id===ids[0]).length,2);assert.deepEqual((await records())[0].body,bodies[0]);await page.getByRole('button',{name:'Закрыть',exact:true}).click();report.explicitRetryPreservedOriginal=true;
 let queueFault=403;
 await page.unroute('**/owner/captures');
 await page.route('**/owner/captures',async route=>{if(route.request().method()!=='POST')return route.continue();const body=route.request().postDataJSON();if(queueFault){transport.push({id:body.client_capture_id,status:queueFault,injected:true});return route.fulfill({status:queueFault,contentType:'application/json',body:JSON.stringify({error:{message:'TEST paused source upload'}})});}const response=await route.fetch();transport.push({id:body.client_capture_id,status:response.status()});return route.fulfill({response});});
 await context.setOffline(true);await captureLocal(`TEST AUD004 access pause first ${marker}`);await captureLocal(`TEST AUD004 access pause second ${marker}`);const pauseBodies=(await records()).filter(r=>!ids.includes(r.body.client_capture_id)).map(r=>r.body);await context.setOffline(false);
 await waitForRecords(list=>list.some(r=>r.upload_error?.status===403));
 assert.equal(transport.filter(t=>t.id===pauseBodies[1].client_capture_id).length,0);queueFault=503;await page.locator('#owner-sync').click();await waitForRecords(list=>list.some(r=>r.upload_error?.status===503));assert.equal(transport.filter(t=>t.id===pauseBodies[1].client_capture_id).length,0);
 queueFault=0;await context.clearCookies();await page.locator('#owner-sync').click();await page.getByLabel('Ключ доступа').waitFor();assert.equal(transport.filter(t=>t.id===pauseBodies[1].client_capture_id).length,0);const retained=await records();for(const body of pauseBodies)assert.deepEqual(retained.find(r=>r.body.client_capture_id===body.client_capture_id).body,body);
 await page.getByLabel('Ключ доступа').fill(process.env.PREVIEW_SECRET);await page.getByRole('button',{name:'Открыть Finance',exact:true}).click();await page.getByRole('heading',{name:'Моя теплица',exact:true}).waitFor();await waitReady();await waitForRecords(list=>list.length===1);report.queuePause={403:true,503:true,real401:true,identitiesPreserved:true,recovered:true};
 inventoryUnavailable=true;await page.locator('#refresh').click();await page.locator('[data-load-state=unavailable]').waitFor();await waitReady();assert.match(await page.locator('#app').innerText(),/не показываются как актуальные/);assert.equal(await page.locator('.finance-kpis,.owner-kpi').count(),0);await openLocal('#load-local');assert.match(await page.locator('#sheet').innerText(),/422/);const outageDownload=page.waitForEvent('download');await page.locator('[data-local-export="0"]').click();const copy=await outageDownload;await copy.saveAs(artifacts+'/stage-d-outage-original.json');assert.deepEqual(JSON.parse(await fs.readFile(artifacts+'/stage-d-outage-original.json','utf8')),bodies[0]);await screenshot('stage-d-outage-local-copies');await page.getByRole('button',{name:'Закрыть',exact:true}).click();report.warmFailureAtomic=true;report.outageExportAvailable=true;
 inventoryUnavailable=false;await page.locator('#load-retry').click();await page.getByRole('heading',{name:'Моя теплица',exact:true}).waitFor();await waitReady();
 await page.reload();await page.getByRole('heading',{name:'Моя теплица',exact:true}).waitFor();await waitReady();assert.equal(transport.filter(x=>x.id===ids[0]).length,2);assert.deepEqual((await records())[0].body,bodies[0]);report.permanentSurvivesReload=true;
 await context.clearCookies();await page.reload();await page.getByLabel('Ключ доступа').waitFor();assert.equal(await page.locator('.owner-kpi').count(),0);report.realExpired401=true;
 assert.deepEqual(errors,[]);report.transport=transport;report.errors=errors;report.passed=true;await fs.writeFile(artifacts+'/stage-d-browser-report.json',JSON.stringify(report,null,2));console.log('PASS AUD004/005 real browser/API/PostgreSQL queue, malformed export, response-loss replay, boot503/retry/warm atomic snapshot and true401');
}catch(error){report.passed=false;report.error=error.message;report.transport=transport;report.errors=errors;await fs.writeFile(artifacts+'/stage-d-browser-report.json',JSON.stringify(report,null,2));await page.screenshot({path:artifacts+'/stage-d-failure.png',fullPage:true}).catch(()=>{});throw error;}finally{await browser.close();}
