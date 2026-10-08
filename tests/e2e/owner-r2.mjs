// Real authenticated browser -> R2 API -> disposable PostgreSQL; no production.
import assert from 'node:assert/strict';
import {chromium} from 'playwright';
import fs from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
const base=process.env.PREVIEW_URL||'http://127.0.0.1:4173';
const browser=await chromium.launch({headless:true,args:['--no-sandbox'],...(process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{})});
const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
const sheet=()=>page.locator('#sheet');
const click=n=>sheet().getByRole('button',{name:n,exact:true}).click();
const close=()=>click('Закрыть');
async function poll(check){for(let i=0;i<100;i++){if(await check())return;await page.waitForTimeout(100);}assert.fail('R2 asynchronous preparation did not complete');}
try{
 await page.goto(base);await page.getByLabel('Ключ доступа').fill(process.env.PREVIEW_SECRET);await page.getByRole('button',{name:'Открыть Finance',exact:true}).click();await page.getByRole('heading',{name:'Моя теплица',exact:true}).waitFor();
 const eid=(await (await context.request.get(base+'/preview/config')).json()).entity_id,root=`/v1/entities/${eid}/owner`;
 const api=async(path)=>{const r=await context.request.get(base+root+path);assert.equal(r.status(),200);return r.json();};
 const post=async(path,body)=>{const r=await context.request.post(base+path,{data:body,headers:{'Idempotency-Key':crypto.randomUUID()}});assert.ok([200,201].includes(r.status()),await r.text());return r.json();};
 const captureText=async(text)=>{await page.locator('[data-r2-mode=text]').click();await sheet().getByLabel('Текст / расшифровка').fill(text);await click('Сохранить источник');await sheet().waitFor({state:'hidden'});};
 const openProposal=async(p)=>{await page.locator('#owner-queue').click();await page.locator(`[data-proposal="${p.id}"]`).getByRole('button',{name:'Проверить карточку',exact:true}).click();await sheet().getByRole('heading',{name:'Проверить действие',exact:true}).waitFor();};
 const before=await api('/home');
 // Delay the first queue snapshot, as when it was read before preparation committed.
 let delayedQueue=false;await page.route('**/owner/confirmations?offset=0',async route=>{if(delayedQueue)return route.continue();delayedQueue=true;const response=await route.fetch();await route.fulfill({response,json:{...await response.json(),items:[],preparing:1}});});
 await page.locator('[data-r2-mode=text]').click();await sheet().getByLabel('Текст / расшифровка').fill('Сегодня полить İmamlı.');await click('Сохранить источник');await sheet().waitFor({state:'hidden'});
 await page.locator('#owner-queue').click();await sheet().getByText('Источник сохранён. Готовим карточки; можно записать следующее событие.',{exact:true}).waitFor();await sheet().getByText('Моя задача · Готово к подтверждению',{exact:false}).first().waitFor();await page.unroute('**/owner/confirmations?offset=0');
 assert.equal((await api('/home')).task_count,before.task_count);assert.deepEqual((await api('/home')).finance.balances,before.finance.balances);
 await click('Проверить карточку');await click('Исправить');await sheet().getByLabel('Задача',{exact:true}).fill('TEST R2 полить İmamlı');await click('Сохранить исправления');await click('Позже');await sheet().getByText('Моя задача · Отложено',{exact:true}).waitFor();assert.match(await sheet().innerText(),/Отложено/);await click('Проверить карточку');await click('Подтвердить');await sheet().waitFor({state:'hidden'});
 assert.equal((await api('/home')).task_count,before.task_count+1);await page.locator('[data-owner-done]').filter({hasText:'Готово'}).first().click();await page.waitForFunction(()=>document.querySelector('.owner-home')&&!document.querySelector('.owner-task'));assert.equal((await api('/home')).task_count,before.task_count);
 // Unreadable image is saved, never fabricated as an authoritative business effect.
 await page.locator('[data-r2-mode=photo]').click();await sheet().locator('input[type=file]').setInputFiles({name:'TEST-unreadable.png',mimeType:'image/png',buffer:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a7WQAAAAASUVORK5CYII=','base64')});await click('Сохранить источник');await sheet().waitFor({state:'hidden'});
 await poll(async()=> (await api('/confirmations')).items.some(p=>p.action_type==='UNRESOLVED'));const sources=(await api('/confirmations')).items;assert.ok(sources.some(p=>p.action_type==='UNRESOLVED'&&p.revision.missing.length));
 await page.locator('#owner-queue').click();await click('Проверить карточку');assert.equal(await sheet().getByRole('button',{name:'Подтвердить',exact:true}).isDisabled(),true);await click('Отклонить');await close();
 // Offline commits in IndexedDB; online retry preserves capture UUID and one source.
 await context.setOffline(true);await page.locator('[data-r2-mode=text]').click();await sheet().getByLabel('Текст / расшифровка').fill('TEST R2 неизвестное событие offline');await click('Сохранить источник');await sheet().getByText('Сохранено на телефоне. В системе ещё нет; отправим при восстановлении связи.',{exact:true}).waitFor();await close();await context.setOffline(false);await page.locator('#owner-sync').click();await page.locator('#refresh').click();
 await poll(async()=> (await api('/confirmations')).items.some(p=>p.revision.payload.note?.includes('offline')));assert.ok((await api('/confirmations')).items.some(p=>p.revision.payload.note?.includes('offline')));
 // Compact Finance card uses an explicit debt and Cash account, never an advance.
 const party=before.finance.references.find(r=>r.kind==='counterparty'&&r.original_name.includes('Серкан'));
 const cash=before.finance.references.find(r=>r.kind==='money_account'&&r.original_name==='Cash TR');
 const debt=before.finance.obligations.find(o=>o.counterparty_id===party.id&&o.remaining_amount==='2600.00');
 await captureText('Заплатил Серкану 5 000');await poll(async()=> (await api('/confirmations')).items.some(p=>p.action_type==='PAYMENT'));
 let payment=(await api('/confirmations')).items.find(p=>p.action_type==='PAYMENT');await openProposal(payment);assert.equal(await sheet().getByRole('button',{name:'Подтвердить',exact:true}).isDisabled(),true);
 await click('Исправить');await sheet().getByLabel('Сумма',{exact:true}).fill('500');await sheet().getByLabel('Контрагент',{exact:true}).selectOption(party.id);await sheet().getByLabel('Наличные / Банк').selectOption(cash.id);await click('Сохранить исправления');await click('Исправить');await sheet().getByLabel('Погасить долг').selectOption(debt.obligation_id);await click('Сохранить исправления');await click('Подтвердить');await sheet().waitFor({state:'hidden'});
 payment=await api(`/confirmations/${payment.id}/preview`);const paid=payment.state;assert.equal(paid,'POSTED');
 const finance=(await api('/home')).finance;assert.equal(finance.balances.TRY.cash,'-500.00');assert.equal(finance.balances.TRY.payable,'2100.00');assert.deepEqual(finance.advances,before.finance.advances);
 const payments=await (await context.request.get(base+`/v1/entities/${eid}/finance-overview`)).json();const paidRecord=payments.operations.find(p=>p.kind==='payment'&&p.source===`proposal:${payment.id}`);
 assert.ok(paidRecord);await post(`/v1/payments/${paidRecord.id}/reverse`,{expected_version:2,event_date:'2026-10-08',reason:'TEST R2 browser cleanup'});
 // Physical receipt with unknown cost; use existing synthetic reference IDs.
 const inventory=await (await context.request.get(base+`/v1/entities/${eid}/inventory`)).json(),product=inventory.products.find(p=>p.name.includes('KNO3')||p.aliases.includes('KNO3')),location=inventory.locations[0];assert.ok(product&&location);
 await captureText('Привезли 100 кг KNO3 в İmamlı.');await poll(async()=> (await api('/confirmations')).items.some(p=>p.action_type==='STOCK_RECEIPT'));
 let stock=(await api('/confirmations')).items.find(p=>p.action_type==='STOCK_RECEIPT');await openProposal(stock);await click('Исправить');await sheet().getByLabel('Товар',{exact:true}).selectOption(product.id);await sheet().getByLabel('Склад',{exact:true}).selectOption(location.id);await click('Сохранить исправления');await click('Подтвердить');await sheet().waitFor({state:'hidden'});
 stock=await api(`/confirmations/${stock.id}/preview`);assert.equal(stock.state,'POSTED');const afterStock=await (await context.request.get(base+`/v1/entities/${eid}/inventory`)).json(),received=afterStock.events.find(ev=>ev.locator===stock.id);assert.ok(received&&received.payload.lines[0].unit_cost===null);
 await post(`/v1/entities/${eid}/inventory/events/${received.id}/reverse`,{event_date:'2026-10-08',reason:'TEST R2 browser cleanup'});
 // Actual image -> real local OCR -> original qualities -> individual review.
 if(!before.finance.references.some(r=>r.kind==='counterparty'&&r.original_name==='Görgün'))await post(`/v1/entities/${eid}/counterparties`,{name:'Görgün',role:'depot',source:'test:r2-browser'});await page.locator('#refresh').click();await page.waitForFunction(()=>document.querySelector('#app').getAttribute('aria-busy')==='false');
 const photo=execFileSync('python',['-c','from tests.ocr_corpus import document;import sys;sys.stdout.buffer.write(document(0)[0])']);
 await page.locator('[data-r2-mode=photo]').click();await sheet().locator('input[type=file]').setInputFiles({name:'TEST-r2-Gorgun.png',mimeType:'image/png',buffer:photo});await click('Сохранить источник');await sheet().waitFor({state:'hidden'});await poll(async()=> (await api('/confirmations')).items.some(p=>p.action_type==='DEPOT_RECEIPT'));
 let receipt=(await api('/confirmations')).items.find(p=>p.action_type==='DEPOT_RECEIPT');assert.deepEqual(receipt.revision.payload.qualities,['HIGHEST','FIRST','SECOND']);await openProposal(receipt);assert.match(await sheet().innerText(),/Alacak: 3000.*не оплата/);await click('Исправить');await sheet().getByLabel('Подтверждаю качества строк именно этого депо').check();await sheet().getByLabel('Проверил строки и сумму; Alacak не означает оплату').check();await click('Сохранить исправления');await click('Подтвердить');await sheet().waitFor({state:'hidden'});
 const afterReceipt=await (await context.request.get(base+`/v1/entities/${eid}/harvest`)).json(),sale=afterReceipt.settlements.find(s=>s.evidence.source_reference===receipt.id);assert.ok(sale);assert.equal((await api('/home')).finance.balances.TRY.cash,'0.00');await post(`/v1/entities/${eid}/harvest/settlements/${sale.id}/reverse`,{expected_version:2,event_date:'2026-10-08',reason:'TEST R2 browser cleanup'});
 await page.locator('#refresh').click();await page.waitForFunction(()=>document.querySelector('#app').getAttribute('aria-busy')==='false');
 for(const width of [320,390,768,1280]){await page.setViewportSize({width,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);assert.equal(await page.locator('#owner-capture-bar').isVisible(),true);await page.evaluate(async()=>{await document.fonts.ready;await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));for(const button of document.querySelectorAll('#nav button,#owner-capture-bar button')){const box=button.getBoundingClientRect();if(box.width<44||box.height<44||box.left<0||box.right>innerWidth+1)throw Error('Owner touch target outside viewport or smaller than 44px');}});await page.screenshot({path:`${process.env.E2E_ARTIFACTS||'artifacts'}/owner-r2-${width}.png`,fullPage:true});await page.locator('#owner-queue').click();assert.equal(await page.evaluate(()=>document.querySelector('#sheet').scrollWidth<=document.querySelector('#sheet').clientWidth),true);await close();}
 assert.deepEqual((await api('/home')).finance.balances,before.finance.balances);assert.deepEqual(errors,[]);
 console.log('PASS R2 source-first, personal task revision/defer/confirm/done, unreadable photo/reject, offline retry, compact payment/unknown-cost stock/real Gorgun OCR confirmation, responsive 320/390/768/1280');
}finally{await browser.close();}
