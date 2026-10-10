// Exact-SHA browser evidence for the isolated synthetic prototype, never the backend.
import assert from 'node:assert/strict';
import {chromium,webkit} from 'playwright';
import fs from 'node:fs/promises';
import {createHash} from 'node:crypto';
import path from 'node:path';
import {runStockReceiptJourneys} from './owner-r3-stock-receipt.mjs';
import {runDailyJourneys} from './owner-r3-daily.mjs';
import {runRefinementJourneys} from './owner-r3-refinement.mjs';
import {runFertilizerJourneys} from './owner-r3-fertilizer.mjs';

const base=process.env.PREVIEW_URL||'http://127.0.0.1:4173';
const artifacts=process.env.E2E_ARTIFACTS||'artifacts/owner-r3-evidence';
const expectedSha=process.env.OWNER_PREVIEW_FEATURE_SHA;
assert.match(expectedSha||'',/^[a-f0-9]{40}$/,'Set the exact build SHA before browser verification');
assert.ok(process.env.PREVIEW_SECRET,'An ephemeral preview key is required');
await fs.mkdir(artifacts,{recursive:true});
const report={feature_sha:expectedSha,started_at:new Date().toISOString(),screenshots:[],journeys:[],checks:[],limitations:['Chromium/WebKit automation is not physical iPhone verification.','Native microphone recording and a real iPhone software keyboard are not asserted by these tests.','Speech unavailable/error fallback is exercised without substituting a transcript for live microphone input.','Click totals are trusted browser DOM interactions, not physical iPhone tap estimates. Native picker/keyboard UI is not measured; the photo journey explicitly chooses a prepared synthetic source.'],errors:[],failures:[],tool_diagnostics:[]};
// Retain all original journey assertions. The owner-requested radial navigation adds one explicit trusted click to capture entry.
// The design-lock corrections below are separately measured in BOTH engines.
report.correction_journeys=[];
report.money_regressions=[];
report.limitations.push('The original eight measured journeys and interruption suite remain Chromium-only. Added design-lock correction journeys and the complete money/font matrix run in both Chromium and WebKit.');
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
async function click(page,id){if(['capture-photo','capture-voice','capture-text','capture-video'].includes(id)&&await page.getByTestId('radial-toggle').count()&&await page.getByTestId('radial-toggle').getAttribute('aria-expanded')!=='true'){await page.getByTestId('radial-toggle').click();}const target=page.getByTestId(id),href=await target.getAttribute('href');await target.click();if(href?.startsWith('#'))await page.waitForFunction(hash=>location.hash===hash,href);await ready(page);}
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
      const hitTarget=['checkbox','radio'].includes(el.type) && el.labels?.length ? el.labels[0] : el;
      const b=hitTarget.getBoundingClientRect();
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
  if(!['capture/','task/','quick/','daily/','assignment/','settlement/','settlement-review/','fertilizer/'].some(prefix=>routeName.startsWith(prefix))){
    assert.equal(await page.getByTestId('radial-toggle').isVisible(),true,'Central radial Capture is available');
    assert.equal(await page.getByTestId('radial-toggle').getAttribute('aria-expanded'),'false','Capture starts collapsed');
    for(const mode of ['photo','voice','text'])assert.equal(await page.getByTestId('capture-'+mode).isVisible(),false,`${mode} remains hidden until the owner opens Capture`);
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

async function correctionJourney(browser,engine,name,run){
  const {context,page}=await newPage(browser);
  page.__r3CorrectionSegments=[];
  await page.evaluate(()=>window.__r3Metrics.start());
  let status='PASS',failure;
  try{await run(page);}catch(error){status='FAIL';failure=error.message;recordFailure(`${engine} correction: ${name}`,error);await captureFailure(page);}
  const segments=[...page.__r3CorrectionSegments];
  let metricError;
  try{
    try{
      segments.push(await page.evaluate(()=>window.__r3Metrics.stop()));
      const clicks=segments.flatMap(segment=>segment.clicks);
      assert.ok(clicks.length>0&&clicks.every(event=>event.trusted),'Correction journeys use trusted browser interactions');
    }catch(error){
      status='FAIL';failure=failure?`${failure}; metrics: ${error.message}`:error.message;metricError=error;
      recordFailure(`${engine} correction metrics: ${name}`,error);
    }
    const raw=Object.fromEntries(['clicks','forms','inputs','keys'].map(key=>[key,segments.flatMap(segment=>segment[key]||[])]));
    report.correction_journeys.push({engine,name,status,...(failure?{failure,partial:true}:{}),start:'#home',end:await page.evaluate(()=>location.hash).catch(()=>null),clicks:raw.clicks.length,opened_forms:raw.forms.length,manual_field_edits:raw.inputs.length,keyboard_actions:raw.keys.length,click_events:raw.clicks,form_events:raw.forms,input_events:raw.inputs,key_events:raw.keys,metric_segments:segments});
    if(metricError)throw metricError;
  }finally{await context.close();}
}
async function correctionReload(page){
  // Reload creates a new document and its own metrics recorder. Preserve each
  // real segment in Node before navigation, then resume collection explicitly.
  page.__r3CorrectionSegments.push(await page.evaluate(()=>window.__r3Metrics.stop()));
  await page.reload();await ready(page);
  await page.evaluate(()=>window.__r3Metrics.start());
}
async function proposal(page,id){return page.evaluate(id=>window.r3Demo.state().proposals.find(item=>item.id===id),id);}
async function originalVisible(page,source){
  const original=page.getByTestId('source-original');
  assert.equal(await original.isVisible(),true,'The immutable original is visible alongside the current action');
  assert.ok((await original.innerText()).includes(source),'The original source remains verbatim');
  assert.equal(await page.getByText('ОРИГИНАЛ',{exact:true}).isVisible(),true,'Original evidence is explicitly labelled');
}
async function currentVisible(page){
  const current=page.getByTestId('current-action');
  assert.equal(await current.isVisible(),true,'Current edited meaning is visible');
  assert.equal(await page.getByText('ТЕКУЩЕЕ ПОДТВЕРЖДАЕМОЕ ДЕЙСТВИЕ',{exact:true}).isVisible(),true,'The action awaiting confirmation is explicitly labelled');
  return current.innerText();
}
async function editSingleFact(page,id,value,whileOpen){
  await click(page,id);
  assert.equal(await page.locator('dialog[open]').count(),1,'One focused fact sheet is open');
  assert.equal(await page.locator('dialog[open] input:not([type=hidden]),dialog[open] textarea,dialog[open] select').count(),1,'The sheet edits one fact, never a large form');
  assert.equal(await page.locator('dialog[open],[role=dialog][aria-modal=true]').count(),1,'Fact editing never stacks dialogs');
  await fill(page,'fact-input',value);
  if(whileOpen)await whileOpen();
  await click(page,'sheet-save');
  assert.equal(await page.locator('dialog[open]').count(),0,'Saving returns to the coherent review');
  assert.equal(await page.getByTestId(id).evaluate(element=>element===document.activeElement),true,'Saving restores focus to the edited fact');
}

// Inspect actual text glyph boxes, not only CSS white-space or the outer element.
// A wrapped number can fit the viewport perfectly and still fail this regression.
async function moneyGeometry(page){
  return page.evaluate(()=>{
    const issues=[],measurements=[];
    const box=rect=>({left:rect.left,right:rect.right,top:rect.top,bottom:rect.bottom,width:rect.width,height:rect.height});
    const glyphs=element=>{
      const result=[],walker=document.createTreeWalker(element,NodeFilter.SHOW_TEXT);
      while(walker.nextNode()){
        const node=walker.currentNode;
        for(let offset=0;offset<node.textContent.length;offset++){
          const character=node.textContent[offset];
          if(!/[0-9A-Z]/.test(character))continue;
          const range=document.createRange();range.setStart(node,offset);range.setEnd(node,offset+1);
          const rect=range.getBoundingClientRect();
          result.push({character,...box(rect)});
        }
      }
      return result;
    };
    const visible=(element,rect,label)=>{
      for(let ancestor=element;ancestor;ancestor=ancestor.parentElement){
        const style=getComputedStyle(ancestor),bounds=ancestor.getBoundingClientRect();
        if(style.visibility!=='visible'||style.display==='none'||Number(style.opacity)===0)issues.push(`${label} is visually hidden`);
        if(['hidden','clip','scroll','auto'].includes(style.overflowX)&&(rect.left<bounds.left-.75||rect.right>bounds.right+.75))issues.push(`${label} is horizontally clipped`);
        if(['hidden','clip','scroll','auto'].includes(style.overflowY)&&(rect.top<bounds.top-.75||rect.bottom>bounds.bottom+.75))issues.push(`${label} is vertically clipped`);
      }
      if(rect.width<=0||rect.height<=0)issues.push(`${label} has no visible glyph box`);
      if(rect.left<-.75||rect.right>innerWidth+.75)issues.push(`${label} is outside the viewport`);
    };
    for(const [index,card] of [...document.querySelectorAll('.metric-card')].entries()){
      const value=card.querySelector('.metric-value'),currency=card.querySelector('.currency'),cardBox=card.getBoundingClientRect();
      if(!value||!currency){issues.push(`KPI ${index} lacks a number or a visible currency element`);continue;}
      const digits=glyphs(value).filter(item=>/\d/.test(item.character)),letters=glyphs(currency),style=getComputedStyle(value),currencyStyle=getComputedStyle(currency);
      if(!digits.length)issues.push(`KPI ${index} has no digits`);
      if(digits.some(digit=>Math.abs(digit.top-digits[0].top)>1))issues.push(`KPI ${index} digits wrap internally: ${value.textContent}`);
      if(parseFloat(style.fontSize)<20)issues.push(`KPI ${index} digits are smaller than readable 20px`);
      if(currency.textContent.trim()!=='TRY'||letters.map(item=>item.character).join('')!=='TRY')issues.push(`KPI ${index} currency is missing or changed`);
      if(parseFloat(currencyStyle.fontSize)<12)issues.push(`KPI ${index} currency is smaller than readable 12px`);
      for(const [kind,items,element] of [['digit',digits,value],['currency',letters,currency]])for(const item of items){
        visible(element,item,`KPI ${index} ${kind} ${item.character}`);
        if(item.left<cardBox.left-.75||item.right>cardBox.right+.75||item.top<cardBox.top-.75||item.bottom>cardBox.bottom+.75)issues.push(`KPI ${index} ${kind} escapes its card`);
      }
      if(digits.some(digit=>letters.some(letter=>digit.left<letter.right-.5&&digit.right>letter.left+.5&&digit.top<letter.bottom-.5&&digit.bottom>letter.top+.5)))issues.push(`KPI ${index} digits overlap currency`);
      measurements.push({value:value.textContent.trim(),currency:currency.textContent.trim(),font_family:style.fontFamily,digit_font_px:parseFloat(style.fontSize),currency_font_px:parseFloat(currencyStyle.fontSize),digit_boxes:digits,currency_boxes:letters,card:box(cardBox)});
    }
    if(measurements.length!==2)issues.push(`Expected both receivable/payable KPIs, got ${measurements.length}`);
    if(Math.max(document.documentElement.scrollWidth,document.body.scrollWidth)>innerWidth+1)issues.push('Money layout causes horizontal page overflow');
    return {issues,measurements};
  });
}
async function moneyRegressions(browser,engine){
  const {context,page}=await newPage(browser);
  try{
    for(const font of ['intended','fallback'])for(const scale of ['normal','large']){
      await page.evaluate(()=>window.r3Demo.reset());
      if(scale==='large'){await route(page,'settings');await click(page,'toggle-large');}
      for(const width of [320,390,700,768,820,900,1280]){
        await page.setViewportSize({width,height:844});
        for(const routeName of ['home','finance/receivable']){
          await route(page,routeName);
          await page.evaluate(font=>{
            if(font==='fallback')document.documentElement.style.setProperty('font-family','system-ui, sans-serif','important');
            else document.documentElement.style.removeProperty('font-family');
          },font);
          await ready(page);
          let status='PASS',failure;
          const result=await moneyGeometry(page);
          try{
            assert.deepEqual(result.issues,[],`${width}px ${font} ${scale} #${routeName}: ${result.issues.join('; ')}`);
            const expected=await page.evaluate(()=>{
              const state=window.r3Demo.state();
              const total=items=>items.reduce((sum,item)=>sum+Math.round(item.amount*100),0)/100;
              return [total(state.receivables),total(state.payables)].map(value=>new Intl.NumberFormat('ru-RU',{maximumFractionDigits:2}).format(value));
            });
            assert.deepEqual(result.measurements.map(item=>item.value),expected,'Money values stay exact, with no layout-driven rounding or truncation');
            if(font==='fallback')assert.ok(result.measurements.every(item=>/^system-ui,/.test(item.font_family)),'The alternate system font stack actually applies to both KPIs');
            assert.equal(await page.evaluate(()=>window.r3Demo.state().largeAmount),scale==='large','The intended normal/large data mode is active');
            if(scale==='large')assert.ok(await page.evaluate(()=>window.r3Demo.state().receivables.some(r=>r.id==='stress'&&r.amount>=123456789.12)),'Large mode really includes a nine-digit exact money value');
          }catch(error){status='FAIL';failure=error.message;recordFailure(`${engine} money ${font} ${scale} ${width} #${routeName}`,error);}
          report.money_regressions.push({engine,font,scale,width,route:'#'+routeName,status,...(failure?{failure}:{}),...result});
          // Fresh, deterministic evidence for every matrix point, including the
          // formerly failing 700/768/820 widths and both 320px large-money views.
          await screenshot(page,engine,`correction-money-${font}-${scale}-${routeName}`,width,`${routeName}: ${scale} money, ${font} system stack`);
        }
      }
    }
  }catch(error){recordFailure(`${engine} money regression setup`,error);await captureFailure(page);}finally{await context.close();}
}

async function correctionJourneys(browser,engine){
  await correctionJourney(browser,engine,'Task: corrected place changes current meaning, original stays immutable',async page=>{
    await click(page,'capture-text');await fill(page,'capture-input',SCENARIO);await click(page,'capture-save');await page.getByTestId('task-save').waitFor();
    const before=await proposal(page,'capture-1'),sourceRecords=await page.evaluate(()=>window.r3Demo.state().sources);
    await originalVisible(page,SCENARIO);
    assert.match(await currentVisible(page),/Özvadi/);
    await editSingleFact(page,'edit-place','İmamlı');
    const current=await currentVisible(page),after=await proposal(page,'capture-1');
    assert.match(current,/Переделать полив/);assert.match(current,/İmamlı/);assert.match(current,/Следующая неделя/);
    assert.doesNotMatch(current,/Özvadi|Озвади/i,'Old source location is not presented as the current actionable instruction');
    assert.equal(after.place,'İmamlı');assert.equal(after.when,before.when);assert.equal(after.instruction,before.instruction);assert.equal(after.source,before.source);
    assert.deepEqual(await page.evaluate(()=>window.r3Demo.state().sources),sourceRecords,'Fact corrections never rewrite the stored original');
    await originalVisible(page,SCENARIO);await screenshot(page,engine,'correction-task-place',390,'Corrected current action İmamlı, immutable original Özvadi');
    await correctionReload(page);assert.match(await currentVisible(page),/İmamlı/);await originalVisible(page,SCENARIO);
    await click(page,'task-save');await page.getByTestId('task-capture-1').waitFor();
    assert.match(await page.getByTestId('task-capture-1').innerText(),/İmamlı/);assert.doesNotMatch(await page.getByTestId('task-capture-1').innerText(),/Özvadi|Озвади/i);
    const saved=await page.evaluate(()=>window.r3Demo.state().tasks.find(item=>item.id==='capture-1'));
    assert.equal(saved.place,'İmamlı');assert.equal(saved.when,before.when);assert.equal(saved.source,SCENARIO);assert.equal(saved.worker,null);assert.equal(saved.date,null);
    await click(page,'task-capture-1');assert.equal(await page.getByTestId('current-action').innerText(),saved.title);assert.doesNotMatch(await page.getByTestId('current-action').innerText(),/Özvadi|Озвади/i);assert.match(await page.getByTestId('edit-place').innerText(),/İmamlı/);assert.match(await page.getByTestId('edit-when').innerText(),/Следующая неделя/);await originalVisible(page,SCENARIO);
  });
  await correctionJourney(browser,engine,'Stock: quantity, unit, product and place use separate focused fact sheets',async page=>{
    await click(page,'nav-review');await click(page,'review-stock-review');
    const original=(await proposal(page,'stock-review')).source,stockBefore=await page.evaluate(()=>window.r3Demo.state().stock);
    const stockFacts=item=>({quantity:item.quantity,unit:item.unit,product:item.product??item.title,place:item.place});
    for(const [key,value,expected] of [['quantity','125,5',125.5],['unit','л','л'],['product','Кальциевая селитра','Кальциевая селитра'],['place','Özvadi','Özvadi']]){
      const before=stockFacts(await proposal(page,'stock-review'));
      await editSingleFact(page,`edit-${key}`,value,key==='quantity'?()=>screenshot(page,engine,'correction-stock-fact-sheet',390,'Stock: one quantity field in one small sheet'):undefined);
      const after=await proposal(page,'stock-review');
      assert.deepEqual(stockFacts(after),{...before,[key]:expected},`Changing ${key} preserves every other selected stock fact`);
      assert.equal(after.source,original);await originalVisible(page,original);await currentVisible(page);
    }
    const current=await currentVisible(page);assert.match(current,/125,5/);assert.match(current,/Кальциевая селитра/);assert.match(current,/Özvadi/);assert.doesNotMatch(current,/KNO[₃3]/);
    assert.deepEqual(await page.evaluate(()=>window.r3Demo.state().stock),stockBefore,'Edits and review do not prematurely change inventory');
    await screenshot(page,engine,'correction-stock-edited-review',390,'Stock corrected quantity, unit, product and location with original evidence');
    await click(page,'confirm-proposal');
    const stockAfter=await page.evaluate(()=>window.r3Demo.state().stock);
    for(const row of stockBefore)assert.deepEqual(stockAfter.find(item=>item.id===row.id),row,'An edited product/unit/place tuple must never update the original KNO₃ inventory row');
    const selected=stockAfter.filter(row=>row.name==='Кальциевая селитра'&&row.unit==='л'&&row.place==='Özvadi');
    assert.equal(selected.length,1,'The receipt effect uses exactly the owner-selected product/unit/place tuple');
    assert.equal(selected[0].quantity,null,'A receipt does not invent an opening balance for a new selected tuple');
    assert.equal(selected[0].movements.filter(item=>item.id==='stock-review').length,1);assert.equal(selected[0].movements.find(item=>item.id==='stock-review').quantity,125.5);
    assert.equal((await proposal(page,'stock-review')).source,original);
  });
  await correctionJourney(browser,engine,'Depot: weight, price and amount stay explicit and mismatch blocks confirmation',async page=>{
    await click(page,'nav-review');await click(page,'review-depot-review');
    const before=await proposal(page,'depot-review'),receivables=await page.evaluate(()=>window.r3Demo.state().receivables);
    await editSingleFact(page,'edit-kg','1250',()=>screenshot(page,engine,'correction-depot-fact-sheet',390,'Depot: one weight field in one small sheet'));
    let updated=await proposal(page,'depot-review');assert.equal(updated.kg,1250);assert.equal(updated.amount,before.amount);assert.equal(updated.price,before.price);
    assert.equal(await page.getByTestId('confirm-proposal').count(),0,'Mismatched weight × price does not silently confirm');
    await editSingleFact(page,'edit-price','32');updated=await proposal(page,'depot-review');assert.equal(updated.price,32);assert.equal(updated.kg,1250);assert.equal(updated.amount,before.amount,'Changing price never silently rewrites the source amount');
    assert.equal(await page.getByTestId('confirm-proposal').count(),0,'An inconsistent amount still blocks confirmation');
    await editSingleFact(page,'edit-amount','40000');updated=await proposal(page,'depot-review');assert.equal(updated.amount,40000);assert.equal(updated.kg,1250);assert.equal(updated.price,32);assert.equal(updated.source,before.source);
    await originalVisible(page,before.source);const current=await currentVisible(page);assert.match(current,/1[\s\u00a0]250/);assert.match(current,/40[\s\u00a0]000/);assert.match(current,/32/);
    assert.deepEqual(await page.evaluate(()=>window.r3Demo.state().receivables),receivables,'Fact edits do not prematurely change receivables');
    assert.equal(await page.getByTestId('confirm-proposal').isVisible(),true,'Explicitly reconciled facts can be reviewed and confirmed');
    await screenshot(page,engine,'correction-depot-edited-review',390,'Depot owner-corrected weight, price and amount with unchanged original');
    await click(page,'confirm-proposal');
    const state=await page.evaluate(()=>window.r3Demo.state()),activity=state.activity.filter(item=>item.id==='depot-review');
    assert.equal(activity.length,1);assert.equal(activity[0].kg,1250);assert.equal(activity[0].revenue,40000);assert.equal(state.receivables[0].amount,receivables[0].amount+40000);assert.equal(state.proposals.find(item=>item.id==='depot-review').source,before.source);
  });
  await correctionJourney(browser,engine,'Photo: owner caption remains visible and distinct from document evidence',async page=>{
    const caption='Проверьте вес и дату на чеке.';
    await click(page,'capture-photo');await click(page,'demo-photo');await fill(page,'capture-comment',caption);await click(page,'capture-save');await page.getByTestId('confirm-proposal').waitFor();
    const receipt=await proposal(page,'depot-review');await originalVisible(page,receipt.source);
    const comment=page.getByTestId('owner-comment');assert.equal(await comment.isVisible(),true);assert.ok((await comment.innerText()).includes(caption));assert.match(await page.locator('#main').innerText(),/Комментарий владельца/);
    assert.equal(await comment.evaluate(element=>!!element.closest('[data-testid="source-original"]')),false,'Caption is separate from immutable document text');
    assert.equal(receipt.source.includes(caption),false,'Owner caption is not silently folded into extracted source text');
    assert.equal(await page.locator('#main img').filter({visible:true}).count()>0,true,'Document image is visible alongside its owner context');
    const sources=await page.evaluate(()=>window.r3Demo.state().sources.filter(item=>item.proposalId==='depot-review'));assert.equal(sources.length,1);assert.equal(sources[0].comment,caption);assert.equal(sources[0].id,'capture-1');
    await screenshot(page,engine,'correction-photo-caption',390,'Photo source and separately labelled owner caption');
    await correctionReload(page);assert.ok((await page.getByTestId('owner-comment').innerText()).includes(caption));await originalVisible(page,receipt.source);
  });
  await correctionJourney(browser,engine,'Photo: explicit contradictory-caption storyboard requires an owner clarification',async page=>{
    await click(page,'capture-photo');await click(page,'demo-photo-conflict');await click(page,'capture-save');await page.getByTestId('caption-conflict').waitFor();
    const before=await proposal(page,'depot-review'),sourceRecords=await page.evaluate(()=>window.r3Demo.state().sources);
    assert.equal(before.kg,1200,'Document weight is not overwritten by the owner comment');
    await originalVisible(page,before.source);assert.match(await page.getByTestId('owner-comment').innerText(),/1 100 кг/);assert.equal(await page.getByTestId('caption-conflict').isVisible(),true);
    assert.equal(await page.getByTestId('confirm-proposal').count(),0,'Contradictory caption/source needs explicit clarification before confirmation');
    await screenshot(page,engine,'correction-photo-caption-conflict',390,'Explicit DEMO contradiction: document 1 200 kg and owner comment 1 100 kg');
    await click(page,'resolve-caption-source');await page.getByTestId('confirm-proposal').waitFor();
    const after=await proposal(page,'depot-review');assert.equal(after.kg,1200);assert.equal(after.amount,before.amount);assert.equal(after.source,before.source);assert.equal(after.status,'pending','Clarification does not itself confirm the proposal');
    assert.deepEqual(await page.evaluate(()=>window.r3Demo.state().sources),sourceRecords,'Resolving a contradiction never rewrites either evidence source');
    assert.match(await page.getByTestId('owner-comment').innerText(),/1 100 кг/);await originalVisible(page,before.source);
    await screenshot(page,engine,'correction-photo-caption-resolved',390,'Owner explicitly kept document facts; original caption remains visible');
  });
  await correctionJourney(browser,engine,'Photo: explicitly using the owner caption preserves evidence and requires amount reconciliation',async page=>{
    await click(page,'capture-photo');await click(page,'demo-photo-conflict');await click(page,'capture-save');await page.getByTestId('caption-conflict').waitFor();
    const before=await proposal(page,'depot-review'),sourceRecords=await page.evaluate(()=>window.r3Demo.state().sources);
    await click(page,'resolve-caption-comment');
    const selected=await proposal(page,'depot-review');assert.equal(selected.kg,1100);assert.equal(selected.price,before.price);assert.equal(selected.amount,before.amount);assert.equal(selected.captionResolution,'comment');assert.equal(selected.status,'pending');
    assert.equal(await page.getByTestId('depot-mismatch').isVisible(),true);assert.equal(await page.getByTestId('confirm-proposal').count(),0,'Selecting a different weight still requires explicit reconciliation of the document amount');
    await originalVisible(page,before.source);assert.match(await page.getByTestId('owner-comment').innerText(),/1 100 кг/);
    await editSingleFact(page,'edit-amount','34100');await page.getByTestId('confirm-proposal').waitFor();
    const current=await currentVisible(page);assert.match(current,/1[\s\u00a0]100/);assert.match(current,/34[\s\u00a0]100/);
    assert.deepEqual(await page.evaluate(()=>window.r3Demo.state().sources),sourceRecords,'The alternative correction path also preserves both immutable evidence sources');
    assert.equal((await proposal(page,'depot-review')).source,before.source);
    await screenshot(page,engine,'correction-photo-caption-used',390,'Owner explicitly used 1 100 kg comment and reconciled amount; document stays unchanged');
  });
  await correctionJourney(browser,engine,'Unknown: one intent clarification preserves Serkan, tomorrow and the source without re-entry',async page=>{
    await click(page,'capture-text');await click(page,'demo-recovery');
    const original=await page.getByTestId('capture-input').inputValue();assert.match(original,/Серкану завтра нужно/);
    await click(page,'capture-save');await page.getByTestId('clarify-intent-reminder').waitFor();
    const before=await proposal(page,'capture-1'),sourceRecords=await page.evaluate(()=>window.r3Demo.state().sources);
    assert.equal(before.type,'unknown');assert.equal(before.party,'TEST Серкан');assert.equal(before.when,'Завтра');assert.equal(before.date,null);assert.equal(before.worker,null);
    await originalVisible(page,original);assert.match(await page.locator('#main').innerText(),/Что произошло\?/);assert.match(await page.locator('#main').innerText(),/TEST Серкан/);assert.match(await page.locator('#main').innerText(),/Завтра/);
    assert.equal(await page.getByTestId('capture-input').count(),0,'Saved source is not requested again');assert.equal(await page.getByTestId('confirm-proposal').count(),0);assert.equal(await page.getByTestId('task-save').count(),0);
    await screenshot(page,engine,'correction-unknown-known-facts',390,'Unknown event: original saved, Serkan and tomorrow retained, one clarification');
    await click(page,'clarify-intent-reminder');await page.getByTestId('task-save').waitFor();
    const after=await proposal(page,'capture-1');assert.equal(after.type,'task');assert.equal(after.party,before.party);assert.equal(after.when,before.when);assert.equal(after.source,before.source);assert.equal(after.date,null);assert.equal(after.worker,null);
    const current=await currentVisible(page);assert.match(current,/Связаться с Серканом/);assert.match(current,/Завтра/);await originalVisible(page,original);
    assert.equal(await page.getByTestId('capture-input').count(),0);assert.equal(await page.locator('dialog[open]').count(),0,'Clarification returns directly to prefilled review, without a blank category form');
    assert.deepEqual(await page.evaluate(()=>window.r3Demo.state().sources),sourceRecords,'A clarification preserves the original source record rather than capturing it again');
    await screenshot(page,engine,'correction-unknown-recovered',390,'Recovered prefilled task: Serkan and tomorrow, no source re-entry');
    await click(page,'task-save');await page.getByTestId('task-capture-1').waitFor();
    const saved=await page.evaluate(()=>window.r3Demo.state().tasks.find(item=>item.id==='capture-1'));assert.equal(saved.party,'TEST Серкан');assert.equal(saved.when,'Завтра');assert.equal(saved.source,original);assert.equal(saved.worker,null);assert.equal(saved.date,null);
    assert.equal(await page.evaluate(()=>window.__r3Metrics.stop().inputs.length),0,'The recovery journey requires zero manual field entry or source re-entry');
    assert.equal(await page.evaluate(()=>window.r3Demo.state().sources.length),1,'Recovery creates no duplicate source');
  });
  await correctionJourney(browser,engine,'Home: compact worker exception opens the actual issue',async page=>{
    const before=await page.evaluate(()=>window.r3Demo.state().workerTasks),signal=page.getByTestId('home-worker-exception');
    assert.equal(await signal.isVisible(),true);assert.equal(await signal.evaluate(element=>{const rect=element.getBoundingClientRect();return rect.top>=0&&rect.bottom<=visualViewport.height;}),true,'Worker exception is discoverable in the initial attention area');
    assert.match(await signal.innerText(),/1/);await screenshot(page,engine,'correction-home-worker-signal',390,'Unchanged Home composition with compact worker-exception attention signal');
    await click(page,'home-worker-exception');assert.equal(await page.evaluate(()=>location.hash),'#task/filter-report');assert.match(await page.locator('#main').innerText(),/Нужен запасной фильтр/);
    assert.deepEqual(await page.evaluate(()=>window.r3Demo.state().workerTasks),before,'Viewing an exception does not change a worker assignment');
  });
  await correctionJourney(browser,engine,'Saved task: edits target the task, stale owner prose requires review, DONE and restore remain available after correction',async page=>{
    await click(page,'capture-text');await click(page,'demo-irrigation');await click(page,'capture-save');await page.getByTestId('task-save').waitFor();await click(page,'task-save');await page.getByTestId('task-capture-1').waitFor();await click(page,'task-capture-1');
    const confirmed=await proposal(page,'capture-1'),sources=await page.evaluate(()=>window.r3Demo.state().sources);
    assert.equal(confirmed.status,'confirmed');
    await editSingleFact(page,'edit-place','İmamlı');
    assert.equal(await page.evaluate(()=>window.r3Demo.state().tasks.find(item=>item.id==='capture-1').place),'İmamlı');
    assert.deepEqual(await proposal(page,'capture-1'),confirmed,'A captured task edit targets the saved task, not its read-only confirmed proposal');
    await editSingleFact(page,'edit-instruction','Переделать полив на İmamlı');await editSingleFact(page,'edit-place','Yeşilöz');
    assert.equal(await page.getByTestId('task-needs-review').isVisible(),true);assert.equal(await page.getByTestId('task-save').count(),0,'Stale owner prose cannot be marked DONE before review');
    assert.doesNotMatch(await page.getByTestId('current-action').innerText(),/İmamlı|Переделать полив на/,'Stale prose is not presented as the current actionable instruction');
    assert.match(await page.getByTestId('edit-place').innerText(),/Yeşilöz/);await originalVisible(page,SCENARIO);
    await screenshot(page,engine,'correction-saved-task-prose-guard',390,'Saved task: changed place requires reviewing owner-written instruction');
    await editSingleFact(page,'edit-instruction','Переделать полив');assert.equal(await page.getByTestId('task-needs-review').count(),0);assert.equal(await page.getByTestId('current-action').innerText(),'Переделать полив');assert.equal(await page.getByTestId('task-save').isVisible(),true);
    await click(page,'task-save');assert.equal(await page.evaluate(()=>window.r3Demo.state().tasks.find(item=>item.id==='capture-1').status),'done');assert.match(await page.getByTestId('task-save').innerText(),/Вернуть в задачи/);
    await click(page,'task-save');const restored=await page.evaluate(()=>window.r3Demo.state().tasks.find(item=>item.id==='capture-1'));assert.equal(restored.status,'todo');assert.equal(restored.place,'Yeşilöz');assert.equal(restored.title,'Переделать полив');assert.equal(restored.source,SCENARIO);
    assert.deepEqual(await proposal(page,'capture-1'),confirmed);assert.deepEqual(await page.evaluate(()=>window.r3Demo.state().sources),sources);
    await screenshot(page,engine,'correction-saved-task-restored',390,'Corrected saved task after DONE and restore, original evidence retained');
  });
  await correctionJourney(browser,engine,'Photo: replacing or clearing the prepared conflict caption cannot fabricate a contradiction',async page=>{
    for(const [name,caption] of [['replaced','Проверьте только дату документа.'],['cleared','']]){
      // Already on Home for the first case; use the actual navigation link
      // between cases. Same-URL page.goto can reload WebKit and lose metrics.
      if(await page.evaluate(()=>location.hash)!=='#home')await click(page,'nav-home');
      await page.evaluate(()=>window.r3Demo.reset());await ready(page);await click(page,'capture-photo');await click(page,'demo-photo-conflict');await fill(page,'capture-comment',caption);
      assert.equal(await page.evaluate(()=>window.r3Demo.state().draft.scenario),'receipt','Editing the exact prepared contradiction caption leaves the conflict storyboard');
      await click(page,'capture-save');await page.getByTestId('confirm-proposal').waitFor();
      const receipt=await proposal(page,'depot-review');assert.equal(receipt.kg,1200);assert.equal(!!receipt.captionConflict,false);assert.equal(await page.getByTestId('caption-conflict').count(),0);assert.doesNotMatch(await page.locator('#main').innerText(),/1[\s\u00a0]100/,'Generic or blank caption never generates the removed 1 100 kg assertion');
      const sources=await page.evaluate(()=>window.r3Demo.state().sources);assert.equal(sources.length,1);assert.equal(sources[0].comment,caption);
      if(caption)assert.ok((await page.getByTestId('owner-comment').innerText()).includes(caption));else assert.equal(await page.getByTestId('owner-comment').count(),0);
      await originalVisible(page,receipt.source);await screenshot(page,engine,`correction-photo-conflict-caption-${name}`,390,`Prepared conflict caption ${name}: no fabricated weight disagreement`);
    }
  });
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
  await correctionJourneys(browser,engine);
  await moneyRegressions(browser,engine);
  await runStockReceiptJourneys({browser,engine,newPage,click,fill,ready,route,screenshot,layout,report,correctionJourney,correctionReload});
  await runDailyJourneys({browser,engine,newPage,click,fill,ready,route,screenshot,layout,report,correctionJourney,correctionReload});
  await runRefinementJourneys({browser,engine,newPage,click,fill,ready,route,screenshot,layout,report,correctionJourney,correctionReload});
  await runFertilizerJourneys({browser,engine,newPage,click,fill,ready,route,screenshot,layout,report,correctionJourney,correctionReload});
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
    await click(page,'nav-tasks');await click(page,'tasks-workers');await click(page,'worker-muhammed');await click(page,'assign-open');assert.equal(await page.locator('.dock').count(),0,'Assignment opens as a focused page');assert.equal(await page.getByTestId('back').isVisible(),true);
    await click(page,'choose-worker');await click(page,'worker-option-muhammed');
    await page.getByTestId('assignment-confirm').waitFor();
    assert.match(await page.locator('#main').innerText(),/Не включать насос/);assert.match(await page.locator('.translation').innerText(),/پمپ را روشن نکنید/);
    assert.equal(await page.evaluate(()=>window.r3Demo.state().workerTask.translationReviewed),false,'Review is never automatically marked');
    assert.match(await page.getByTestId('assignment-confirm').innerText(),/Проверил текст/);
    assert.equal(await page.locator('.dock').count(),0,'Focused assignment hides capture/navigation dock');
    for(const height of [844,460]){
      await page.setViewportSize({width:390,height});
      await page.getByTestId('back').evaluate(el=>el.scrollIntoView({block:'center'}));
      assert.equal(await page.getByTestId('back').evaluate(el=>{const b=el.getBoundingClientRect();return b.top>=0&&b.bottom<=visualViewport.height;}),true,'Back remains reachable on focused assignment');
      await page.getByTestId('assignment-confirm').evaluate(el=>el.scrollIntoView({block:'center'}));
      assert.equal(await page.getByTestId('assignment-confirm').evaluate(el=>{const b=el.getBoundingClientRect(),hit=document.elementFromPoint(b.x+b.width/2,b.y+b.height/2);return b.top>=0&&b.bottom<=visualViewport.height&&b.left>=0&&b.right<=visualViewport.width&&(hit===el||el.contains(hit));}),true,'Assignment CTA is fully inside visual viewport without overlay');
      if(height===460)await screenshot(page,'chromium','state-assignment-reduced-height',390,'Focused assignment at reduced-height keyboard proxy');
    }
    await page.setViewportSize({width:390,height:844});await screenshot(page,'chromium','state-assignment-before-confirmation',390,'Focused reviewed assignment before confirmation');await click(page,'assignment-confirm');
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
  assert.equal(report.stock_receipt_journeys.length,12,'Six stock receipt journeys in both engines');
  assert.ok(report.stock_receipt_journeys.every(item=>item.status==='PASS'));
  assert.equal(report.stock_receipt_layouts.length,42,'Three receipt layout groups at seven widths in both engines');
  assert.equal(report.refinement_journeys.length,14,'Seven refinement journeys in both engines');
  assert.ok(report.refinement_journeys.every(item=>item.status==='PASS'));
  assert.equal(report.refinement_layout_checks.length,56,'Four refinement pages by seven widths by two engines');
  assert.equal(report.fertilizer_journeys.length,8,'Four fertilizer journeys in both engines');
  assert.ok(report.fertilizer_journeys.every(item=>item.status==='PASS'));
  assert.equal(report.fertilizer_layout_checks.length,14,'Builder and review checked together at seven widths in both engines');
  assert.equal(report.daily_journeys.length,22,'Eleven additive daily journeys in both engines');
  assert.ok(report.daily_journeys.every(item=>item.status==='PASS'),'All daily journeys pass');
  assert.equal(report.daily_layout_checks.length,98,'Seven daily pages by seven widths by two engines');
  assert.equal(report.journeys.length,8);
  assert.equal(report.correction_journeys.length,20,'All ten focused correction journeys run in each engine');
  assert.ok(report.correction_journeys.every(item=>item.status==='PASS'),'Both-engine correction journeys pass');
  assert.equal(report.money_regressions.length,112,'Both engines cover 7 widths × 2 font stacks × 2 amount scales × Home/Finance');
  assert.ok(report.money_regressions.every(item=>item.status==='PASS'),'Every money/font geometry case passes');
  for(const engine of ['chromium','webkit']){
    assert.equal(report.correction_journeys.filter(item=>item.engine===engine).length,10);
    assert.equal(report.money_regressions.filter(item=>item.engine===engine).length,56);
  }
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
  await fs.writeFile(path.join(artifacts,'stock-receipt-metrics.json'),JSON.stringify({feature_sha:expectedSha,journeys:report.stock_receipt_journeys,layouts:report.stock_receipt_layouts,limitations:['Native iPhone keyboard/picker remain unverified; attachments use real browser file-input APIs with synthetic PNG bytes.']},null,2)+'\n');
  await fs.writeFile(path.join(artifacts,'daily-journey-metrics.json'),JSON.stringify({feature_sha:expectedSha,method:'Trusted DOM clicks from Home, with explicit radial expansion counted. Both engines, eleven additive daily journeys, 98 daily screen/width cases.',journeys:report.daily_journeys,layouts:report.daily_layout_checks},null,2)+'\n');
  await fs.writeFile(path.join(artifacts,'refinement-journey-metrics.json'),JSON.stringify({feature_sha:expectedSha,method:'Seven trusted browser journeys in each engine: contextual settlements, monthly history, durable report-context evidence and explicit link review, contextual worker and storage recovery. Native picker/camera are not measured.',journeys:report.refinement_journeys,layouts:report.refinement_layout_checks},null,2)+'\n');
  await fs.writeFile(path.join(artifacts,'fertilizer-journey-metrics.json'),JSON.stringify({feature_sha:expectedSha,method:'Four trusted browser journeys per engine: per-da multi-component, normal CURRENT topology with unknown mappings acknowledged, separate preparations/overrides, and seven-width builder/review with reload and zero stock/finance effects.',journeys:report.fertilizer_journeys,layouts:report.fertilizer_layout_checks},null,2)+'\n');
  await fs.writeFile(path.join(artifacts,'browser-report.json'),JSON.stringify(report,null,2)+'\n');
  await fs.writeFile(path.join(artifacts,'journey-metrics.json'),JSON.stringify({feature_sha:expectedSha,method:'Trusted DOM click events from Home, visible form entry events, actual manual field changes. Dropdown selection uses actual keyboard Home/Arrow/Enter events listed separately. Native file chooser/keyboard tap costs are unmeasured. Photo journey3 explicitly uses the prepared synthetic-source button; real file attachment is covered separately without claiming picker-tap counts.',journeys:report.journeys},null,2)+'\n');
  await fs.writeFile(path.join(artifacts,'correction-journey-metrics.json'),JSON.stringify({feature_sha:expectedSha,method:'Ten additional trusted browser journeys in each engine. Source/current coherence, each stock/depot fact sheet, separate photo caption, both explicit contradiction resolutions, zero-entry Serkan/tomorrow recovery, compact Home worker signal, saved-task correction/prose guard/DONE/restore, and replaced/cleared conflict captions. Original eight Chromium journeys are retained separately.',journeys:report.correction_journeys},null,2)+'\n');
  await fs.writeFile(path.join(artifacts,'money-regressions.json'),JSON.stringify({feature_sha:expectedSha,method:'Actual per-character Range glyph geometry. Every digit stays on one line inside its card/viewport; TRY remains visible and unclipped. Exact displayed values are compared with current demo state. Digits >=20px and currency >=12px. Intended and system-ui fallback stacks, normal and large values, Home and Finance, 320/390/700/768/820/900/1280, Chromium and WebKit.',cases:report.money_regressions},null,2)+'\n');
  const artifactFiles=await fs.readdir(artifacts), manifest=[];
  for(const file of artifactFiles.filter(f=>/\.(png|json|txt)$/.test(f)&&f!=='evidence-manifest.json')){
    const bytes=await fs.readFile(path.join(artifacts,file));
    manifest.push({file,bytes:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex'),part:file.endsWith('.png')?(file.includes('-stock-receipt-')?'stock-receipt':file.includes('-fertilizer-')?'fertilizer':file.includes('-refinement-')?'refinement':file.includes('-daily-')?'daily':file.startsWith('chromium-')?'chromium-core':file.startsWith('webkit-')?'webkit-core':'metadata'):'metadata'});
  }
  await fs.writeFile(path.join(artifacts,'evidence-manifest.json'),JSON.stringify({feature_sha:expectedSha,parts:['chromium-core','webkit-core','daily','refinement','fertilizer','stock-receipt','metadata'],files:manifest},null,2)+'\n');
  for(const part of ['chromium-core','webkit-core','daily','refinement','fertilizer','stock-receipt','metadata'])assert.ok(manifest.filter(f=>f.part===part).reduce((n,f)=>n+f.bytes,0)<25*1024*1024,`Evidence ${part} must fit the supported artifact transfer limit`);
  console.log(`R3 browser ${report.status}: ${report.screenshots.length} screenshots, ${report.journeys.length}/8 measured journeys, SHA ${expectedSha}`);
  console.log(`R3 correction evidence: ${report.correction_journeys.length}/20 added both-engine journeys; ${report.money_regressions.length}/112 money/font cases`);
  console.log(`R3 owner-B evidence: ${report.refinement_journeys?.length||0}/14 contextual journeys; ${report.fertilizer_journeys?.length||0}/8 fertilizer journeys`);
  if(report.failure)console.error(report.failure.message);
}
