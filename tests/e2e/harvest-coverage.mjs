// AUD-009 acceptance on a fresh authenticated PostgreSQL preview. Sources are
// created through real forms/APIs; no route mocks or frontend state injection.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {chromium} from 'playwright';
import {navigateOwner, revealOwnerControl} from './navigation.mjs';

const base=process.env.PREVIEW_URL||'http://127.0.0.1:4173';
assert.ok(['127.0.0.1','localhost','[::1]'].includes(new URL(base).hostname),'Disposable local preview only');
assert.ok(process.env.PREVIEW_SECRET,'Guarded preview authentication is required');
const artifacts=process.env.E2E_ARTIFACTS||'artifacts';
await fs.mkdir(artifacts,{recursive:true});
const browser=await chromium.launch({headless:true,args:['--no-sandbox'],...(process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{})});
const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
const page=await context.newPage(),errors=[],screenshots=[],states=[];
page.on('pageerror',error=>errors.push(error.message));
const ready=()=>page.waitForFunction(()=>document.querySelector('#app')?.getAttribute('aria-busy')==='false');
const sheet=()=>page.locator('#sheet-content');
const close=async()=>{await sheet().getByRole('button',{name:'Закрыть',exact:true}).click();await page.locator('#sheet').waitFor({state:'hidden'});};
async function api(path,body){
 const response=body===undefined?await context.request.get(base+path):await context.request.post(base+path,{data:body,headers:{'Idempotency-Key':crypto.randomUUID()}});
 assert.ok(response.ok(),`${path}: ${response.status()} ${await response.text()}`);
 return response.json();
}
async function shot(name,width,locator){
 await page.evaluate(async()=>{await document.fonts.ready;await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));});
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'No horizontal page overflow');
 const file=`${artifacts}/harvest-coverage-${name}-${width}.png`;
 await locator.screenshot({path:file});screenshots.push(file);
}
let path,eid,initialFinance,settlement;
async function filter(from,to){
 await revealOwnerControl(page.locator('#harvest-from'));
 await page.locator('#harvest-from').fill(from);await page.locator('#harvest-to').fill(to);
 const [response]=await Promise.all([
  page.waitForResponse(response=>response.request().method()==='GET'&&response.url().includes(path+'?')&&new URL(response.url()).searchParams.get('date_from')===from&&new URL(response.url()).searchParams.get('date_to')===to),
  page.getByRole('button',{name:'Показать период',exact:true}).click(),
 ]);
 assert.equal(response.status(),200);const result=await response.json();
 // Filling changes input properties. The value attributes change only when
 // the real response has been rendered, so an earlier snapshot cannot pass.
 await page.waitForFunction(({from,to})=>document.querySelector('#harvest-from')?.getAttribute('value')===from&&document.querySelector('#harvest-to')?.getAttribute('value')===to,{from,to});
 await ready();return result;
}
async function fieldDisclosure(){
 const summary=page.getByText('Количество веток по полевым отчётам',{exact:true});
 const disclosure=summary.locator('..');
 if(!await disclosure.evaluate(node=>node.open))await summary.click();
 await disclosure.locator(':scope > p').first().waitFor({state:'visible'});
 return disclosure;
}
async function createField(count,day){
 await page.getByRole('button',{name:'+ Срез',exact:true}).click();
 await sheet().getByLabel('Дата среза',{exact:true}).fill(day);
 await sheet().getByLabel('Теплица · исходное название',{exact:true}).fill('Poncho');
 await sheet().getByLabel('Срезанных деревьев',{exact:true}).fill(count===null?'':String(count));
 await sheet().getByLabel('Кто сообщил',{exact:true}).fill('TEST coverage worker');
 await sheet().getByLabel('Исходный текст / сообщение',{exact:true}).fill(`TEST original field report ${day}: count ${count===null?'UNKNOWN':count}`);
 await sheet().getByRole('button',{name:'Сохранить отчёт',exact:true}).click();
 await sheet().getByRole('heading',{name:'Полевой отчёт',exact:true}).waitFor();await close();await ready();
}
try{
 await page.goto(base);await page.getByLabel('Ключ доступа').fill(process.env.PREVIEW_SECRET);
 await page.getByRole('button',{name:'Открыть Finance',exact:true}).click();
 await page.getByRole('heading',{name:'Моя теплица',exact:true}).waitFor();
 eid=(await api('/preview/config')).entity_id;path=`/v1/entities/${eid}/harvest`;
 initialFinance=await api(`/v1/entities/${eid}/finance-overview`);
 assert.deepEqual((await api(path)).reports,[],'Coverage journey requires its own fresh preview lifecycle');
 await navigateOwner(page,'harvest');
 const initiallyEmpty=await filter('2026-10-05','2026-10-06');
 assert.equal(initiallyEmpty.total_cut_count,null);assert.equal(initiallyEmpty.cut_count_coverage.report_count,0);
 assert.match(await (await fieldDisclosure()).innerText(),/Нет полевых отчётов в выбранном периоде/);
 await createField(null,'2026-10-05');await createField(7,'2026-10-06');
 const originalReports=(await api(path)).reports;
 assert.deepEqual(originalReports.map(row=>row.cut_count),[7,null]);
 const cases=[
  {name:'no-reports',from:'2026-10-07',to:'2026-10-07',sum:null,total:0,known:0,text:/Нет полевых отчётов в выбранном периоде/},
  {name:'unknown',from:'2026-10-05',to:'2026-10-05',sum:null,total:1,known:0,text:/Количество неизвестно: 1 из 1 отчётов без количества/},
  {name:'partial-seven',from:'2026-10-05',to:'2026-10-06',sum:7,total:2,known:1,text:/Известно: 7 веток.*Неполные данные: количество неизвестно в 1 из 2 отчётов/},
  {name:'known-seven',from:'2026-10-06',to:'2026-10-06',sum:7,total:1,known:1,text:/Известно: 7 веток.*Отчёты с количеством: 1 из 1/},
 ];
 for(const width of [390,320]){
  await page.setViewportSize({width,height:844});
  await navigateOwner(page,'more');await navigateOwner(page,'harvest');
  for(const scenario of cases){
   const view=await filter(scenario.from,scenario.to);
   assert.equal(view.total_cut_count,scenario.sum);
   assert.deepEqual(view.cut_count_coverage,{report_count:scenario.total,known_report_count:scenario.known,unknown_report_count:scenario.total-scenario.known,complete:scenario.total>0&&scenario.total===scenario.known});
   assert.deepEqual(view.reports,originalReports,'Date filters must preserve original source rows');
   const disclosure=await fieldDisclosure(),text=await disclosure.locator(':scope > p').first().innerText();
   assert.match(text,scenario.text);assert.doesNotMatch(text,/Всего в периоде аналитики|0 веток|null/);
   states.push({width,name:scenario.name,date_from:view.date_from,date_to:view.date_to,total_cut_count:view.total_cut_count,cut_count_coverage:view.cut_count_coverage,text});
   await shot(scenario.name,width,disclosure);
  }
 }
 const depot=await api(`/v1/entities/${eid}/counterparties`,{name:'TEST Coverage Depot',role:'depot',source:'test:harvest-coverage-browser'});
 const draft=await api(path+'/settlements',{depot_id:depot.id,event_date:'2026-10-06',currency:'TRY',due_date:'2026-10-15',
  lines:[{original_quality_name:'Grand',net_weight_kg:'70',unit_price:'30',count:7,source_line:'1',allocations:[]},{original_quality_name:'İkinci',net_weight_kg:'30',unit_price:'30',count:null,source_line:'2',allocations:[]}],
  evidence:{source:'test:harvest-coverage-browser',raw_text:'TEST original receipt: 70 kg / 7 bunches; 30 kg / UNKNOWN count'}});
 settlement=await api(path+`/settlements/${draft.id}/post`,{expected_version:1,confirmed:true,reviewed_issues:draft.issues,review_reason:'TEST explicitly reviewed partial count coverage and unallocated source'});
 assert.equal(settlement.count_coverage_complete,false);assert.equal(Number(settlement.average_weight_kg),10);
 for(const width of [390,320]){
  await page.setViewportSize({width,height:844});await navigateOwner(page,'more');await navigateOwner(page,'harvest');
  const view=await filter('2026-10-06','2026-10-06'),bucket=view.analytics.find(row=>row.currency==='TRY'&&row.greenhouse_id===null);
  assert.equal(bucket.count_coverage_complete,false);assert.equal(bucket.confirmed_kg,'70.000');assert.equal(bucket.confirmed_count,7);assert.equal(Number(bucket.average_weight_kg),10);
  const card=page.locator('section.module-metric').filter({has:page.getByRole('heading',{name:'Нераспределено',exact:true})});
  assert.match(await card.innerText(),/Средний вес · известная часть/);assert.match(await card.innerText(),/70.000 кг \/ 7 деревьев/);
  assert.match(await card.innerText(),/Полный средний вес неизвестен/);await shot('partial-depot-average',width,card);
  await page.locator(`[data-hsettlement="${settlement.id}"]`).click();await sheet().getByRole('heading',{name:'Расчёт депо',exact:true}).waitFor();
  assert.match(await sheet().innerText(),/Средний вес · известная часть: 10,00/);
  assert.match(await sheet().innerText(),/Подтверждённая база: 70.000 кг \/ 7 веток/);
  assert.match(await sheet().innerText(),/Полный средний вес неизвестен/);
  const reconciliation=sheet().locator('.card').filter({has:page.getByRole('heading',{name:'Сверка поле / депо',exact:true})});
  await reconciliation.scrollIntoViewIfNeeded();await shot('partial-depot-detail',width,reconciliation);await close();
 }
 assert.deepEqual((await api(path)).reports,originalReports);
 await api(path+`/settlements/${settlement.id}/reverse`,{expected_version:2,event_date:'2026-10-06',reason:'TEST browser fixture cleanup; retain all source evidence'});
 const finalFinance=await api(`/v1/entities/${eid}/finance-overview`);
 assert.deepEqual(finalFinance.balances,initialFinance.balances);
 assert.equal((await api('/v1/finance/reconciliation?entity_id='+eid)).ok,true);
 assert.deepEqual(errors,[]);
 await fs.writeFile(`${artifacts}/harvest-coverage.json`,JSON.stringify({ok:true,entity_id:eid,fixtureReports:originalReports,settlement_id:settlement.id,states,screenshots,financeRestored:true,errors},null,2));
 console.log('PASS Harvest coverage: real field forms, all four states at 390/320, clicked inclusive date filters/navigation, partial depot card/detail, preserved source rows and restored Finance.');
}catch(error){
 await page.screenshot({path:`${artifacts}/harvest-coverage-failure.png`,fullPage:true}).catch(()=>{});
 await fs.writeFile(`${artifacts}/harvest-coverage-failure.json`,JSON.stringify({error:String(error),errors,states,screenshots},null,2));throw error;
}finally{await browser.close();}
