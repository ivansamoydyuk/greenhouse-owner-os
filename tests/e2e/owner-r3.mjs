// Exact-SHA browser evidence for the isolated synthetic prototype, never the backend.
import assert from 'node:assert/strict';
import {chromium,webkit} from 'playwright';
import fs from 'node:fs/promises';
import path from 'node:path';

const base=process.env.PREVIEW_URL||'http://127.0.0.1:4173';
const artifacts=process.env.E2E_ARTIFACTS||'artifacts/owner-r3-evidence';
const expectedSha=process.env.OWNER_PREVIEW_FEATURE_SHA;
assert.match(expectedSha||'',/^[a-f0-9]{40}$/,'Set the exact build SHA before browser verification');
assert.ok(process.env.PREVIEW_SECRET,'An ephemeral preview key is required');
await fs.mkdir(artifacts,{recursive:true});
const report={feature_sha:expectedSha,started_at:new Date().toISOString(),screenshots:[],journeys:[],checks:[],limitations:['Chromium/WebKit automation is not physical iPhone verification.','Native microphone recording and a real iPhone software keyboard are not asserted by these tests.','Speech unavailable/error fallback is exercised without substituting a transcript for live microphone input.','Click totals are trusted browser DOM interactions, not physical iPhone tap estimates. Native picker/keyboard UI is not measured; the photo journey explicitly chooses a prepared synthetic source.'],errors:[],failures:[],tool_diagnostics:[]};
const SCENARIO='Нужно переделать полив на Озвади на следующей неделе';
const screens=[['home','Главная'],['review','Проверка'],['capture/voice','Голос'],['capture/photo','Фото'],['capture/text','Текст'],['tasks','Задачи'],['task/irrigation','Карточка задачи'],['finance/receivable','Финансы'],['harvest','Урожай'],['inventory','Склад'],['workers','Работники'],['more','Ещё']];
let currentPage;

