import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import {chromium,webkit} from 'playwright';
const base=process.env.PREVIEW_URL||'http://127.0.0.1:4173';
const out=process.env.E2E_ARTIFACTS||'artifacts/friction-before';
await fs.mkdir(out,{recursive:true});
const result={sha:process.env.OWNER_PREVIEW_FEATURE_SHA,kind:'before',cases:[],screenshots:[],measurements:[]};
for(const [engine,type] of [['chromium',chromium],['webkit',webkit]]){
 const browser=await type.launch();const context=await browser.newContext({viewport:{width:390,height:844}});const page=await context.newPage();
 const click=async name=>page.getByRole('button',{name,exact:true}).click();
 const route=async hash=>{await page.goto(base+'/r3/connected.html#'+hash);await page.waitForFunction(h=>document.querySelector('#main')?.dataset.renderedRoute===h,hash).catch(async()=>{await page.locator('[data-form]').waitFor();});};
 const fill=async(name,v)=>{await page.waitForFunction(n=>{const x=document.querySelector(`[name="${n}"]`);return x&&!x.closest('[inert]');},name);await page.locator(`[name="${name}"]`).fill(v);};
 const snap=async name=>{const file=engine+'-390-'+name+'.png';await page.screenshot({path:path.join(out,file),fullPage:true});result.screenshots.push(file);result.measurements.push(await page.evaluate(file=>({file,height:document.documentElement.scrollHeight,viewport:{width:innerWidth,height:innerHeight},actions:[...document.querySelectorAll("button[type=submit], [data-action=confirm-proposal]")].map(x=>({label:x.textContent,disabled:x.disabled,y:x.getBoundingClientRect().top+scrollY}))}),file));};
 await page.goto(base+'/r3/');await page.locator('#secret').fill(process.env.PREVIEW_SECRET);await click('Войти');await page.waitForURL('**/connected.html');await page.getByText('Добрый день',{exact:true}).waitFor();
 const config=await (await context.request.get(base+'/preview/config')).json();assert.equal(config.feature_sha,result.sha);const root='/v1/entities/'+config.entity_id;
 const seeded=execFileSync('python',[process.env.BASELINE_FIXTURE_SCRIPT,'--entity',config.entity_id,'--owner',config.owner_id],{encoding:'utf8'});
 result.cases.push({engine,diagnostic_fixture:JSON.parse(seeded)});
 await page.locator('[data-testid=home-expense]').click();await fill('text','8500 насос ремонт');await click('Сохранить оригинал');await page.locator('a[href^="#review/"]').first().click();await page.locator('[data-action^="recover:"]').click();await page.locator('.compact-expense-review').waitFor();await snap('expense');
 const id=page.url().split('#review/')[1];const p=await(await context.request.get(base+root+'/owner/confirmations/'+id+'/preview')).json();assert.equal(p.source.text,'8500 насос ремонт');assert.equal(p.revision.payload.amount,null);assert.equal(p.revision.payload.description,null);result.cases.push({engine,case:'A',source:p.source.text,amount:p.revision.payload.amount,description:p.revision.payload.description,missing:p.revision.missing});
 await route('quick/harvest');const opts=await page.locator('[name=greenhouse] option').evaluateAll(xs=>xs.map(x=>({value:x.value,text:x.textContent})));const place=opts.find(x=>/mam/i.test(x.text));assert.ok(place);await page.locator('[name=greenhouse]').selectOption(place.value);await fill('count','135');await fill('date','2026-10-10');await fill('reporter','Владелец TEST');await click('Сохранить полевой срез');await page.locator('.form-error').filter({hasText:'исходную формулировку'}).waitFor();await snap('harvest');result.cases.push({engine,case:'B',count:'135',blocker:await page.locator('.form-error').textContent()});
 await route('quick/receipt');const inv=await(await context.request.get(base+root+'/inventory')).json();const product=inv.products.find(x=>x.base_unit==='kg');assert.ok(product);await page.locator('[name=product]').selectOption(product.id);await page.locator('[name=location]').selectOption(inv.locations[0].id);await fill('quantity','120');await fill('date','2026-10-10');await page.locator('[data-form=receipt] button[type=submit]').click();await page.locator('.form-error').filter({hasText:'исходную запись'}).waitFor();await snap('receipt');result.cases.push({engine,case:'C',quantity:'120',unit:'kg',blocker:await page.locator('.form-error').textContent()});
 await browser.close();
}
await fs.writeFile(path.join(out,'before-owner-cases.json'),JSON.stringify(result,null,2));
