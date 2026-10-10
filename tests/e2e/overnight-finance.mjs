import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {chromium,webkit} from 'playwright';
const base=process.env.PREVIEW_URL || 'http://127.0.0.1:4173';
const out=process.env.E2E_ARTIFACTS || 'artifacts/overnight-finance';
await fs.mkdir(out,{recursive:true});
const result={sha:process.env.OWNER_PREVIEW_FEATURE_SHA,status:'RUNNING',engines:[],screenshots:[],errors:[],layouts:[],fixture_setup:'API synthetic prerequisites only; payment/review/confirmation/navigation are real browser actions'};
try {for(const [engine,type] of [['chromium',chromium],['webkit',webkit]]) {
 const browser=await type.launch();const context=await browser.newContext({viewport:{width:390,height:844}});const page=await context.newPage();
 page.on('pageerror',e=>result.errors.push(String(e)));
 const ready=prefix=>page.waitForFunction(p=>location.hash.slice(1).startsWith(p)&&document.querySelector('#main')?.dataset.renderedRoute===location.hash.slice(1),prefix);
 const goto=async hash=>{await page.goto(base+'/r3/connected.html#'+hash);await ready(hash);};
 const click=async text=>{const b=page.getByRole('button',{name:text,exact:true});await b.scrollIntoViewIfNeeded();await b.click();};
 const get=async url=>{const r=await context.request.get(base+url);assert.equal(r.status(),200,await r.text());return r.json();};
 const post=async(url,body,key)=>{const r=await context.request.post(base+url,{data:body,headers:{'Idempotency-Key':`overnight-finance-${engine}-${key}`,'Origin':base}});assert.ok([200,201].includes(r.status()),await r.text());return r.json();};
 const snap=async name=>{await page.locator('#toast.show').waitFor({state:'hidden'});const file=`${engine}-${page.viewportSize().width}-${name}.png`;await page.screenshot({path:path.join(out,file),fullPage:true});result.screenshots.push(file);};
 const layout=async name=>{for(const width of [320,390,768,1280]){await page.setViewportSize({width,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`${name} ${width} overflow`);const targets=await page.locator('.money-tab').evaluateAll(xs=>xs.map(x=>({w:x.getBoundingClientRect().width,h:x.getBoundingClientRect().height})));assert.ok(targets.every(t=>t.w>=44&&t.h>=44));result.layouts.push({engine,name,width,targets});if(width===390||width===320)await snap(name);}await page.setViewportSize({width:390,height:844});};
 await page.goto(base+'/r3/');await page.locator('#secret').fill(process.env.PREVIEW_SECRET);await click('Войти');await page.waitForURL('**/connected.html');await page.getByText('Добрый день',{exact:true}).waitFor();
 const config=await get('/preview/config');assert.equal(config.feature_sha,result.sha);const root='/v1/entities/'+config.entity_id;
 const refs=await post(root+'/reference-bootstrap',{currency:'TRY'},'refs');const ledger=Object.fromEntries(refs.references.map(r=>[r.system_code,r.id]));
 const date=engine==='chromium'?'2026-10-05':'2026-10-06';
 const account=await post(root+'/money-accounts',{name:`TEST ${engine} finance acceptance`,account_type:'bank',currency:'TRY',source:'TEST synthetic stage A'},'account');
 const parties={};const documents={};
 for(const [direction,role,amount,code] of [['receivable','customer','1000.00','GENERAL_REVENUE'],['payable','supplier','500.00','GENERAL_EXPENSE']]) {
  const party=await post(root+'/counterparties',{name:`TEST ${engine} ${role} acceptance`,role,source:'TEST synthetic stage A'},'party-'+role);parties[direction]=party.id;
  const doc=await post('/v1/obligations',{entity_id:config.entity_id,counterparty_id:party.id,direction,original_amount:amount,currency:'TRY',event_date:date,offset_ledger_account_id:ledger[code],description:`TEST ${engine} ${direction==='receivable'?'Продажа 1000':'Принятый ремонт 500'}`},'document-'+role);
  documents[direction]=await post(`/v1/obligations/${doc.id}/post?expected_version=1`,null,'post-'+role);
 }
 const record={engine,date,entity_id:config.entity_id,account_id:account.id,documents,payments:[]};
 // Actual owner UI allocates and confirms each payment, one lost server response.
 for(const [direction,amount] of [['receivable','400.00'],['payable','200.00']]) {
  await goto(`settle/${direction}/${parties[direction]}/TRY`);await page.locator('[name=account]').selectOption(account.id);await page.locator('[name=date]').fill(date);await page.locator('[name=allocation_'+documents[direction].id+']').fill(amount);
  await click('Проверить оплату');await ready('payment/');const paymentId=page.url().split('#payment/')[1];
  assert.match(await page.locator('#main').innerText(),/Остаток после оплаты · прогноз/);
  if(direction==='receivable') {let lost=false;await page.route('**/v1/payments/'+paymentId+'/post?*',async route=>{if(!lost){lost=true;const response=await route.fetch();assert.equal(response.status(),200);await route.abort('failed');}else await route.continue();});await click('Подтвердить оплату в TEST');await page.locator('#action-error').filter({hasText:'Ответ сервера не получен'}).waitFor();await page.reload();await ready('payment/');await page.unroute('**/v1/payments/'+paymentId+'/post?*');}
  else await click('Подтвердить оплату в TEST');
  await page.getByText('Сервер подтвердил платёж. Повтор не создаёт вторую проводку.',{exact:true}).waitFor();record.payments.push(paymentId);
 }
 const route=`money/overview/${date}/${date}/TRY/all/${account.id}`;await goto(route);
 const data=await get(root+`/finance/analytics?date_from=${date}&date_to=${date}&currency=TRY&money_account_id=${account.id}`);
 assert.equal(data.economics.revenue,'1000.00');assert.equal(data.economics.operating_cost,'500.00');assert.equal(data.economics.operating_result,'500.00');assert.equal(data.cash_totals.net_change,'200.00');assert.equal(data.accounts[0].current_balance,'200.00');
 for(const [direction,remaining] of [['receivable','600.00'],['payable','300.00']])assert.equal(data.debts.documents.find(d=>d.obligation_id===documents[direction].id).remaining_amount,remaining);
 await layout('overview');await page.getByRole('link',{name:'Деньги',exact:true}).click();await ready('money/cash');await layout('cash');assert.match(await page.locator('#main').innerText(),/TEST .* finance acceptance/);
 await page.getByRole('link',{name:'Результат',exact:true}).click();await ready('money/result');await layout('result');assert.match(await page.locator('#main').innerText(),/Результат по учтённым затратам/);
 await page.getByRole('link',{name:'Долги',exact:true}).click();await ready('money/debts');await page.getByRole('link',{name:new RegExp(`TEST ${engine} Продажа 1000`)}).click();await ready('money-document/');assert.match(await page.locator('#main').innerText(),/600 TRY/);assert.match(await page.locator('#main').innerText(),/400 TRY/);await layout('document-history');
 await page.getByRole('link',{name:'Назад к финансам',exact:true}).click();await ready('money/debts');assert.ok(page.url().includes(date+'/'+date+'/TRY/all/'+account.id));await page.reload();await ready('money/debts');
 await page.getByRole('link',{name:'Теплицы',exact:true}).click();await ready('money/areas');await layout('areas');
 // Read error stays local; session remains valid and retry recovers same selection.
 await page.route('**/finance/analytics?*',r=>r.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:{message:'TEST temporary finance read failure'}})}));await page.reload();await page.getByRole('heading',{name:'Не удалось загрузить'}).waitFor();assert.ok(!page.url().includes('/preview/login'));await page.unroute('**/finance/analytics?*');await click('Повторить');await ready('money/areas');
 const after=await get(root+`/finance/analytics?date_from=${date}&date_to=${date}&currency=TRY&money_account_id=${account.id}`);assert.equal(after.cash_totals.net_change,'200.00');assert.equal(after.economics.operating_result,'500.00');record.analytics=after;result.engines.push(record);await browser.close();
}
 assert.deepEqual(result.errors,[]);result.status='PASS';
} catch(error) {result.status='FAIL';result.failure=String(error.stack || error);throw error;}
finally {await fs.writeFile(path.join(out,'finance-results.json'),JSON.stringify(result,null,2));}