async function newPage(browser) {
  const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true,locale:'ru-RU',timezoneId:'Europe/Istanbul',reducedMotion:'reduce'});
  const response=await context.request.post(base+'/preview/login',{headers:{Origin:base},data:{secret:process.env.PREVIEW_SECRET}});
  assert.equal(response.status(),200,'Preview login failed');
  const configResponse=await context.request.get(base+'/preview/config');
  assert.equal(configResponse.status(),200);
  const config=await configResponse.json();
  assert.equal(config.feature_sha,expectedSha);
  assert.equal(config.backend_connected,false);
  await context.addInitScript(()=>{
    window.__r3Csp=[];document.addEventListener('securitypolicyviolation',event=>window.__r3Csp.push({sample:event.sample,directive:event.effectiveDirective,blocked_uri:event.blockedURI}));
    const visible=e=>!!(e.getClientRects().length&&!e.closest('[hidden]'));
    let activeForms=new Set();
    const metrics={active:false,clicks:[],forms:[],inputs:[],keys:[]};
    const refreshForms=()=>{
      const forms=new Set([...document.querySelectorAll('form,[data-r3-form],dialog[open]')].filter(e=>visible(e)&&!e.querySelector('form,[data-r3-form]')).map(e=>`${location.hash}:${e.dataset.r3Form||e.id||e.getAttribute('aria-label')||e.tagName}`));
      for(const field of document.querySelectorAll('input:not([type=file]):not([type=hidden]),textarea,select'))if(visible(field)&&!field.closest('form,[data-r3-form],dialog'))forms.add(`${location.hash}:focused-fields`);
      if(metrics.active)for(const key of forms)if(!activeForms.has(key))metrics.forms.push({key,at:performance.now()});
      activeForms=forms;
    };
    document.addEventListener('click',event=>{
      if(!metrics.active)return;
      const target=event.target.closest('button,a,input,textarea,select,label,summary');
      metrics.clicks.push({target:target?.dataset.testid||target?.id||target?.getAttribute('aria-label')||target?.textContent?.trim().slice(0,100)||event.target.tagName,route:location.hash,trusted:event.isTrusted});
    },true);
    document.addEventListener('change',event=>{
      if(!metrics.active)return;
      const field=event.target;
      if(!field.matches('input,textarea,select'))return;
      metrics.inputs.push({field:field.dataset.testid||field.name||field.id||field.tagName,type:field.type,value:field.type==='file'?[...field.files].map(f=>f.name).join(','):field.value,route:location.hash});
    },true);
    document.addEventListener('DOMContentLoaded',()=>{new MutationObserver(refreshForms).observe(document.documentElement,{subtree:true,childList:true,attributes:true,attributeFilter:['open','hidden','class']});refreshForms();});
    window.__r3Metrics={start(){metrics.active=true;metrics.clicks=[];metrics.forms=[];metrics.inputs=[];metrics.keys=[];refreshForms();},key(key){if(metrics.active)metrics.keys.push({key,route:location.hash});},stop(){metrics.active=false;return structuredClone(metrics);}};
  });
  const page=await context.newPage();currentPage=page;
  page.on('pageerror',error=>report.errors.push({type:'pageerror',message:error.message}));
  page.on('console',message=>{if(message.type()==='error')report.errors.push({type:'console',message:message.text(),location:message.location(),during_screenshot:!!page.__r3TakingScreenshot});});
  context.on('request',request=>{
    const url=new URL(request.url());
    if(!['http:','https:'].includes(url.protocol))return;
    if(url.origin!==new URL(base).origin||url.pathname.startsWith('/v1/')||!['GET','HEAD'].includes(request.method()))report.errors.push({type:'isolation',method:request.method(),url:url.origin+url.pathname});
  });
  await page.goto(base+'/#home');await ready(page);
  return {context,page};
}
async function ready(page){await page.locator('#main h1').waitFor();await page.evaluate(async()=>{await document.fonts.ready;await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));});}
async function route(page,name){await page.goto(base+'/#'+(name==='capture/voice'?'home':name));await ready(page);if(name==='capture/voice'){await click(page,'capture-voice');await page.getByTestId('voice-fallback').waitFor({timeout:15000});}await page.evaluate(()=>window.scrollTo(0,0));}
async function click(page,id){const target=page.getByTestId(id),href=await target.getAttribute('href');await target.click();if(href?.startsWith('#'))await page.waitForFunction(hash=>location.hash===hash,href);await ready(page);}
async function fill(page,id,value){const field=page.getByTestId(id);await field.click();if(await field.evaluate(el=>el.tagName==='SELECT')){const target=await field.evaluate((el,label)=>[...el.options].findIndex(option=>option.textContent===label),value);assert.ok(target>=0,'Requested select choice exists');for(const key of ['Home',...Array(target).fill('ArrowDown'),'Enter']){await page.keyboard.press(key);await page.evaluate(key=>window.__r3Metrics.key(key),key);}assert.equal(await field.inputValue(),value);}else await field.fill(value);await field.blur();}
async function rawShot(page,options){
  const firstError=report.errors.length;page.__r3TakingScreenshot=true;
  try{return await page.screenshot({...options,caret:'initial'});}finally{
    await page.evaluate(()=>new Promise(r=>requestAnimationFrame(r))).catch(()=>{});
    const violations=await page.evaluate(()=>window.__r3Csp?.splice(0)||[]).catch(()=>[]);
    for(const violation of violations){
      // Playwright's verified screenshotter source injects exactly "body {}" to
      // synchronize WebKit animations. Keep CSP enabled and prove that sample
      // before classifying this tooling-only diagnostic outside app failures.
      if(violation.sample?.trim()==='body {}'&&/^style-src/.test(violation.directive)&&violation.blocked_uri==='inline'){
        const index=report.errors.findIndex((entry,i)=>i>=firstError&&entry.during_screenshot&&entry.type==='console'&&entry.message.startsWith('Refused to apply a stylesheet because its hash, its nonce, or'));
        if(index>=0){const [entry]=report.errors.splice(index,1);report.tool_diagnostics.push({...entry,...violation,source:'Playwright WebKit screenshot animation synchronization; CSP correctly blocked the empty stylesheet'});}
      }else report.errors.push({type:'csp',...violation});
    }
    page.__r3TakingScreenshot=false;
  }
}
async function screenshot(page,engine,routeName,width,label){
  await page.locator('#toast.show').waitFor({state:'hidden',timeout:6000});
  const originalScroll=await page.evaluate(()=>({x:scrollX,y:scrollY}));
  const file=`${engine}-${String(width)}-${routeName.replaceAll('/','-')}.png`;
  const pixels=await rawShot(page,{path:path.join(artifacts,file),fullPage:true}),pixelWidth=pixels.readUInt32BE(16);
  if(pixelWidth!==width)recordFailure(`${engine} screenshot ${width} #${routeName}`,new Error(`PNG width ${pixelWidth}px differs from requested viewport ${width}px`));
  report.screenshots.push({file,feature_sha:expectedSha,engine,width,pixel_width:pixelWidth,screen:label,route:'#'+routeName,full_page:true});
  const overflow=await page.evaluate(async expected=>{await new Promise(r=>requestAnimationFrame(r));return Math.max(document.documentElement.scrollWidth,document.body.scrollWidth)>expected+1;},width);if(overflow)recordFailure(`${engine} after-screenshot ${width} #${routeName}`,new Error('Body/document overflow after screenshot reflow'));
  if(width===390){await page.evaluate(position=>window.scrollTo(position.x,position.y),originalScroll);const viewportFile=file.replace('.png','-viewport.png');await rawShot(page,{path:path.join(artifacts,viewportFile),fullPage:false});report.screenshots.push({file:viewportFile,feature_sha:expectedSha,engine,width,height:page.viewportSize().height,screen:label,route:'#'+routeName,full_page:false});}
}
async function layout(page,routeName,width){
  const problems=await page.evaluate(expectedWidth=>{
    const out=[];
    if(Math.max(document.documentElement.scrollWidth,document.body.scrollWidth)>expectedWidth+1)out.push('Horizontal page overflow beyond requested CSS viewport');
    const meta=document.querySelector('meta[name=viewport]')?.content||'';
    if(/user-scalable\s*=\s*no|maximum-scale\s*=\s*1(?:\D|$)/i.test(meta))out.push('Text/page zoom is restricted');
    for(const el of document.querySelectorAll('a,button,input:not([type=file]):not([type=hidden]),select,textarea')){
      const b=el.getBoundingClientRect();
      if(!b.width||!b.height||getComputedStyle(el).visibility==='hidden')continue;
      if(b.width<43.5||b.height<43.5)out.push(`Touch target below 44px: ${el.dataset.testid||el.textContent.trim().slice(0,40)} ${Math.round(b.width)}×${Math.round(b.height)}`);
      if((b.left<-.5||b.right>expectedWidth+.5)&&!el.closest('.filters'))out.push(`Control outside viewport: ${el.dataset.testid||el.tagName}`);
    }
    for(const row of document.querySelectorAll('.stock-row')){const quantity=row.querySelector('.stock-quantity')?.getBoundingClientRect();if(!quantity)continue;for(const text of row.querySelectorAll(':scope > div strong,:scope > div p')){const range=document.createRange();range.selectNodeContents(text);if([...range.getClientRects()].some(b=>b.right>quantity.left-8&&b.top<quantity.bottom&&b.bottom>quantity.top))out.push(`Stock text touches quantity: ${text.textContent}`);}}
    if(document.querySelectorAll('dialog[open],[role=dialog][aria-modal=true]').length>1)out.push('Multiple modal layers');
    return out;
  },width);
  assert.deepEqual(problems,[],`${width}px #${routeName}: ${problems.join('; ')}`);
  const covered=await page.evaluate(async()=>{const dock=document.querySelector('.dock'),last=document.querySelector('#main')?.lastElementChild;if(!dock||!last)return false;window.scrollTo(0,document.documentElement.scrollHeight);await new Promise(r=>requestAnimationFrame(r));const hidden=last.getBoundingClientRect().bottom>dock.getBoundingClientRect().top+1;window.scrollTo(0,0);return hidden;});
  assert.equal(covered,false,`${width}px #${routeName}: bottom content is covered by capture/navigation dock`);
  if(!routeName.startsWith('capture/')&&!routeName.startsWith('task/')){
    for(const mode of ['photo','voice','text'])assert.equal(await page.getByTestId('capture-'+mode).isVisible(),true,`${mode} capture is available`);
  }
}
async function captureFailure(page){if(!page.isClosed()){await rawShot(page,{path:path.join(artifacts,'failure.png'),fullPage:true}).catch(()=>{});await fs.writeFile(path.join(artifacts,'failure.html'),await page.content()).catch(()=>{});}}
function recordFailure(stage,error){report.failures.push({stage,message:error.message,stack:error.stack});console.error(`${stage}: ${error.message}`);}
async function journey(browser,name,run){
  const {context,page}=await newPage(browser);
  await page.evaluate(()=>window.__r3Metrics.start());
  let status='PASS',failure;
  try {await run(page);}catch(error){status='FAIL';failure=error.message;recordFailure(name,error);await captureFailure(page);}
  try {
    const raw=await page.evaluate(()=>window.__r3Metrics.stop());
    assert.ok(raw.clicks.every(c=>c.trusted),'Journey clicks must be trusted browser events');
    const facts=raw.inputs.filter(x=>!['checkbox','radio','file'].includes(x.type));
    const repeated=facts.filter((fact,index)=>facts.slice(0,index).some(previous=>previous.value&&previous.value===fact.value));
    report.journeys.push({name,status,...(failure?{failure,partial:true}:{}),start:'#home',end:await page.evaluate(()=>location.hash),clicks:raw.clicks.length,opened_forms:raw.forms.length,manual_field_edits:facts.length,keyboard_actions:raw.keys.length,key_events:raw.keys,reentered_facts:repeated.length,click_events:raw.clicks,form_events:raw.forms,input_events:raw.inputs});
  }finally{await context.close();}
}

