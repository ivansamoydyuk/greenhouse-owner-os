// Isolated real-browser UI regression. Fake API responses are deliberate gates;
// PostgreSQL/effect coverage remains in owner-r2.mjs and integration tests.
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {chromium} from 'playwright';
const web=fileURLToPath(new URL('../../src/greenhouse_owner_os/web/',import.meta.url));
const html='<!doctype html><html><body><button id="owner-queue">Очередь</button><button id="owner-sources">Источники</button><button id="owner-local">На телефоне</button><p id="owner-pending-state"></p><nav id="nav"><button data-page="tasks">Задачи</button></nav><dialog id="sheet"><div id="sheet-content"></div></dialog></body></html>';
const server=createServer(async(req,res)=>{try{if(req.url==='/'){res.setHeader('Content-Type','text/html');res.end(html);return;}if(!/^\/[a-z0-9-]+\.js$/.test(req.url)){res.writeHead(404).end();return;}res.setHeader('Content-Type','text/javascript');res.end(await readFile(web+req.url.slice(1)));}catch{res.writeHead(404).end();}});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const browser=await chromium.launch({headless:true,args:['--no-sandbox'],...(process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{})});
const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
const base=`http://127.0.0.1:${server.address().port}`;
async function setup(){
 await page.goto(base);
 await page.evaluate(async()=>{
  await new Promise((resolve,reject)=>{const r=indexedDB.deleteDatabase('owner-source-pending-v1');r.onsuccess=resolve;r.onerror=reject;});
  Object.defineProperty(navigator,'onLine',{configurable:true,get:()=>false});
  const {ownerR2,pendingWrite}=await import('/owner-r2.js');
  window._write=pendingWrite;window._held=[];window._notices=[];window._delay='';
  window._data={owner:{owner_id:'owner-a',finance:{references:[{id:'party',kind:'counterparty',original_name:'TEST Серкан'},{id:'try-account',kind:'money_account',original_name:'Cash TR',currency:'TRY'},{id:'uah-account',kind:'money_account',original_name:'TEST UAH',currency:'UAH'}],work_areas:[{id:'greenhouse',kind:'greenhouse',name:'İmamlı'}],obligations:[{obligation_id:'try-debt',counterparty_id:'party',direction:'payable',description:'TRY debt',remaining_amount:'1000.00',currency:'TRY'},{obligation_id:'uah-debt',counterparty_id:'party',direction:'payable',description:'UAH debt',remaining_amount:'1000.00',currency:'UAH'}]}},tasks:{workers:[]},inventory:{products:[],locations:[]}};
  window._proposal=id=>({id,version:1,state:'READY',action_type:'PAYMENT',revision:{payload:{description:'TEST '+id,amount:'100.00',currency:'TRY',event_date:'2026-10-08',counterparty_id:'party',money_account_id:'try-account',obligation_id:'try-debt',attributions:[]},missing:[],basis:{debt_remaining:{remaining_amount:'1000.00',currency:'TRY'}},preview_digest:'a'.repeat(64)},source:{text:'PRIVATE ORIGINAL '+id,has_file:false}});
  const api=async(path,body)=>{
   if(window._delay&&path.includes(window._delay))return new Promise((resolve,reject)=>window._held.push({path,body,resolve,reject}));
   if(path.includes('/confirmations?'))return {items:[_proposal('old'),_proposal('new')],next_offset:null,preparing:0};
   if(path.endsWith('/preview'))return _proposal(path.includes('/new/')?'new':'old');
   if(path.includes('/captures?'))return {items:[]};
   throw Error('Unexpected fixture request: '+path);
  };
  const openSheet=(title,html)=>{const content=document.querySelector('#sheet-content'),sheet=document.querySelector('#sheet');content.innerHTML=`<button id="close">Закрыть</button><h2 id="title">${title}</h2>`+html;content.querySelector('#close').onclick=()=>sheet.close();if(!sheet.open)sheet.showModal();};
  window._owner=ownerR2({api,openSheet,reload:async()=>{},getData:()=>_data,getEntity:()=> 'entity',notice:t=>_notices.push(t),advanced:()=>{}});_owner.bind();
 });
 // Allow the initial empty pending sync to finish before introducing fixtures.
 await page.waitForFunction(()=>document.querySelector('#owner-pending-state').textContent==='Неотправленных источников нет');
}
const openQueue=async()=>{await page.locator('#owner-queue').click();await page.locator('[data-r2-open=old]').waitFor();};
const waitHeld=()=>page.waitForFunction(()=>_held.length>0);
const release=payload=>page.evaluate(value=>{_held.shift().resolve(value);},payload);
const settle=()=>page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
try{
 await setup();await openQueue();await page.evaluate(()=>_delay='/old/preview');await page.locator('[data-r2-open=old]').click();await waitHeld();await page.locator('#close').click();await page.locator('[data-r2-mode=text]').click();await page.locator('textarea').fill('NEW unsaved editor');await release(await page.evaluate(()=>_proposal('old')));await settle();assert.equal(await page.locator('#title').textContent(),'Что произошло?');assert.equal(await page.locator('textarea').inputValue(),'NEW unsaved editor');assert.ok(!(await page.locator('#sheet').innerText()).includes('PRIVATE ORIGINAL'));
 // A newer card/editor generation wins even while the older preview is pending.
 await setup();await openQueue();await page.evaluate(()=>_delay='/old/preview');await page.locator('[data-r2-open=old]').click();await waitHeld();await page.evaluate(()=>{_delay='';void _owner.queue();});await page.locator('[data-r2-open=new]').click();await page.locator('#r2-edit').click();await page.getByLabel('За что',{exact:true}).fill('NEWER unsaved card');await release(await page.evaluate(()=>_proposal('old')));await settle();assert.equal(await page.locator('#title').textContent(),'Исправить карточку');assert.equal(await page.getByLabel('За что',{exact:true}).inputValue(),'NEWER unsaved card');
 // An explicit currency change immediately clears incompatible account and debt.
 await page.getByLabel('Валюта',{exact:true}).selectOption('UAH');assert.equal(await page.getByLabel('Наличные / Банк').inputValue(),'');assert.equal(await page.getByLabel('Погасить долг').inputValue(),'');assert.equal(await page.getByLabel('Наличные / Банк').locator('option[value=try-account]').count(),0);assert.equal(await page.getByLabel('Погасить долг').locator('option[value=uah-debt]').count(),1);
 // A source response from owner A must never reopen after logout/owner B login.
 await setup();await page.evaluate(()=>_delay='/captures');await page.locator('#owner-sources').click();await waitHeld();await page.evaluate(()=>{_owner.logout();_data.owner.owner_id='owner-b';document.querySelector('#sheet').close();document.querySelector('#sheet-content').textContent='OWNER B';});await release({items:[{id:'old-source',text:'PRIVATE OWNER A SOURCE',captured_at:'2026-10-08',state:'SERVER_SAVED',proposals:[]}]});await settle();assert.equal(await page.locator('#sheet').evaluate(s=>s.open),false);assert.equal(await page.locator('#sheet-content').textContent(),'OWNER B');
 // The same isolation applies to IndexedDB, not just network responses.
 await setup();await page.evaluate(async()=>{await _write({key:'entity:owner-a:local',scope:'entity:owner-a',body:{client_capture_id:'local',text:'PRIVATE OWNER A LOCAL',captured_at:'2026-10-08'}});const original=indexedDB.open.bind(indexedDB);window._idbHolds=[];indexedDB.open=(...args)=>{const real=original(...args),delayed={};real.onsuccess=()=>{delayed.result=real.result;_idbHolds.push(()=>delayed.onsuccess());};real.onerror=()=>delayed.onerror?.();return delayed;};});await page.locator('#owner-local').click();await page.waitForFunction(()=>_idbHolds.length>0);await page.evaluate(()=>{_owner.logout();_data.owner.owner_id='owner-b';document.querySelector('#sheet-content').textContent='OWNER B LOCAL';_idbHolds.shift()();});await settle();assert.equal(await page.locator('#sheet').evaluate(s=>s.open),false);assert.equal(await page.locator('#sheet-content').textContent(),'OWNER B LOCAL');
 // A failed prior-owner request must not surface a private error in the new session.
 await setup();await page.evaluate(()=>_delay='/captures');await page.locator('#owner-sources').click();await waitHeld();await page.evaluate(()=>{_owner.logout();_data.owner.owner_id='owner-b';_held.shift().reject(Error('PRIVATE OWNER A ERROR'));});await settle();assert.deepEqual(await page.evaluate(()=>_notices),[]);
 assert.deepEqual(errors,[]);console.log('PASS R2 browser late review/new editor, newer-card generation, currency/account/debt reset, network and IndexedDB owner isolation, stale error isolation');
}finally{await browser.close();await new Promise(r=>server.close(r));}
