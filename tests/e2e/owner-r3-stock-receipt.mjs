// Synthetic receipt journeys. All business changes use UI actions; state reads are assertions only.
import assert from 'node:assert/strict';
import {syntheticPixelPng} from '../fixtures/owner-r3-media.mjs';
const stateOf=page=>page.evaluate(()=>window.r3Demo.state());
const moneyOf=s=>JSON.stringify([s.daily.expenses,s.daily.payments,s.daily.obligations,s.daily.receipts]);
export async function runStockReceiptJourneys({browser,engine,newPage,click,fill,ready,route,screenshot,layout,report,correctionJourney,correctionReload}) {
 report.stock_receipt_journeys??=[];report.stock_receipt_layouts??=[];
 const journey=async(name,run)=>{const start=report.correction_journeys.length;try{await correctionJourney(browser,engine,'Stock receipt: '+name,run);}finally{report.stock_receipt_journeys.push(...report.correction_journeys.splice(start));}};
 const go=async(page,id)=>{await click(page,id);await ready(page);};
 const select=async(page,id,value)=>{await page.getByTestId(id).selectOption(value);await page.getByTestId(id).blur();await ready(page);};
 const open=async page=>{await go(page,'nav-more');await go(page,'more-inventory');await go(page,'inventory-arrival');};
 const create=async(page,name,unit='kg')=>{await go(page,'receipt-product-open');await fill(page,'receipt-product-name',name);await select(page,'receipt-product-unit',unit);await go(page,'receipt-product-create');return (await stateOf(page)).daily.products.find(p=>p.name===name);};
 const review=async page=>{await go(page,'daily-save');await page.getByTestId('daily-original').waitFor();return (await stateOf(page)).daily.actions[0];};
 const confirm=async page=>{await go(page,'daily-confirm');await page.getByTestId('daily-confirmed').waitFor();};
 const file={name:'DEMO-receipt.png',mimeType:'image/png',buffer:syntheticPixelPng()};
 const attach=async page=>{const details=page.locator('details').filter({has:page.getByTestId('receipt-file')});if(!await details.getAttribute('open'))await details.locator('summary').click();await page.getByTestId('receipt-file').setInputFiles(file);await page.getByTestId('receipt-source-image').waitFor();await page.getByTestId('receipt-source-image').evaluate(img=>img.decode());};
 const assertDraft=async(page,expected)=>{const s=await stateOf(page),draft=s.dailyDrafts['inventory-arrival'];for(const [key,value]of Object.entries(expected))assert.deepEqual(draft[key],value,key+' survives');return draft;};
 await journey('new product; filled form, cancel/create, attachment and one DEMO confirmation survive reload',async page=>{
  await open(page);const original=await stateOf(page),money=moneyOf(original);
  await select(page,'daily-productId','kno3');await select(page,'daily-locationId','stock-location-yesiloz');await fill(page,'daily-quantity','12.125');await fill(page,'daily-date','2026-10-08');await attach(page);await fill(page,'daily-comment','DEMO коробка повреждена');await fill(page,'daily-raw','DEMO накладная: получено 12.125 кг');
  const before=structuredClone((await stateOf(page)).dailyDrafts['inventory-arrival']);
  await go(page,'receipt-product-open');await fill(page,'receipt-product-name','Не сохранённый товар');await select(page,'receipt-product-unit','L');await go(page,'receipt-product-cancel');
  await assertDraft(page,Object.fromEntries(['productId','locationId','quantity','date','comment','raw','sourceFile','requestId'].map(k=>[k,before[k]])));
  assert.equal((await stateOf(page)).daily.products.length,original.daily.products.length);
  const product=await create(page,'DEMO Сульфат магния');assert.equal(await page.getByTestId('daily-productId').inputValue(),product.id);
  await assertDraft(page,Object.fromEntries(['locationId','quantity','date','comment','raw','sourceFile','requestId'].map(k=>[k,before[k]])));
  assert.deepEqual((await stateOf(page)).stock,original.stock);assert.equal(moneyOf(await stateOf(page)),money);
  await correctionReload(page);await page.getByTestId('receipt-source-image').evaluate(img=>img.decode());assert.equal(await page.getByTestId('daily-productId').inputValue(),product.id);
  const action=await review(page),src=structuredClone((await stateOf(page)).daily.sources.find(s=>s.id===action.sourceId));assert.equal(src.file.url,before.sourceFile.url);assert.equal(src.comment,before.comment);
  assert.match(await page.locator('#main').innerText(),/DEMO Сульфат магния/);assert.match(await page.locator('#main').innerText(),/Yeşilöz/);assert.match(await page.locator('#main').innerText(),/12.125 кг/);
  await screenshot(page,engine,'stock-receipt-new-review',390,'New product, independent warehouse, exact quantity and original attachment');
  await page.getByTestId('daily-confirm').click({clickCount:2});await page.getByTestId('daily-confirmed').waitFor();await correctionReload(page);
  const after=await stateOf(page),row=after.stock.find(r=>r.productId===product.id);assert.equal(row.quantity,null);assert.equal(row.quantityMinor,null);assert.equal(row.costMinor,null);assert.equal(row.movements.length,1);assert.equal(row.movements[0].quantityMinor,12125);
  assert.equal(after.daily.actions.filter(a=>a.requestId===before.requestId).length,1);assert.deepEqual(after.daily.sources.find(s=>s.id===src.id),src);assert.equal(moneyOf(after),money);await page.getByTestId('receipt-source-image').evaluate(img=>img.decode());
 });
 await journey('existing global product first arrival on another configured warehouse; no duplicate product',async page=>{
  await open(page);const before=await stateOf(page);await select(page,'daily-productId','kno3');await select(page,'daily-locationId','stock-location-yesiloz');await fill(page,'daily-quantity','7.5');const a=await review(page);await confirm(page);
  const after=await stateOf(page),row=after.stock.find(r=>r.id===after.daily.actions.find(x=>x.id===a.id).result.stockId);assert.equal(row.productId,'kno3');assert.equal(row.locationId,'stock-location-yesiloz');assert.equal(row.quantity,null);assert.equal(row.movements[0].quantityMinor,7500);assert.equal(after.daily.products.length,before.daily.products.length);assert.equal(moneyOf(after),moneyOf(before));
  await go(page,'daily-result-open');await go(page,'stock-'+row.id);assert.match(await page.locator('#main').innerText(),/Остаток неизвестен/);await go(page,'stock-movements');assert.match(await page.locator('#main').innerText(),/7,5 кг/);await correctionReload(page);assert.equal((await stateOf(page)).stock.filter(r=>r.productId==='kno3').length,3);
 });
 await journey('similar names require owner choice; validation, Back and reopen preserve drafts',async page=>{
  await open(page);await select(page,'daily-locationId','stock-location-ozvadi');await fill(page,'daily-quantity','9');await go(page,'receipt-product-open');await go(page,'receipt-product-create');assert.match(await page.getByTestId('receipt-product-error').innerText(),/название/);
  await fill(page,'receipt-product-name','Кальциевая');await select(page,'receipt-product-unit','kg');assert.match(await page.getByTestId('receipt-product-suggestions').innerText(),/Кальциевая селитра/);
  const before=(await stateOf(page)).daily.products.length;await go(page,'receipt-product-use-calcium');assert.equal((await stateOf(page)).daily.products.length,before);assert.equal(await page.getByTestId('daily-productId').inputValue(),'calcium');
  await go(page,'receipt-product-open');await fill(page,'receipt-product-name','Кальциевая селитра');await select(page,'receipt-product-unit','kg');await go(page,'receipt-product-create');assert.match(await page.getByTestId('receipt-product-error').innerText(),/уже есть/);assert.equal((await stateOf(page)).daily.products.length,before);
  await go(page,'receipt-product-cancel');await go(page,'back');await go(page,'inventory-arrival');assert.equal(await page.getByTestId('daily-quantity').inputValue(),'9');assert.equal(await page.getByTestId('daily-locationId').inputValue(),'stock-location-ozvadi');
  await select(page,'daily-productId','');await review(page);assert.equal(await page.getByTestId('daily-confirm').count(),0);assert.match(await page.getByTestId('daily-clarifications').innerText(),/Выберите товар/);await go(page,'daily-edit');await select(page,'daily-productId','calcium');await review(page);await confirm(page);
 });
 await journey('quota retry and rejected files preserve one product, one action and attached source',async page=>{
  await open(page);await select(page,'daily-locationId','stock-location-imamli');await fill(page,'daily-quantity','2');await attach(page);const original=(await stateOf(page)).dailyDrafts['inventory-arrival'].sourceFile;
  await page.getByTestId('receipt-file').setInputFiles({name:'too-big.png',mimeType:'image/png',buffer:Buffer.alloc(512*1024+1)});assert.deepEqual((await stateOf(page)).dailyDrafts['inventory-arrival'].sourceFile,original);
  await page.getByTestId('receipt-file').setInputFiles({name:'not-image.txt',mimeType:'text/plain',buffer:Buffer.from('invalid')});assert.deepEqual((await stateOf(page)).dailyDrafts['inventory-arrival'].sourceFile,original);
  await create(page,'DEMO Ретрай');const before=await stateOf(page);
  await page.evaluate(()=>{const original=Storage.prototype.setItem;window.__restoreReceiptStorage=()=>Storage.prototype.setItem=original;Storage.prototype.setItem=function(){throw new DOMException('Synthetic quota','QuotaExceededError');};});
  await go(page,'daily-save');await page.getByTestId('storage-warning').waitFor();await go(page,'daily-save');assert.equal((await stateOf(page)).daily.actions.length,before.daily.actions.length+1);
  await page.evaluate(()=>window.__restoreReceiptStorage());const a=await review(page);await correctionReload(page);await confirm(page);const after=await stateOf(page);assert.equal(after.daily.actions.filter(x=>x.id===a.id).length,1);assert.equal(after.daily.products.filter(p=>p.name==='DEMO Ретрай').length,1);assert.equal(after.stock.flatMap(r=>r.movements).filter(m=>m.sourceId===a.sourceId).length,1);assert.equal(after.daily.sources.find(s=>s.id===a.sourceId).file.url,original.url);
 });
 await journey('pending source read blocks review; corrected attachment remains separate from original',async page=>{
  await open(page);await select(page,'daily-productId','kno3');await select(page,'daily-locationId','stock-location-imamli');await fill(page,'daily-quantity','4');
  await page.locator('details').filter({has:page.getByTestId('receipt-file')}).locator('summary').click();
  await page.evaluate(()=>{const read=FileReader.prototype.readAsDataURL;FileReader.prototype.readAsDataURL=function(file){setTimeout(()=>read.call(this,file),700);};});
  await page.getByTestId('receipt-file').setInputFiles(file);await page.getByTestId('receipt-file-reading').waitFor();assert.equal(await page.getByTestId('daily-save').isDisabled(),true);assert.equal((await stateOf(page)).daily.actions.length,0);
  await page.getByTestId('receipt-file-reading').waitFor({state:'detached'});await page.getByTestId('receipt-source-image').evaluate(img=>img.decode());const a=await review(page),source=structuredClone((await stateOf(page)).daily.sources.find(s=>s.id===a.sourceId));
  await go(page,'daily-edit');await page.getByTestId('receipt-file').setInputFiles({...file,name:'DEMO-correction.png'});await page.getByTestId('receipt-file-reading').waitFor({state:'detached'});await review(page);
  await page.getByTestId('receipt-correction-source').waitFor();assert.match(await page.getByTestId('receipt-correction-source').innerText(),/DEMO-correction.png/);assert.equal(await page.getByTestId('receipt-source-image').count(),2);
  await correctionReload(page);assert.deepEqual((await stateOf(page)).daily.sources.find(s=>s.id===source.id),source);assert.match(await page.getByTestId('receipt-correction-source').innerText(),/DEMO-correction.png/);await confirm(page);
 });
 await journey('primary 390/320 and seven-width long-label form, editor, review and stock geometry',async page=>{
  await open(page);const name='DEMO Комплексное водорастворимое удобрение для отдельного длительного названия';await create(page,name,'piece');await select(page,'daily-locationId','stock-location-yesiloz');await fill(page,'daily-quantity','1250.125');
  for(const width of [390,320,700,768,820,900,1280]) {
   await page.setViewportSize({width,height:844});await ready(page);await layout(page,'quick/inventory-arrival',width);await screenshot(page,engine,'stock-receipt-form',width,'Independent product / configured storage / quantity / product-owned unit');
   await go(page,'receipt-product-open');await fill(page,'receipt-product-name','DEMO Другое удобрение с очень длинным названием без обрезки кнопок');await select(page,'receipt-product-unit','L');await layout(page,'quick/inventory-arrival',width);await screenshot(page,engine,'stock-receipt-editor',width,'Inline new-product editor with readable long labels and buttons');await go(page,'receipt-product-cancel');
   report.stock_receipt_layouts.push({engine,width,screen:'form-and-inline-editor',status:'PASS'});
  }
  const a=await review(page);
  for(const width of [390,320,700,768,820,900,1280]) {await page.setViewportSize({width,height:844});await layout(page,'daily/'+a.id,width);await screenshot(page,engine,'stock-receipt-review',width,'Short receipt review with long product label, quantity and piece unit');report.stock_receipt_layouts.push({engine,width,screen:'review',status:'PASS'});}
  await confirm(page);await go(page,'daily-result-open');
  for(const width of [390,320,700,768,820,900,1280]) {await page.setViewportSize({width,height:844});await layout(page,'inventory',width);await screenshot(page,engine,'stock-receipt-inventory',width,'Long product and UNKNOWN balance remain distinct');report.stock_receipt_layouts.push({engine,width,screen:'inventory',status:'PASS'});}
 });
}