async function main(browser,engine){
  const {context,page}=await newPage(browser);
  const previousFailures=report.failures.length;
  try{
    for(const [routeName,label] of screens){await page.evaluate(()=>window.r3Demo.reset());await route(page,routeName);if(routeName==='capture/photo'){await click(page,'demo-photo');await page.waitForFunction(()=>[...document.querySelectorAll('#main img')].some(img=>img.complete&&img.naturalWidth>0));}try{await layout(page,routeName,390);}catch(error){recordFailure(`${engine} layout 390 #${routeName}`,error);}await screenshot(page,engine,routeName,390,label);}
    for(const width of [320,768,1280]){
      await page.setViewportSize({width,height:844});
      for(const [routeName,label] of screens){await page.evaluate(()=>window.r3Demo.reset());await route(page,routeName);if(routeName==='capture/photo'){await click(page,'demo-photo');await page.waitForFunction(()=>[...document.querySelectorAll('#main img')].some(img=>img.complete&&img.naturalWidth>0));}try{await layout(page,routeName,width);}catch(error){recordFailure(`${engine} layout ${width} #${routeName}`,error);}await screenshot(page,engine,routeName,width,label);}
    }
    if(report.failures.length===previousFailures)report.checks.push(`${engine}: all 12 screens at 320/390/768/1280, no horizontal overflow, 44px controls, single modal layer, zoom allowed`);
  }catch(error){await captureFailure(page);throw error;}finally{await context.close();}
  if(engine!=='chromium')return;
  await journey(browser,'1. Home → review → missing amount → one clarification → demo confirmation',async page=>{
    await click(page,'nav-review');await click(page,'review-clarify');await click(page,'edit-amount');await fill(page,'fact-input','2500');await click(page,'sheet-save');await click(page,'confirm-proposal');
    await page.waitForFunction(()=>window.r3Demo.state().proposals.find(p=>p.id==='clarify').status==='confirmed');
    assert.equal(await page.locator('dialog[open]').count(),0);
  });
  await journey(browser,'2. Home → voice → honest fallback → prepared text scenario',async page=>{
    await click(page,'capture-voice');await page.getByTestId('voice-fallback').waitFor({timeout:15000});await click(page,'voice-fallback');await click(page,'demo-irrigation');await click(page,'capture-save');
    await page.getByTestId('task-save').waitFor();assert.match(await page.locator('#main').innerText(),/Özvadi/);
  });
  await journey(browser,'3. Home → photo → choose explicit prepared synthetic source → preparation → review (native picker taps unmeasured)',async page=>{
    await click(page,'capture-photo');await click(page,'demo-photo');
    await page.waitForFunction(()=>[...document.querySelectorAll('#main img')].some(img=>img.complete&&img.naturalWidth>0));
    await click(page,'capture-save');await page.getByTestId('confirm-proposal').waitFor();assert.match(await page.locator('#main').innerText(),/1[\s\u00a0]200/);
  });
  await journey(browser,'4. Home → text → task → edit one fact',async page=>{
    await click(page,'capture-text');await fill(page,'capture-input',SCENARIO);await click(page,'capture-save');await page.getByTestId('task-save').waitFor();
    await click(page,'edit-place');await fill(page,'fact-input','İmamlı');await click(page,'sheet-save');
    assert.match(await page.locator('#main').innerText(),/Следующая неделя/);assert.match(await page.locator('#main').innerText(),/İmamlı/);
    await click(page,'task-save');await page.getByTestId('task-capture-1').waitFor();
    const task=await page.evaluate(()=>window.r3Demo.state().tasks.find(t=>t.id==='capture-1'));assert.equal(task.place,'İmamlı');assert.equal(task.when,'Следующая неделя');assert.equal(task.date,null);assert.equal(task.worker,null);
  });
  await journey(browser,'5. Home → receivables → counterparty',async page=>{
    await click(page,'home-receivable');await click(page,'finance-depot');assert.match(await page.locator('#main').innerText(),/TEST Банановое депо/);
  });
  await journey(browser,'6. Home → harvest → greenhouse',async page=>{
    await click(page,'home-harvest');await click(page,'harvest-ozvadi');assert.match(await page.locator('#main').innerText(),/Özvadi/);
  });
  await journey(browser,'7. Home → inventory → item → movements',async page=>{
    await click(page,'nav-more');await click(page,'more-inventory');await click(page,'stock-kno3');await click(page,'stock-movements');await page.getByRole('heading',{name:'Движения',exact:true}).waitFor();assert.match(await page.locator('#main').innerText(),/Физический приход/);
  });
  await journey(browser,'8. Home → tasks → TEST worker → reviewed assignment',async page=>{
    await click(page,'nav-tasks');await click(page,'tasks-workers');await click(page,'worker-muhammed');await click(page,'assign-open');
    await click(page,'choose-worker');await click(page,'worker-option-muhammed');
    await page.getByTestId('assignment-confirm').waitFor();
    assert.match(await page.locator('#main').innerText(),/Не включать насос/);assert.match(await page.locator('.translation').innerText(),/پمپ را روشن نکنید/);
    assert.equal(await page.evaluate(()=>window.r3Demo.state().workerTask.translationReviewed),false,'Review is never automatically marked');
    assert.match(await page.getByTestId('assignment-confirm').innerText(),/Проверил текст/);await screenshot(page,'chromium','state-assignment-before-confirmation',390,'Reviewed assignment before confirmation');await click(page,'assignment-confirm');
    assert.match(await page.locator('#main').innerText(),/назначено|Назначено/);
  });
  try{await interruptions(browser);}catch(error){recordFailure('Interruption and regression checks',error);}
}

