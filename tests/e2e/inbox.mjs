import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {chromium} from 'playwright';
const base=process.env.PREVIEW_URL||'http://127.0.0.1:4173';
const browser=await chromium.launch({headless:true,args:['--no-sandbox'],...(process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{})});
const context=await browser.newContext({viewport:{width:390,height:844}}),page=await context.newPage(),errors=[];
page.on('pageerror',x=>errors.push(x.message));
const sheet=()=>page.locator('#sheet-content'),click=n=>sheet().getByRole('button',{name:n,exact:true}).click(),close=()=>click('Закрыть');
async function api(p,b,key=crypto.randomUUID()){const response=b===undefined?await context.request.get(base+p):await context.request.post(base+p,{headers:{'Idempotency-Key':key},data:b});assert.ok(response.ok(),await response.text());return response.json();}
let eid,root,initial,financeItem;
async function view(){return api(`/v1/entities/${eid}/finance-overview?limit=100`);}
async function add(text,locator,file){await page.getByRole('button',{name:'+ Добавить',exact:true}).click();if(text)await sheet().getByLabel('Текст сообщения / расшифровка документа').fill(text);if(locator)await sheet().getByLabel('Ссылка или номер источника (если есть)').fill(locator);if(file)await sheet().locator('#inbox-file').setInputFiles(file);await click('Сохранить и проверить');await sheet().getByRole('heading',{name:'Проверка входящего',exact:true}).waitFor();}
try{
 await page.goto(base);await page.getByLabel('Ключ доступа').fill(process.env.PREVIEW_SECRET);await page.getByRole('button',{name:'Открыть Finance',exact:true}).click();await page.getByRole('heading',{name:'Финансы',exact:true}).waitFor();
 // The signed-out bootstrap intentionally receives 401; record all console
 // errors during the authenticated owner workflows.
 page.on('console',x=>{if(x.type()==='error')errors.push(x.text());});
 eid=(await api('/preview/config')).entity_id;root=`/v1/entities/${eid}/inbox`;initial=await view();
 await page.locator('[data-page=inbox]').click();await page.getByRole('heading',{name:'Входящие',exact:true}).waitFor();
 await add('Сегодня заплатил Серкану 2600 лир из Cash TR','TEST:manual:1');
 let queue=await api(root);financeItem=queue.items[0];
 assert.equal(financeItem.normalized.amount,'2600');await click('Проверить поля и создать черновик');await sheet().getByRole('heading',{name:'Проверить сообщение',exact:true}).waitFor();
 await click('Подтвердить данные и создать черновик');await page.locator('#confirmation').getByRole('button',{name:'Подтвердить',exact:true}).click();await sheet().getByText('Черновик',{exact:true}).waitFor();
 assert.deepEqual((await view()).balances,initial.balances);await close();
 const confirmed=await api(root+'/'+financeItem.id);assert.equal(confirmed.state,'CONFIRMED');assert.ok(confirmed.capture_id);
 const retry=await api(root,{raw_text:financeItem.raw_text,locator:'TEST:manual:1'});assert.equal(retry.id,confirmed.id);assert.equal(retry.capture_id,confirmed.capture_id);
 const capture=await api(`/v1/entities/${eid}/captures/${confirmed.capture_id}`),payment=await api('/v1/payments/'+capture.finance_id);assert.equal(payment.status,'draft');
 console.log('PASS A/D: Inbox text -> owner review -> Finance draft, no automatic posting; stable retry reuses source and draft');

 const depot=await api(`/v1/entities/${eid}/counterparties`,{name:'TEST Inbox Depot',role:'depot',source:'test:inbox-e2e'});await page.getByRole('button',{name:'Обновить',exact:true}).click();
 await add('Сегодня депо TEST Inbox Depot Grand 100 кг по 40 лир','TEST:depot:1',{name:'depot.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF-1.7 TEST Inbox original')});
 const receiptItem=(await api(root)).items[0];await click('Проверить поля и создать черновик');await sheet().getByLabel('Депо',{exact:true}).selectOption(depot.id);await sheet().getByLabel('Alacak (не оплата)',{exact:true}).fill('4000');
 await click('Сохранить и проверить черновик');await sheet().getByRole('heading',{name:'Расчёт депо',exact:true}).waitFor();await sheet().getByText('Черновик',{exact:true}).waitFor();
 const r=await api(root+'/'+receiptItem.id),receipt=await api(`/v1/entities/${eid}/harvest/settlements/${r.harvest_id}`);assert.equal(receipt.status,'draft');assert.equal(receipt.payment_id,null);assert.equal(receipt.obligation_id,null);assert.equal(receipt.alacak_value,'4000.00');assert.equal(receipt.lines[0].normalized_quality,'FIRST');assert.deepEqual((await view()).balances,initial.balances);await close();
 console.log('PASS B: uploaded depot evidence + labelled owner transcription -> authoritative Harvest draft; Alacak creates no cash/sale');

 await add('<img src=x onerror="window.inboxXss=true"> неизвестный документ','TEST:unknown:1',{name:'чек<img onerror=window.inboxFilenameXss=true>.png',mimeType:'image/png',buffer:Buffer.from([137,80,78,71,13,10,26,10,0])});
 await sheet().getByText('Нужна проверка',{exact:true}).waitFor();assert.equal(await sheet().locator('#inbox-review').count(),0);assert.equal(await page.evaluate(()=>window.inboxXss),undefined);
 assert.equal(await page.evaluate(()=>window.inboxFilenameXss),undefined);assert.equal(await sheet().locator('img').count(),0);const unknown=(await api(root)).items[0];assert.equal(unknown.route,'UNRESOLVED');assert.equal(unknown.normalized.amount,undefined);
 await click('Отклонить');await sheet().getByLabel('Причина',{exact:true}).fill('TEST unknown rejected');await click('Подтвердить отклонение');await sheet().getByText('Отклонён',{exact:true}).waitFor();await close();assert.deepEqual((await view()).balances,initial.balances);
 console.log('PASS C: unknown/XSS text -> review required -> no guessed route/values/effect; auditable rejection');

 // Binary-only input must never claim OCR.
 await add('', 'TEST:binary-only:1',{name:'scan.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF-1.7 TEST no transcription')});
 assert.equal(await sheet().locator('#inbox-review').count(),0);await sheet().getByText('Текст отсутствует',{exact:true}).waitFor();await close();
 for(const width of [320,390,768,1280]){
  await page.setViewportSize({width,height:844});await page.locator('[data-page=inbox]').click();await page.getByRole('heading',{name:'Входящие',exact:true}).waitFor();
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Inbox overflow '+width);await page.screenshot({path:`artifacts/inbox-${width}.png`,fullPage:true,animations:'disabled'});
  await page.locator(`[data-inbox="${receiptItem.id}"]`).click();await sheet().getByRole('heading',{name:'Проверка входящего',exact:true}).waitFor();await page.screenshot({path:`artifacts/inbox-detail-${width}.png`,fullPage:true,animations:'disabled'});assert.ok(await sheet().evaluate(n=>n.scrollWidth<=n.clientWidth+1),JSON.stringify(await sheet().evaluate(n=>({width:n.clientWidth,scroll:n.scrollWidth,elements:[...n.querySelectorAll('*')].filter(x=>x.getBoundingClientRect().right>n.getBoundingClientRect().right+1).map(x=>({tag:x.tagName,text:x.innerText?.slice(0,60),width:x.getBoundingClientRect().width}))})))+' Inbox detail overflow '+width);await page.screenshot({path:`artifacts/inbox-detail-${width}.png`,fullPage:true,animations:'disabled'});await close();
 }
 assert.deepEqual(errors,[]);await fs.writeFile('artifacts/inbox-e2e.json',JSON.stringify({status:'PASS',widths:[320,390,768,1280],errors,source:financeItem.id,posted:false},null,2));console.log('PASS E/F: Inbox/detail 320/390/768/1280, no overflow or console/runtime errors');
}finally{await browser.close();}
