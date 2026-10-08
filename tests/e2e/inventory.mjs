// Synthetic owner story: UI -> authenticated HTTP -> PostgreSQL -> visible stock.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {createRequire} from 'node:module';
const {chromium}=createRequire(import.meta.url)('playwright');
const base=process.env.PREVIEW_URL||'http://127.0.0.1:4173';
const browser=await chromium.launch({headless:true,args:['--no-sandbox'],...(process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{})});
const ctx=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
const page=await ctx.newPage(),errors=[];page.on('pageerror',x=>errors.push(x.message));
const sheet=()=>page.locator('#sheet-content'),click=n=>sheet().getByRole('button',{name:n,exact:true}).click(),close=async()=>{await click('Закрыть');await page.locator('#sheet').waitFor({state:'hidden'});};
async function api(p,b,key=crypto.randomUUID()){const r=b===undefined?await ctx.request.get(base+p):await ctx.request.post(base+p,{headers:{'Idempotency-Key':key},data:b});assert.ok(r.ok(),await r.text());return r.json();}
let eid,root,p,l,b,g,initial,receipt,application,transfer;
const view=()=>api(root),amount=async location=>(await view()).stock.filter(x=>x.product_id===p.id&&(!location||x.location_id===location)).reduce((a,x)=>a+Number(x.quantity),0);
async function fill(kind,q,product=p.id){await page.locator(`[data-inew=${kind}]`).click();await sheet().locator('#ip-0').selectOption(product);await sheet().locator('#iq-0').fill(q);await sheet().locator('#iu-0').selectOption('kg');await sheet().locator('#il-0').selectOption(l.id);if(kind==='application')await sheet().locator('#ig-0').selectOption(g.id);if(kind==='transfer')await sheet().locator('#id-0').selectOption(b.id);}
async function save(){await click('Сохранить складской черновик');await sheet().getByRole('heading',{name:'Складское событие',exact:true}).waitFor();return (await view()).events[0];}
async function post(){await click('Провести физическое событие');await page.locator('#confirmation').getByRole('button',{name:'Подтвердить',exact:true}).click();await sheet().getByRole('heading',{name:/ · Проведено/}).waitFor();}
async function reverse(r){await close();await page.locator(`[data-ievent="${r.id}"]`).first().click();await click('Отменить событие');await sheet().getByLabel('Причина отмены').fill('TEST E2E cleanup');await click('Подтвердить отмену');await sheet().getByRole('heading',{name:/ · Отменено/}).waitFor();}
try{
 await page.goto(base);await page.getByLabel('Ключ доступа').fill(process.env.PREVIEW_SECRET);await page.getByRole('button',{name:'Открыть Finance',exact:true}).click();await page.getByRole('heading',{name:'Финансы',exact:true}).waitFor();
 page.on('console',x=>{if(x.type()==='error'&&!/status of (422|409)/.test(x.text()))errors.push(x.text());});
 eid=(await api('/preview/config')).entity_id;root=`/v1/entities/${eid}/inventory`;initial=(await api(`/v1/entities/${eid}/finance-overview`)).balances;
 g=(await api(`/v1/entities/${eid}/finance-overview`)).work_areas.find(x=>x.name==='İmamlı');
 await page.locator('[data-page=inventory]').click();await page.getByRole('heading',{name:'Удобрения / Запасы',exact:true}).waitFor();
 // Master/location creation through actual owner forms.
 await page.getByRole('button',{name:'+ Товар',exact:true}).click();await sheet().getByLabel('Название',{exact:true}).fill('TEST E2E KNO3');await sheet().getByLabel('Проверенные алиасы через ;').fill('TEST калийной селитры');await sheet().getByLabel('Базовая единица').selectOption('kg');await sheet().getByLabel('Валюта управленческой оценки (если известна)').selectOption('TRY');await click('Сохранить товар');await sheet().waitFor({state:'hidden'});
 p=(await view()).products.find(x=>x.name==='TEST E2E KNO3');
 for(const name of ['TEST E2E A','TEST E2E B']){await page.getByRole('button',{name:'+ Склад',exact:true}).click();await sheet().getByLabel('Название').fill(name);await click('Сохранить склад');await sheet().waitFor({state:'hidden'});}
 const locations=(await view()).locations;l=locations.find(x=>x.name==='TEST E2E A');b=locations.find(x=>x.name==='TEST E2E B');
 await fill('receipt','100');await sheet().getByLabel('Цена за базовую единицу (если известна)').fill('10');receipt=await save();assert.equal(await amount(),0);await post();assert.equal(await amount(l.id),100);await close();
 console.log('PASS A: owner receipt draft -> explicit post -> stock 100 kg');
 await fill('application','15');application=await save();assert.equal(await amount(),100);await post();assert.equal(await amount(),85);await close();
 console.log('PASS B: owner application -> stock 85 kg, greenhouse attribution');
 await fill('application','999');const insufficient=await save();await click('Провести физическое событие');await page.locator('#confirmation').getByRole('button',{name:'Подтвердить',exact:true}).click();await sheet().locator('#i-error').getByText('Недостаточно товара на выбранном складе',{exact:true}).waitFor();assert.equal(await amount(),85);await close();
 console.log('PASS C: insufficient stock rejected, no physical effect');
 await fill('transfer','20');transfer=await save();await post();assert.equal(await amount(l.id),65);assert.equal(await amount(b.id),20);assert.equal(await amount(),85);await close();
 console.log('PASS D: atomic transfer, entity quantity unchanged');
 // Shared text path creates only an application draft.
 await page.locator('[data-page=overview]').click();await page.locator('#global-capture').click();await sheet().locator('#capture-text').fill('Сегодня в Имамлы дали 5 кг TEST E2E KNO3');await click('Разобрать и проверить');await sheet().getByRole('heading',{name:'Проверить складское сообщение',exact:true}).waitFor();await sheet().locator('#il-0').selectOption(l.id);const captureDraft=await save();assert.equal(captureDraft.status,'draft');assert.equal(await amount(),85);assert.ok(captureDraft.evidence.raw_text);await close();
 console.log('PASS E: shared text/voice transcript architecture -> draft only');
 await page.locator('[data-page=inbox]').click();await page.getByRole('button',{name:'+ Добавить',exact:true}).click();await sheet().getByLabel('Текст сообщения / расшифровка документа').fill('Сегодня привезли 7 кг TEST E2E KNO3');await sheet().getByLabel('Ссылка или номер источника (если есть)').fill('TEST:inventory:delivery:1');await click('Сохранить и проверить');await sheet().getByRole('heading',{name:'Проверка входящего',exact:true}).waitFor();await click('Проверить поля и создать черновик');await sheet().locator('#il-0').selectOption(l.id);const inboxDraft=await save();assert.equal(await amount(),85);
 const item=(await api(`/v1/entities/${eid}/inbox`)).items.find(x=>x.locator==='TEST:inventory:delivery:1');assert.equal(item.inventory_id,inboxDraft.id);
 const direct=await api(root+'/events',{...item.reviewed.reviewed,evidence:{source:item.source,locator:item.locator,raw_text:item.raw_text}});assert.equal(direct.id,inboxDraft.id);
 console.log('PASS F: Inbox -> Inventory draft, direct retry one authoritative event');
 await close();await page.locator('[data-page=inventory]').click();await page.locator(`[data-ievent="${transfer.id}"]`).first().click();await click('Отменить событие');await sheet().getByLabel('Причина отмены').fill('TEST E2E cleanup');await click('Подтвердить отмену');await sheet().getByRole('heading',{name:/ · Отменено/}).waitFor();assert.equal(await amount(l.id),85);assert.equal(await amount(b.id),0);
 await reverse(application);assert.equal(await amount(),100);await reverse(receipt);assert.equal(await amount(),0);await close();
 console.log('PASS G: immutable movement reversal restores exact quantity/cost');
 // Unknown product -> uncosted stock -> explicit one-time currency -> known receipt.
 const originalProduct=p;
 await page.getByRole('button',{name:'+ Товар',exact:true}).click();await sheet().getByLabel('Название',{exact:true}).fill('TEST E2E UNKNOWN KNO3');await sheet().getByLabel('Базовая единица').selectOption('kg');await click('Сохранить товар');await sheet().waitFor({state:'hidden'});
 p=(await view()).products.find(x=>x.name==='TEST E2E UNKNOWN KNO3');assert.equal(p.currency,null);
 await fill('receipt','100');const unknownReceipt=await save();await post();await close();
 await page.locator(`[data-iproduct="${p.id}"]`).first().click();await sheet().getByText('Валюта оценки: не установлена',{exact:true}).waitFor();await sheet().getByLabel('Установить валюту оценки один раз').selectOption('TRY');await sheet().getByLabel('Основание: проверенный документ / источник').fill('TEST verified supplier document');await click('Установить валюту оценки');await page.locator('#confirmation').getByRole('button',{name:'Подтвердить',exact:true}).click();await sheet().getByText('Валюта оценки: TRY',{exact:true}).waitFor();assert.equal(await sheet().locator('#i-currency-form').count(),0);await close();
 let stock=(await view()).stock.find(x=>x.product_id===p.id);assert.equal(stock.quantity,'100');assert.equal(stock.uncosted_quantity,'100');assert.equal(stock.known_cost,'0');
 await fill('receipt','100');await sheet().getByLabel('Цена за базовую единицу (если известна)').fill('20');const knownReceipt=await save();await post();
 stock=(await view()).stock.find(x=>x.product_id===p.id);assert.equal(stock.quantity,'200');assert.equal(stock.uncosted_quantity,'100');assert.equal(stock.known_cost,'2000');
 await reverse(knownReceipt);await reverse(unknownReceipt);await close();p=originalProduct;
 const crossKind=await ctx.request.post(base+root+'/events',{headers:{'Idempotency-Key':crypto.randomUUID()},data:{...item.reviewed.reviewed,kind:'application',evidence:{source:item.source,locator:item.locator,raw_text:item.raw_text}}});assert.equal(crossKind.status(),409);
 assert.equal((await view()).events.filter(x=>x.source===item.source&&x.locator===item.locator&&!x.replacement_of_id).length,1);
 console.log('PASS J/K: mobile currency establishment keeps old stock uncosted; Inbox cross-kind conflict');

 for(const width of [320,390,768,1280]){
  await page.setViewportSize({width,height:900});await page.locator('[data-page=inventory]').click();assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:`artifacts/inventory-${width}.png`,fullPage:true});
  await page.locator(`[data-ievent="${captureDraft.id}"]`).first().click();await sheet().getByRole('heading',{name:'Складское событие',exact:true}).waitFor();assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:`artifacts/inventory-detail-${width}.png`,fullPage:true});await close();await page.locator(`[data-iproduct="${p.id}"]`).first().click();await sheet().getByText('Валюта оценки: TRY',{exact:true}).waitFor();assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await close();
 }
 assert.deepEqual((await api(`/v1/entities/${eid}/finance-overview`)).balances,initial);assert.equal((await view()).invariants.ok,true);assert.equal((await api(`/v1/finance/reconciliation?entity_id=${eid}`)).ok,true);assert.deepEqual(errors,[]);
 console.log('PASS H/I: 320/390/768/1280, no console/runtime errors; Finance unchanged; Inventory invariants and reconciliation PASS');
 await fs.writeFile('artifacts/inventory-e2e.json',JSON.stringify({ok:true,widths:[320,390,768,1280],stock:0,financeUnchanged:true,errors},null,2));
}catch(err){await page.screenshot({path:'artifacts/inventory-failure.png',fullPage:true});throw err;}finally{await browser.close();}
