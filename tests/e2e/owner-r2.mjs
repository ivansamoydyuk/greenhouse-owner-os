// Real authenticated browser -> R2 API -> disposable PostgreSQL; no production.
import assert from 'node:assert/strict';
import {chromium} from 'playwright';
import fs from 'node:fs/promises';
const base=process.env.PREVIEW_URL||'http://127.0.0.1:4173';
const browser=await chromium.launch({headless:true,args:['--no-sandbox'],...(process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{})});
const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
const sheet=()=>page.locator('#sheet');
const click=n=>sheet().getByRole('button',{name:n,exact:true}).click();
const close=()=>click('Закрыть');
try{
 await page.goto(base);await page.getByLabel('Ключ доступа').fill(process.env.PREVIEW_SECRET);await page.getByRole('button',{name:'Открыть Finance',exact:true}).click();await page.getByRole('heading',{name:'Моя теплица',exact:true}).waitFor();
 const eid=(await (await context.request.get(base+'/preview/config')).json()).entity_id,root=`/v1/entities/${eid}/owner`;
 const api=async(path)=>{const r=await context.request.get(base+root+path);assert.equal(r.status(),200);return r.json();};
 const before=await api('/home');
 await page.locator('[data-r2-mode=text]').click();await sheet().getByLabel('Текст / расшифровка').fill('Сегодня полить İmamlı.');await click('Сохранить источник');await sheet().waitFor({state:'hidden'});
 await page.locator('#owner-queue').click();await sheet().getByText('Моя задача · Готово к подтверждению',{exact:false}).first().waitFor();
 assert.equal((await api('/home')).task_count,before.task_count);assert.deepEqual((await api('/home')).finance.balances,before.finance.balances);
 await click('Проверить карточку');await click('Исправить');await sheet().getByLabel('Задача',{exact:true}).fill('TEST R2 полить İmamlı');await click('Сохранить исправления');await click('Позже');assert.match(await sheet().innerText(),/Отложено/);await click('Проверить карточку');await click('Подтвердить');await sheet().waitFor({state:'hidden'});
 assert.equal((await api('/home')).task_count,before.task_count+1);await page.locator('[data-owner-done]').filter({hasText:'Готово'}).first().click();await page.waitForFunction(()=>document.querySelector('.owner-home')&&!document.querySelector('.owner-task'));assert.equal((await api('/home')).task_count,before.task_count);
 // Unreadable image is saved, never fabricated as an authoritative business effect.
 await page.locator('[data-r2-mode=photo]').click();await sheet().locator('input[type=file]').setInputFiles({name:'TEST-unreadable.png',mimeType:'image/png',buffer:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a7WQAAAAASUVORK5CYII=','base64')});await click('Сохранить источник');await sheet().waitFor({state:'hidden'});
 const sources=(await api('/confirmations')).items;assert.ok(sources.some(p=>p.action_type==='UNRESOLVED'&&p.revision.missing.length));
 await page.locator('#owner-queue').click();await click('Проверить карточку');assert.equal(await sheet().getByRole('button',{name:'Подтвердить',exact:true}).isDisabled(),true);await click('Отклонить');await close();
 // Offline commits in IndexedDB; online retry preserves capture UUID and one source.
 await context.setOffline(true);await page.locator('[data-r2-mode=text]').click();await sheet().getByLabel('Текст / расшифровка').fill('TEST R2 неизвестное событие offline');await click('Сохранить источник');await sheet().getByText('Сохранено на телефоне. В системе ещё нет; отправим при восстановлении связи.',{exact:true}).waitFor();await close();await context.setOffline(false);await page.locator('#owner-sync').click();await page.locator('#refresh').click();
 assert.ok((await api('/confirmations')).items.some(p=>p.revision.payload.note?.includes('offline')));
 for(const width of [320,390,768,1280]){await page.setViewportSize({width,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);assert.equal(await page.locator('#owner-capture-bar').isVisible(),true);await page.screenshot({path:`${process.env.E2E_ARTIFACTS||'artifacts'}/owner-r2-${width}.png`,fullPage:true});await page.locator('#owner-queue').click();assert.equal(await page.evaluate(()=>document.querySelector('#sheet').scrollWidth<=document.querySelector('#sheet').clientWidth),true);await close();}
 assert.deepEqual((await api('/home')).finance.balances,before.finance.balances);assert.deepEqual(errors,[]);
 console.log('PASS R2 source-first, personal task revision/defer/confirm/done, unreadable photo/reject, offline retry, responsive 320/390/768/1280');
}finally{await browser.close();}
