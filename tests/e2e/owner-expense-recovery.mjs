// Real authenticated caller -> API -> disposable PostgreSQL. Only response
// delivery is interrupted; requests still execute against the actual service.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import fs from 'node:fs/promises';
import {chromium} from 'playwright';
import {navigateOwner} from './navigation.mjs';
import {chooseTaskAudience} from './task-review-fixture.mjs';

const base=process.env.PREVIEW_URL||'http://127.0.0.1:4173',artifacts=process.env.E2E_ARTIFACTS||'artifacts';
const browser=await chromium.launch({headless:true,args:['--no-sandbox'],...(process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{})});
const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
const sheet=()=>page.locator('#sheet'),click=name=>sheet().getByRole('button',{name,exact:true}).click();
const close=async()=>{if(await sheet().isVisible())await click('Закрыть');};
async function poll(check){for(let i=0;i<100;i++){if(await check())return;await page.waitForTimeout(100);}assert.fail('G preparation did not reach its expected state');}
await fs.mkdir(artifacts,{recursive:true});
try{
  await page.goto(base);await page.getByLabel('Ключ доступа').fill(process.env.PREVIEW_SECRET);
  await page.getByRole('button',{name:'Открыть Finance',exact:true}).click();await page.getByRole('heading',{name:'Моя теплица',exact:true}).waitFor();
  const eid=(await (await context.request.get(base+'/preview/config')).json()).entity_id,root=`/v1/entities/${eid}/owner`;
  const get=async path=>{const response=await context.request.get(base+root+path);assert.equal(response.status(),200,await response.text());return response.json();};
  const post=async(path,body,expected=200)=>{const response=await context.request.post(base+root+path,{data:body,headers:{'Idempotency-Key':crypto.randomUUID()}});assert.equal(response.status(),expected,await response.text());return response.json();};
  const evidence=(mode,id)=>JSON.parse(execFileSync(process.env.PYTHON||'python',['scripts/owner_recovery_browser_evidence.py',mode,eid,id],{encoding:'utf8'}));
  const openProposal=async id=>{await close();await navigateOwner(page,'review');await page.locator(`[data-proposal="${id}"] [data-r2-open]`).click();await sheet().getByRole('heading',{name:'Проверить действие',exact:true}).waitFor();};
  const openSources=async()=>{await close();await navigateOwner(page,'home');await page.locator('#owner-sources').click();await sheet().getByRole('heading',{name:'Сохранённые источники',exact:true}).waitFor();};
  const initial=await get('/home'),cash=initial.finance.references.find(r=>r.kind==='money_account'&&r.original_name==='Cash TR');assert.ok(cash);
  // Actual unchanged multi-proposal preparation, not synthetic sibling SQL.
  const sourceText='TEST неопределённое событие.\nКупил бензин за 321 TRY.\nЗавтра полить İmamlı.';
  await page.locator('[data-r2-mode=text]').click();await sheet().locator('textarea[name=text]').fill(sourceText);await click('Сохранить источник');await sheet().waitFor({state:'hidden'});
  await poll(async()=> (await get('/captures')).items.some(s=>s.text===sourceText&&s.proposals.length===3));
  const source=(await get('/captures')).items.find(s=>s.text===sourceText),prepared=(await get('/confirmations')).items.filter(p=>p.inbox_id===source.id);
  assert.deepEqual(prepared.map(p=>p.action_type).sort(),['DIRECT_EXPENSE','UNRESOLVED','UNRESOLVED']);
  const taskOrigin=prepared.find(p=>p.action_type==='UNRESOLVED'&&p.revision.payload.task_candidates);assert.ok(taskOrigin);
  let origin=prepared.find(p=>p.action_type==='UNRESOLVED'&&!p.revision.payload.task_candidates);assert.ok(origin);assert.notEqual(origin.id,taskOrigin.id);
  await navigateOwner(page,'review');await sheet().locator(`[data-proposal="${taskOrigin.id}"] [data-r2-open]`).click();
  const personal=await chooseTaskAudience(page,(path,body)=>body===undefined?get(path.slice(root.length)):post(path.slice(root.length),body),root,taskOrigin,'personal');await close();
  assert.equal(personal.action_type,'PERSONAL_TASK');
  const siblings=await Promise.all([...prepared.filter(p=>p.id!==origin.id).map(p=>p.id),personal.id].map(id=>get(`/confirmations/${id}/preview`)));
  assert.equal((await get(`/captures/${source.id}`)).proposals.length,4,'Three original slots plus one personal successor');
  const correctedNote='TEST G уточнение владельца: отдельная поездка за топливом, сумму уточню';
  origin=await post(`/confirmations/${origin.id}/revise`,{expected_version:origin.version,payload:{note:correctedNote}});
  const before=evidence('read',source.id);
  const original=await context.request.get(base+root+`/captures/${source.id}/original`);assert.equal(await original.text(),sourceText);
  assert.equal(before.original_sha256,createHash('sha256').update(sourceText).digest('hex'));
  // Closing before explicit selection creates no link or effect.
  await openProposal(origin.id);await click('Исправить');await sheet().getByRole('button',{name:'Прямой расход по этому источнику',exact:true}).waitFor();await page.keyboard.press('Escape');await sheet().waitFor({state:'hidden'});
  assert.deepEqual(evidence('read',source.id),before);
  // Commit handoff but lose its first response. The visible retry reuses it.
  let lostHandoff=false,handoffRequests=[];
  const handoffPattern='**/owner/captures/*/direct-expense-recovery';
  await page.route(handoffPattern,async route=>{handoffRequests.push({body:route.request().postDataJSON(),key:route.request().headers()['idempotency-key']});if(lostHandoff)return route.continue();lostHandoff=true;await route.fetch();await route.abort('failed');});
  await openProposal(origin.id);await click('Исправить');await click('Прямой расход по этому источнику');await sheet().locator('#r2-recovery-retry:not([hidden])').waitFor();
  const reserved=evidence('read',source.id);assert.equal(reserved.links.length,2);assert.equal(reserved.effects.length,0);assert.equal(reserved.proposals.length,5);
  assert.ok(reserved.links.some(link=>link.origin_proposal_id===taskOrigin.id&&link.recovery_proposal_id===personal.id));
  const expenseLink=reserved.links.find(link=>link.origin_proposal_id===origin.id);assert.ok(expenseLink);
  const target=expenseLink.recovery_proposal_id;assert.equal(expenseLink.origin_proposal_id,origin.id);
  await click('Повторить переход');await sheet().getByRole('heading',{name:'Исправить карточку',exact:true}).waitFor();await page.unroute(handoffPattern);
  assert.equal(handoffRequests.length,2);assert.deepEqual(handoffRequests[0],handoffRequests[1]);
  assert.deepEqual(handoffRequests[0].body,{origin_proposal_id:origin.id,expected_version:origin.version});
  assert.equal(await sheet().getByLabel('Сумма',{exact:true}).inputValue(),'');assert.equal(await sheet().getByLabel('Валюта',{exact:true}).inputValue(),'');
  assert.equal(await sheet().getByLabel('За что',{exact:true}).inputValue(),'');assert.match(await sheet().innerText(),/Контрагент необязателен/);
  await sheet().getByText('Оригинальный источник',{exact:true}).click();assert.match(await sheet().innerText(),/TEST неопределённое событие/);
  await sheet().locator(`[data-origin-proposal="${origin.id}"] summary`).click();assert.ok((await sheet().innerText()).includes(correctedNote));assert.match(await sheet().innerText(),/исходного предложения · версия 2/);
  for(const width of [320,390,768,1280]){await page.setViewportSize({width,height:844});assert.equal(await sheet().evaluate(n=>n.scrollWidth<=n.clientWidth),true);await page.screenshot({path:`${artifacts}/g-missing-${width}.png`,fullPage:true});}
  await page.setViewportSize({width:390,height:844});await close();
  assert.equal(evidence('read',source.id).expenses.length,0);assert.equal((await get(`/captures/${source.id}`)).server_saved,true);
  // Resume by source, including its old-origin button, with no new target.
  await openSources();await sheet().locator(`[data-source-proposal="${target}"]`).first().click();await click('Исправить');
  await sheet().getByLabel('Сумма',{exact:true}).fill('517.25');await sheet().getByLabel('Валюта',{exact:true}).selectOption('TRY');
  await sheet().getByLabel('Дата',{exact:true}).fill('2026-10-08');await sheet().getByLabel('Наличные / Банк',{exact:true}).selectOption(cash.id);
  await sheet().getByLabel('За что',{exact:true}).fill('TEST G ручной бензин');await click('Сохранить исправления');
  await sheet().getByRole('heading',{name:'Проверить действие',exact:true}).waitFor();assert.equal(await sheet().getByRole('button',{name:'Подтвердить',exact:true}).isEnabled(),true);
  const ready=await get(`/confirmations/${target}/preview`);assert.equal(ready.version,2);assert.equal(ready.revision.payload.counterparty_id,null);assert.equal(ready.revision.provenance.amount.category,'OWNER INPUT');
  await page.screenshot({path:`${artifacts}/g-exact-review.png`,fullPage:true});
  // A real confirm commits, then delivery fails. Reverse it through the trusted
  // service before retry: replay must not claim that it is currently posted.
  let lostConfirm=false,confirmRequests=[];const confirmPattern=`**/owner/confirmations/${target}/confirm`;
  await page.route(confirmPattern,async route=>{confirmRequests.push({body:route.request().postDataJSON(),key:route.request().headers()['idempotency-key']});if(lostConfirm)return route.continue();lostConfirm=true;await route.fetch();await route.abort('failed');});
  await click('Подтвердить');await sheet().locator('#r2-card-status').filter({hasText:'Связь прервалась'}).waitFor();
  const posted=await get(`/confirmations/${target}/preview`);assert.equal(posted.current_effect.status,'posted');assert.equal(posted.current_effect.counterparty_id,null);
  const postedEvidence=evidence('read',source.id);assert.equal(postedEvidence.effects.length,1);assert.equal(postedEvidence.expenses.length,1);assert.equal(postedEvidence.original_sha256,before.original_sha256);
  for(const prior of siblings){const current=await get(`/confirmations/${prior.id}/preview`);assert.deepEqual(current.revision,prior.revision);assert.equal(current.state,prior.state);assert.equal(current.version,prior.version);}
  const retired=await get(`/confirmations/${origin.id}/preview`);assert.equal(retired.state,'REJECTED');assert.deepEqual(retired.revision.payload,origin.revision.payload);assert.deepEqual(retired.revision.provenance,origin.revision.provenance);assert.equal(posted.recovery_origin_context.note,correctedNote);assert.equal(posted.recovery_origin_context.version,origin.version);assert.equal(retired.recovery_origin_context.note,correctedNote);
  await post(`/confirmations/${origin.id}/confirm`,{expected_version:origin.version,preview_digest:origin.revision.preview_digest,confirmed:true},409);
  await post(`/direct-expenses/${posted.current_effect.id}/reverse`,{expected_version:posted.current_effect.version,event_date:posted.current_effect.event_date,reason:'TEST G response-loss reversal'});
  await click('Подтвердить');await sheet().getByText(/Расход отменён\. История подтверждения/).waitFor();await page.unroute(confirmPattern);
  const reversedBadge=sheet().getByText('Расход · Отменён',{exact:true});await reversedBadge.waitFor();const badgeBox=await reversedBadge.boundingBox();assert.ok(badgeBox&&badgeBox.y>=0&&badgeBox.y+badgeBox.height<=844,'Current reversed status must be visible without scrolling');
  assert.deepEqual(confirmRequests[0],confirmRequests[1]);assert.equal(confirmRequests.length,2);assert.equal(await sheet().locator('#r2-edit').count(),0);
  assert.equal(evidence('read',source.id).expenses.length,1);assert.deepEqual((await get('/home')).finance.balances,initial.finance.balances);
  await page.screenshot({path:`${artifacts}/g-reversal-current-truth.png`,fullPage:true});
  // The independent already-typed expense revises in place with known fields.
  const typed=siblings.find(p=>p.action_type==='DIRECT_EXPENSE');await openProposal(typed.id);await click('Исправить');
  assert.match(typed.revision.payload.amount,/^321(?:\.00)?$/);assert.equal(await sheet().getByLabel('Сумма',{exact:true}).inputValue(),typed.revision.payload.amount);assert.equal(await sheet().getByLabel('Валюта',{exact:true}).inputValue(),'TRY');
  assert.match(await sheet().innerText(),/За что: Сохранённый контекст/);
  await sheet().getByLabel('Сумма',{exact:true}).fill('999');await click('+ Новый');await sheet().getByLabel('Название',{exact:true}).fill('TEST G draft party');await click('Готово');
  await sheet().getByRole('heading',{name:'Исправить карточку',exact:true}).waitFor();assert.equal(await sheet().getByLabel('Сумма',{exact:true}).inputValue(),'999');assert.match(await sheet().innerText(),/Сумма: Из источника · 321/);assert.doesNotMatch(await sheet().innerText(),/Из источника · 999/);
  await close();await openProposal(typed.id);await click('Исправить');assert.equal(await sheet().getByLabel('Сумма',{exact:true}).inputValue(),typed.revision.payload.amount);
  await sheet().getByLabel('Наличные / Банк',{exact:true}).selectOption(cash.id);await click('Сохранить исправления');
  await sheet().getByRole('heading',{name:'Проверить действие',exact:true}).waitFor();const typedReady=await get(`/confirmations/${typed.id}/preview`);
  assert.equal(typedReady.id,typed.id);assert.deepEqual(typedReady.revision.provenance.amount,typed.revision.provenance.amount);assert.deepEqual(typedReady.revision.provenance.currency,typed.revision.provenance.currency);
  await click('Подтвердить');await sheet().getByText(/Расход подтверждён\. Деньги списаны/).waitFor();assert.equal(await sheet().getByText('Расход · Подтверждено',{exact:true}).isVisible(),true);const typedPosted=await get(`/confirmations/${typed.id}/preview`);
  await post(`/direct-expenses/${typedPosted.current_effect.id}/reverse`,{expected_version:typedPosted.current_effect.version,event_date:typedPosted.current_effect.event_date,reason:'TEST G typed sibling cleanup'});
  const final=evidence('read',source.id);assert.equal(final.links.length,2);assert.equal(final.proposals.length,5);assert.equal(final.expenses.length,2);assert.ok(final.expenses.every(d=>d.status==='reversed'));
  await fs.writeFile(`${artifacts}/g-lineage-evidence.json`,JSON.stringify({before,reserved,posted:postedEvidence,final},null,2));
  // Separate synthetic exhausted-job fixture, not a claim of OCR failure.
  const failed=evidence('failed',initial.owner_id);await openSources();await sheet().locator(`[data-source-manual="${failed.id}"]`).click();
  await sheet().getByRole('heading',{name:'Исправить карточку',exact:true}).waitFor();assert.equal(await sheet().getByLabel('Сумма',{exact:true}).inputValue(),'');await close();
  const failedEvidence=evidence('read',failed.id);assert.equal(failedEvidence.links.length,1);assert.equal(failedEvidence.links[0].origin_proposal_id,null);assert.equal(failedEvidence.expenses.length,0);
  assert.equal(await (await context.request.get(base+root+`/captures/${failed.id}/original`)).text(),failed.text);
  await fs.writeFile(`${artifacts}/g-failed-source-evidence.json`,JSON.stringify(failedEvidence,null,2));
  assert.deepEqual((await get('/home')).finance.balances,initial.finance.balances);assert.deepEqual(errors,[]);
  console.log('PASS G real source/caller, explicit selection, cancellation, response-loss handoff/confirm replay, current reversal truth, optional party, known provenance, independent actual-parser siblings, synthetic exhausted source, SQL lineage, 320/390/768/1280');
}catch(error){
  await page.screenshot({path:`${artifacts}/g-failure.png`,fullPage:true}).catch(()=>{});
  await fs.writeFile(`${artifacts}/g-failure.html`,await page.content());await fs.writeFile(`${artifacts}/g-failure-errors.json`,JSON.stringify({message:error.message,pageErrors:errors},null,2));throw error;
}finally{await browser.close();}
