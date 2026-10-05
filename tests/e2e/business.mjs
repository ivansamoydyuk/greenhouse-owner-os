import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {chromium} from 'playwright';
const base=process.env.PREVIEW_URL||'http://127.0.0.1:4173';
const browser=await chromium.launch({headless:true,args:['--no-sandbox'],...(process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{})});
const context=await browser.newContext({viewport:{width:390,height:844}}),page=await context.newPage(),errors=[];
page.on('pageerror',x=>errors.push(x.message));
const sheet=()=>page.locator('#sheet-content');
const click=n=>sheet().getByRole('button',{name:n,exact:true}).click();
const confirm=()=>page.locator('#confirmation').getByRole('button',{name:'Подтвердить',exact:true}).click();
const close=()=>sheet().getByRole('button',{name:'Закрыть',exact:true}).click();
let eid;
async function view(){return (await context.request.get(base+`/v1/entities/${eid}/finance-overview?limit=100`)).json();}
async function capture(text){await page.getByRole('button',{name:'🎙 / ✎ Записать событие',exact:true}).click();await page.getByLabel('Что произошло?').fill(text);await click('Разобрать и проверить');await page.getByRole('heading',{name:'Проверить сообщение',exact:true}).waitFor();}
async function post(){await click('Провести');await confirm();await sheet().locator('.badge.posted').waitFor();}
async function reverse(){await click('Отменить операцию');await sheet().getByLabel('Причина отмены').fill('TEST business alignment cleanup');await click('Проверить отмену');await confirm();await sheet().locator('.badge.reversed').waitFor();}
async function draft(){await click('Подтвердить данные и создать черновик');await confirm();await sheet().getByText('Черновик',{exact:true}).waitFor();}
try{
 await page.goto(base);await page.getByLabel('Ключ доступа').fill(process.env.PREVIEW_SECRET);await page.getByRole('button',{name:'Открыть Finance',exact:true}).click();await page.getByRole('heading',{name:'Финансы',exact:true}).waitFor();
 eid=(await (await context.request.get(base+'/preview/config')).json()).entity_id;
 const initial=await view(),cp=initial.references.find(r=>r.original_name==='Серкан Ремонт насосов'),imamli=initial.work_areas.find(a=>a.name==='İmamlı'),bankUSD=initial.references.find(a=>a.original_name==='Банк USD');
 assert.equal(initial.obligations.find(o=>o.opening).attributions[0].work_area_id,imamli.id);
 await page.getByRole('heading',{name:'Стратегические обязательства',exact:true}).waitFor();
 await capture('Серкану должен 8000 лир за ремонт насоса в Имамлы');
 assert.equal(await sheet().getByLabel('Дата события',{exact:true}).inputValue(),'');
 assert.equal(await sheet().getByLabel('Тип долга',{exact:true}).inputValue(),'OPERATIONAL');
 assert.equal(await sheet().getByLabel('İmamlı · сумма',{exact:true}).inputValue(),'8000');
 assert.equal((await view()).balances.TRY.payable,'2600.00');
 await click('Подтвердить данные и создать черновик');await confirm();await page.locator('#capture-error').filter({hasText:'Дата события'}).waitFor();
 assert.equal((await view()).balances.TRY.payable,'2600.00');
 await sheet().getByLabel('Дата события',{exact:true}).fill('2026-10-05');await draft();
 assert.equal((await view()).balances.TRY.payable,'2600.00');await post();
 let v=await view();assert.equal(v.business_balances.TRY.operational_payable,'10600.00');assert.ok(v.economics.some(x=>x.work_area_id===imamli.id&&x.amount==='8000.00'));
 await reverse();await close();console.log('PASS: F/H operational text -> explicit missing date -> reviewed draft -> separate post -> attribution -> reversal');
 // Unknown counterparty never created by extraction; add explicit TEST reference.
 const response=await context.request.post(base+`/v1/entities/${eid}/counterparties`,{headers:{'Idempotency-Key':crypto.randomUUID()},data:{name:'Ахмет TEST',role:'supplier',source:'test:business-e2e'}});assert.equal(response.status(),201);const ahmet=await response.json();
 await page.getByRole('button',{name:'Обновить',exact:true}).click();
 await capture('Взял у Ахмета 300000 долларов на запуск бизнеса, это стратегический долг');
 assert.equal(await sheet().getByLabel('Тип долга',{exact:true}).inputValue(),'STRATEGIC');
 await sheet().getByLabel('Контрагент',{exact:true}).selectOption(ahmet.id);await sheet().getByLabel('Дата события',{exact:true}).fill('2026-10-05');await sheet().getByLabel('Счёт / касса',{exact:true}).selectOption(bankUSD.id);
 await draft();assert.equal((await view()).balances.USD.cash,'0.00');await post();
 v=await view();assert.equal(v.business_balances.USD.strategic_payable,'300000.00');assert.equal(v.business_balances.USD.operational_payable,'0.00');assert.equal(v.balances.USD.cash,'300000.00');assert.equal(v.balances.TRY.payable,'2600.00');await reverse();await close();console.log('PASS: G/K strategic receipt -> principal/cash not operating expense -> currency isolation -> reverse');
 // Explicit payment capture reuses the attributed opening obligation without moving geography.
 await capture('Заплатил Серкану 2600 лир сегодня наличными из Cash TR');
 const opening=(await view()).obligations.find(o=>o.opening);
 await sheet().locator(`[name="allocation-${opening.obligation_id}"]`).fill('2600');await draft();
 assert.equal((await view()).balances.TRY.payable,'2600.00');await post();assert.equal((await view()).balances.TRY.payable,'0.00');
 assert.equal((await view()).obligations.find(o=>o.opening).attributions[0].work_area_id,imamli.id);
 await reverse();await close();console.log('PASS: captured payment -> explicit opening allocation -> cash/AP -> reverse, no geographic reallocation');
 // Depot debt is evidence for future authoritative settlement, not manual sale.
 await capture('Серкану должен 1 лир');await close();
 await page.getByRole('button',{name:'🎙 / ✎ Записать событие',exact:true}).click();await page.getByLabel('Что произошло?').fill('Горгун депо должен мне 65000 лир, обещали заплатить 15 октября');await click('Разобрать и проверить');await page.getByRole('heading',{name:'Требует внимания',exact:true}).waitFor();assert.equal(await sheet().getByRole('button',{name:'Подтвердить данные и создать черновик'}).count(),0);assert.equal((await view()).balances.TRY.receivable,'0.00');await close();console.log('PASS: depot sale source boundary, no duplicate receivable/revenue');
 // Adapter contract test, NOT a claim of real acoustic transcription accuracy.
 await page.addInitScript(()=>{window.SpeechRecognition=class{start(){const r=[{transcript:'Серкану должен 8000 лир за ремонт насоса в Имамлы'}];r.isFinal=true;this.onresult({results:[r]});this.onend?.();}stop(){this.onend?.();}abort(){}};});
 await page.reload();await page.getByRole('heading',{name:'Финансы',exact:true}).waitFor();await page.getByRole('button',{name:'🎙 / ✎ Записать событие',exact:true}).click();await click('🎙 Диктовать');assert.match(await page.getByLabel('Что произошло?').inputValue(),/8000/);await click('Разобрать и проверить');await page.getByRole('heading',{name:'Проверить сообщение',exact:true}).waitFor();v=await view();assert.ok(v.captures.some(x=>x.source==='voice_transcript'));assert.equal(v.balances.TRY.payable,'2600.00');await close();console.log('PASS: I speech result -> same extraction/review contract -> no posting; adapter simulated');
 await fs.mkdir('artifacts',{recursive:true});
 for(const width of [320,390,768,1280]){await page.setViewportSize({width,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);await page.screenshot({path:`artifacts/business-overview-${width}.png`,fullPage:true});await page.getByRole('button',{name:'🎙 / ✎ Записать событие',exact:true}).click();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);await close();}
 assert.deepEqual(errors,[]);v=await view();assert.equal(v.balances.TRY.payable,'2600.00');assert.ok(Object.values(v.balances).every(b=>b.cash==='0.00'));assert.ok((await (await context.request.get(base+`/v1/finance/reconciliation?entity_id=${eid}`)).json()).ok);console.log('PASS: responsive/global shell/errors/reconciliation, owner opening unchanged');
}finally{await browser.close();}