async function interruptions(browser){
  const {context,page}=await newPage(browser);
  try{
    await click(page,'capture-text');await fill(page,'capture-input','TEST незнакомое сообщение, не распознавайте автоматически');await page.reload();await ready(page);
    assert.equal(await page.getByTestId('capture-input').inputValue(),'TEST незнакомое сообщение, не распознавайте автоматически','Reload preserves draft');
    await page.goBack();await ready(page);await page.goForward();await ready(page);
    assert.equal(await page.getByTestId('capture-input').inputValue(),'TEST незнакомое сообщение, не распознавайте автоматически','Browser Back/Forward preserves draft');
    await click(page,'capture-save');await page.getByText('Нужна ручная проверка',{exact:true}).waitFor();
    assert.equal(await page.getByTestId('confirm-proposal').count(),0,'Unknown input is never a fabricated understood success');await screenshot(page,'chromium','state-unknown-input',390,'Unknown input without fabricated understanding');
    await route(page,'review/clarify');await click(page,'edit-amount');await fill(page,'fact-input','3500');await click(page,'sheet-cancel');
    assert.equal(await page.locator('dialog[open]').count(),0);
    assert.equal(await page.getByTestId('edit-amount').evaluate(el=>el===document.activeElement),true,'Closing sheet restores focus');
    await click(page,'edit-amount');assert.equal(await page.getByTestId('fact-input').inputValue(),'3500','Cancelled fact draft remains available');
    await page.keyboard.press('Escape');assert.equal(await page.locator('dialog[open]').count(),0);
    await route(page,'capture/photo');
    await page.getByTestId('photo-file').setInputFiles({name:'TEST-unsupported.txt',mimeType:'text/plain',buffer:Buffer.from('TEST')});
    await page.getByText(/Этот файл не удалось открыть/).first().waitFor();
    await page.getByTestId('photo-file').setInputFiles({name:'TEST-selected.png',mimeType:'image/png',buffer:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a7WQAAAAASUVORK5CYII=','base64')});
    await page.waitForFunction(()=>[...document.querySelectorAll('#main img')].some(img=>img.complete&&img.naturalWidth>0));
    await route(page,'capture/text');await page.setViewportSize({width:390,height:460});await fill(page,'capture-input',SCENARIO);
    await page.getByTestId('capture-save').scrollIntoViewIfNeeded();assert.equal(await page.getByTestId('capture-save').isVisible(),true);
    await page.getByTestId('capture-input').focus();await page.keyboard.press('Tab');
    assert.ok(await page.evaluate(()=>document.activeElement!==document.body),'Keyboard tab keeps reachable controls');await page.getByTestId('capture-save').evaluate(el=>el.scrollIntoView({block:'center'}));assert.equal(await page.getByTestId('capture-save').evaluate(el=>{const b=el.getBoundingClientRect();return b.top>=0&&b.bottom<=visualViewport.height&&b.left>=0&&b.right<=visualViewport.width;}),true,'Primary action fits fully inside reduced visual viewport after scrolling');await screenshot(page,'chromium','state-keyboard-height-proxy',390,'Reduced-height keyboard proxy, not native iPhone keyboard');
    await page.setViewportSize({width:390,height:844});
    await route(page,'settings');await click(page,'toggle-error');await route(page,'capture/text');await fill(page,'capture-input',SCENARIO);await click(page,'capture-save');await page.getByTestId('capture-retry').waitFor();
    assert.equal(await page.evaluate(()=>window.r3Demo.state().draft.text),SCENARIO);await screenshot(page,'chromium','state-save-error',390,'Preparation error preserving the source');
    const beforeSources=await page.evaluate(()=>window.r3Demo.state().sources.length);
    await click(page,'capture-retry');await page.getByTestId('task-save').waitFor();assert.equal(await page.evaluate(()=>window.r3Demo.state().sources.length),beforeSources+1,'Retry creates one source');
    await route(page,'settings');await click(page,'toggle-offline');await route(page,'capture/text');await fill(page,'capture-input',SCENARIO);await click(page,'capture-save');await page.getByTestId('capture-retry').waitFor();assert.match(await page.locator('#main').innerText(),/Нет сети/);
    await route(page,'settings');await click(page,'toggle-offline');await route(page,'capture/text');assert.equal(await page.getByTestId('capture-input').inputValue(),SCENARIO);await click(page,'capture-save');await page.getByTestId('task-save').waitFor();
    await route(page,'settings');await click(page,'toggle-stale');await route(page,'review/clarify');await click(page,'edit-amount');await fill(page,'fact-input','3500');await click(page,'sheet-save');await click(page,'confirm-proposal');await page.getByTestId('refresh-stale').waitFor();await screenshot(page,'chromium','state-stale-card',390,'Stale card blocks confirmation');
    assert.equal(await page.evaluate(()=>window.r3Demo.state().proposals.find(p=>p.id==='clarify').status),'pending','Stale card has no confirmation effect');
    await click(page,'refresh-stale');assert.equal(await page.locator('dialog[open]').count(),0);await click(page,'confirm-proposal');
    assert.equal(await page.evaluate(()=>window.r3Demo.state().proposals.find(p=>p.id==='clarify').status),'confirmed');
    await route(page,'review');await click(page,'review-harvest');await click(page,'review-depot-review');await page.goBack();await ready(page);assert.equal(await page.getByTestId('review-harvest').getAttribute('aria-pressed'),'true','Back retains queue filter');assert.equal(await page.getByTestId('review-depot-review').evaluate(el=>el===document.activeElement),true,'Browser Back restores focus to the exact review card');
    await page.evaluate(()=>window.r3Demo.reset());await route(page,'settings');await click(page,'toggle-empty');await page.getByText('Здесь пока пусто',{exact:true}).waitFor();assert.equal(await page.getByTestId('review-confirmed').evaluate(el=>{const b=el.getBoundingClientRect();return b.left>=0&&b.right<=visualViewport.width;}),true,'Selected empty-state filter stays inside viewport');await screenshot(page,'chromium','state-empty-review',390,'Empty review');
    await route(page,'settings');await click(page,'toggle-large');await layout(page,'finance/receivable',390);await screenshot(page,'chromium','state-large-finance',390,'Large amount');await page.setViewportSize({width:320,height:844});await layout(page,'finance/receivable',320);await screenshot(page,'chromium','state-large-finance',320,'Large amount');
    report.checks.push('Real UI state controls: empty queue, large amount 320/390, preparation error/retry without duplication, offline draft recovery, stale confirmation blocked, Back preserves review filter');
    await page.setViewportSize({width:390,height:844});await page.evaluate(()=>window.r3Demo.reset());
    await route(page,'review/unknown-review');await page.getByText('Исходник и история',{exact:true}).click();await screenshot(page,'chromium','state-incomplete-source',390,'Incomplete synthetic source');await route(page,'finance/payable');await screenshot(page,'chromium','state-long-label',390,'Long counterparty label');
    await route(page,'review/clarify');await click(page,'edit-amount');await fill(page,'fact-input','1,10');await click(page,'sheet-save');assert.match(await page.locator('.detail-value').innerText(),/^1,10 TRY$/);assert.equal(await page.evaluate(()=>window.r3Demo.state().proposals.find(p=>p.id==='clarify').amount),1.1);
    await route(page,'workers');const beforeWorker=await page.getByTestId('worker-muhammed').innerText();
    await route(page,'assignment/worker-task');await click(page,'choose-worker');await click(page,'worker-option-muhammed');await route(page,'workers');assert.equal(await page.getByTestId('worker-muhammed').innerText(),beforeWorker,'Selecting a worker does not count as assignment');
    await click(page,'worker-muhammed');assert.equal(await page.getByTestId('task-worker-task').count(),0,'Draft is not presented as assigned');await click(page,'assign-open');await click(page,'assignment-confirm');await route(page,'worker/muhammed');await page.getByTestId('task-worker-task').waitFor();await click(page,'task-worker-task');assert.match(await page.locator('#main').innerText(),/TEST Мухаммед/);
    await page.evaluate(()=>window.r3Demo.reset());await route(page,'review/unknown-review');await click(page,'reject:unknown-review');await click(page,'nav-more');await click(page,'more-sources');await click(page,'source-unknown-review');await page.getByText('Исходник и история',{exact:true}).click();assert.match(await page.locator('#main').innerText(),/TEST: общий итог на второй странице не виден/);assert.equal(await page.getByTestId('confirm-proposal').count(),0);
    await route(page,'review/clarify');await click(page,'edit-amount');await fill(page,'fact-input','1,10');await click(page,'sheet-save');await click(page,'confirm-proposal');await route(page,'review/clarify');assert.equal(await page.getByTestId('edit-amount').count(),0,'Confirmed detail is read-only');
    report.checks.push('Rejected source remains discoverable through More → Sources; rejected and confirmed details offer no confirmation or amount edit');
    report.checks.push('Browser Back restores exact review-card focus; 1,10 TRY preserved; selecting worker creates no assigned count/detail before reviewed confirmation');
    await context.addInitScript(()=>{Object.defineProperty(window,'SpeechRecognition',{configurable:true,value:undefined});Object.defineProperty(window,'webkitSpeechRecognition',{configurable:true,value:undefined});});
    await route(page,'home');await page.reload();await ready(page);await click(page,'capture-voice');await page.getByText('Голосовой ввод недоступен',{exact:true}).waitFor();await screenshot(page,'chromium','state-voice-unsupported-simulated',390,'Controlled unsupported-speech capability simulation');await click(page,'voice-fallback');await page.getByTestId('capture-input').waitFor();
    report.checks.push('Explicit negative-capability simulation removes browser speech constructors and verifies unsupported → text fallback; this is not a microphone test');
    report.checks.push('Reload and browser Back/Forward preserve draft; cancel/Escape restores focus and retains typed fact; unsupported photo and real selected image; reduced-viewport/keyboard reachability');
  }catch(error){await captureFailure(page);throw error;}finally{await context.close();}
}

try{
  for(const [engine,type] of [['chromium',chromium],['webkit',webkit]]){
    const browser=await type.launch({headless:true});
    try{await main(browser,engine);}finally{await browser.close();}
  }
  assert.equal(report.journeys.length,8);
  assert.deepEqual(report.failures,[],'All collected browser/layout/journey assertions must pass');
  assert.ok(report.journeys.every(j=>j.status==='PASS'));
  assert.deepEqual(report.errors,[],'Browser console/network isolation errors');
  report.status='PASS';
}catch(error){
  report.status='FAIL';report.failure={message:error.message,stack:error.stack};
  if(currentPage&&!currentPage.isClosed()){
    await rawShot(currentPage,{path:path.join(artifacts,'failure.png'),fullPage:true}).catch(()=>{});
    await fs.writeFile(path.join(artifacts,'failure.html'),await currentPage.content()).catch(()=>{});
  }
  process.exitCode=1;
}finally{
  report.finished_at=new Date().toISOString();
  await fs.writeFile(path.join(artifacts,'browser-report.json'),JSON.stringify(report,null,2)+'\n');
  await fs.writeFile(path.join(artifacts,'journey-metrics.json'),JSON.stringify({feature_sha:expectedSha,method:'Trusted DOM click events from Home, visible form entry events, actual manual field changes. Dropdown selection uses actual keyboard Home/Arrow/Enter events listed separately. Native file chooser/keyboard tap costs are unmeasured. Photo journey3 explicitly uses the prepared synthetic-source button; real file attachment is covered separately without claiming picker-tap counts.',journeys:report.journeys},null,2)+'\n');
  console.log(`R3 browser ${report.status}: ${report.screenshots.length} screenshots, ${report.journeys.length}/8 measured journeys, SHA ${expectedSha}`);
  if(report.failure)console.error(report.failure.message);
}
