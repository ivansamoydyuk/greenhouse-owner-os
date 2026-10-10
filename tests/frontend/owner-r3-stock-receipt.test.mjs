import test from 'node:test';
import assert from 'node:assert/strict';
import {initialState} from '../../prototype/owner-r3/fixtures.js';
import {ensureDaily,projectDaily,ensureReceiptDraft,createProduct,similarProducts,createAction,updateAction,confirmAction,overview} from '../../prototype/owner-r3/daily-demo.js';
import {quickForm,dailyReview} from '../../prototype/owner-r3/daily-ui.js';
import {fertilizerProducts} from '../../prototype/owner-r3/fertilizer-demo.js';
const fresh=()=>{const s=initialState();ensureDaily(s);projectDaily(s);return s;};
const request=(s,values={})=>createAction(s,'inventory-arrival',{requestId:'receipt-1',productId:'kno3',locationId:'stock-location-yesiloz',quantity:'12.125',date:'2026-10-09',...values});
test('catalog is global and configured locations remain available without any stock rows',()=>{
 const s=fresh();assert.equal(s.daily.products.filter(p=>p.name==='KNO₃').length,1);
 assert.equal(s.stock[0].productId,s.stock[1].productId);const config=structuredClone(s.daily.storageLocations);s.daily.inventory=[];projectDaily(s);
 assert.deepEqual(s.daily.storageLocations,config);assert.equal(s.daily.products.length,3);
 const a=request(s);assert.equal(a.canConfirm,true);confirmAction(s,a.id);assert.equal(s.stock[0].quantityMinor,null);
});
test('new product requires only name and supported base unit, creates no stock or financial effect',()=>{
 const s=fresh(),before=structuredClone(s.stock),money=overview(s);const p=createProduct(s,{requestId:'p-1',name:'DEMO Магний',baseUnit:'kg'});
 assert.equal(p.unit,'кг');assert.equal(p.source.kind,'owner-entry');assert.deepEqual(s.stock,before);assert.deepEqual(overview(s),money);
 for(const baseUnit of ['',null,'bag','м²'])assert.throws(()=>createProduct(s,{requestId:'bad-'+baseUnit,name:'Other',baseUnit}),/единицу/);
 assert.throws(()=>createProduct(s,{requestId:'empty',name:' ',baseUnit:'kg'}),/название/);
});
test('exact normalized names and aliases are rejected; similar names are only suggestions',()=>{
 const s=fresh();s.daily.products[0].aliases=['Potassium'];
 assert.throws(()=>createProduct(s,{requestId:'a',name:' KNO3 ',baseUnit:'kg'}),/уже есть/);
 assert.throws(()=>createProduct(s,{requestId:'b',name:'POTASSIUM',baseUnit:'L'}),/уже есть/);
 assert.equal(similarProducts(s,'Кальциевая').length,1);
 const other=createProduct(s,{requestId:'c',name:'Кальциевая селитра DEMO другой состав',baseUnit:'kg'});assert.notEqual(other.id,'calcium');
});
test('product request identity survives serialize/reload/retry and detects changed request payload',()=>{
 let s=fresh();const input={requestId:'stable-product',name:'DEMO Бор',baseUnit:'L'};const p=createProduct(s,input);
 s=JSON.parse(JSON.stringify(s));assert.equal(createProduct(s,input).id,p.id);assert.equal(s.daily.products.filter(x=>x.requestId===input.requestId).length,1);
 assert.throws(()=>createProduct(s,{...input,baseUnit:'kg'}),/другими данными/);
});
test('first receipt on another configured location records exact movement but preserves unknown opening and cost',()=>{
 const s=fresh(),money=overview(s),a=request(s);assert.equal(a.canConfirm,true);assert.equal(s.stock.length,4);confirmAction(s,a.id,a.revision);
 const row=s.stock.find(r=>r.id===a.result.stockId);assert.equal(row.productId,'kno3');assert.equal(row.locationId,'stock-location-yesiloz');assert.equal(row.unit,'кг');
 assert.equal(row.quantity,null);assert.equal(row.quantityMinor,null);assert.equal(row.cost,null);assert.equal(row.costMinor,null);assert.equal(row.movements[0].quantityMinor,12125);
 for(const key of ['revenueMinor','operatingExpenseMinor','cashPaidMinor','payableMinor'])assert.equal(overview(s)[key],money[key]);
 assert.equal(s.daily.products.filter(p=>p.name==='KNO₃').length,1);assert.equal(s.daily.invoices[0].arrivalConfirmed,false);
 const projected=fertilizerProducts(s).find(p=>p.id===row.id);assert.equal(projected.productId,'kno3');assert.equal(projected.stockLocationId,row.locationId);
});
test('configured foreign-owner and unconfigured destinations are rejected, even with stock',()=>{
 const s=fresh();s.daily.storageLocations.push({id:'foreign',name:'Other',ownerId:'other-owner',configured:true},{id:'inactive',ownerId:s.daily.ownerId,configured:false});
 for(const locationId of ['missing','foreign','inactive']){const a=request(s,{requestId:locationId,locationId});assert.equal(a.canConfirm,false);assert.throws(()=>confirmAction(s,a.id),/NEEDS_INPUT/);}
});
test('base unit is product-owned with no implicit conversion; missing or cleared selections require clarification',()=>{
 const s=fresh(),p=createProduct(s,{requestId:'p-litres',name:'DEMO жидкое',baseUnit:'L'}),a=request(s,{productId:p.id,unit:'kg'});assert.equal(a.input.unit,'л');
 confirmAction(s,a.id);assert.equal(s.stock.at(-1).unit,'л');assert.equal(s.stock.at(-1).movements[0].quantity,12.125);
 const cleared=request(s,{requestId:'clear',stockId:'kno3',productId:'',locationId:''});assert.equal(cleared.canConfirm,false);
});
test('legacy drafts and confirmations migrate additively without losing sources or unknown legacy units',()=>{
 const s=fresh();delete s.daily.products;delete s.daily.storageLocations;s.dailyDrafts={'inventory-arrival':{stockId:'film-stock',quantity:'6',comment:'keep',sourceFile:{name:'keep.png',url:'data:image/png;base64,AAAA'}}};
 const original=structuredClone(s.dailyDrafts['inventory-arrival']),d=ensureReceiptDraft(s);assert.equal(d.productId,'film-stock');assert.equal(d.locationId,'stock-location-imamli');
 for(const k of Object.keys(original))assert.deepEqual(d[k],original[k]);const id=d.requestId;assert.equal(ensureReceiptDraft(JSON.parse(JSON.stringify(s))).requestId,id);
 const a=createAction(s,'inventory-arrival',d);confirmAction(s,a.id);assert.equal(s.stock.find(r=>r.id==='film-stock').unit,'м²');assert.equal(s.stock.find(r=>r.id==='film-stock').quantity,null);
});
test('receipt retry/double confirm/reload preserves single source and movement with stable identities',()=>{
 let s=fresh();const values={requestId:'retry',productId:'kno3',locationId:'stock-location-imamli',quantity:'1',date:'2026-10-09'};const a=createAction(s,'inventory-arrival',values),id=a.id;
 assert.equal(createAction(s,'inventory-arrival',values).id,id);confirmAction(s,id,a.revision);confirmAction(s,id,1);s=JSON.parse(JSON.stringify(s));confirmAction(s,id,1);
 assert.equal(createAction(s,'inventory-arrival',values).id,id);assert.equal(s.daily.sources.filter(x=>x.id===a.sourceId).length,1);assert.equal(s.stock[0].movements.filter(m=>m.id.startsWith(id+'-')).length,1);assert.equal(s.stock[0].quantity,181);
 assert.throws(()=>createAction(s,'inventory-arrival',{...values,quantity:'2'}),/уже сохранён/);
});
test('stale review rejected and original attached source/comment remain immutable through correction',()=>{
 const s=fresh(),file={name:'DEMO.png',kind:'image',url:'data:image/png;base64,AAAA'},a=request(s,{raw:'DEMO source',comment:'original',sourceFile:file}),source=structuredClone(s.daily.sources.find(x=>x.id===a.sourceId)),revision=a.revision;
 file.url='changed';updateAction(s,a.id,{quantity:'7',comment:'owner correction',sourceFile:{name:'changed'}});assert.throws(()=>confirmAction(s,a.id,revision),/STALE/);confirmAction(s,a.id,a.revision);
 assert.deepEqual(s.daily.sources.find(x=>x.id===a.sourceId),source);assert.equal(source.file.url,'data:image/png;base64,AAAA');assert.equal(source.comment,'original');
});
test('receipt form separates product/location, never offers editable unit, review retains accessible attachment',()=>{
 const s=fresh();ensureReceiptDraft(s);let html=quickForm(s,'inventory-arrival');assert.match(html,/daily-productId/);assert.match(html,/daily-locationId/);assert.doesNotMatch(html,/daily-stockId|name="unit"/);assert.match(html,/receipt-file/);
 const a=request(s,{sourceFile:{name:'DEMO.png',kind:'image',url:'data:image/png;base64,AAAA'}});html=dailyReview(s,a.id);for(const value of ['Товар','Склад','Количество','Дата','KNO₃','Yeşilöz','12.125 кг','Неизвестна','receipt-source-image'])assert.ok(html.includes(value),value);
});
test('pending source file disables review and explains why',()=>{
 const s=fresh();ensureReceiptDraft(s);const html=quickForm(s,'inventory-arrival',undefined,{fileReading:true});assert.match(html,/receipt-file-reading/);assert.match(html,/data-testid="daily-save"[^>]*disabled/);
});
test('replacement attachment is visible separately while original remains immutable',()=>{
 const s=fresh(),a=request(s,{sourceFile:{name:'original.png',kind:'image',url:'data:image/png;base64,AAAA'}});updateAction(s,a.id,{sourceFile:{name:'corrected.png',kind:'image',url:'data:image/png;base64,BBBB'}});const html=dailyReview(s,a.id);assert.match(html,/original.png/);assert.match(html,/corrected.png/);assert.match(html,/receipt-correction-source/);
});
