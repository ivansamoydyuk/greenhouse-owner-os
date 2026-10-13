import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

const harness=readFileSync(new URL('../e2e/capture-daily-owner-journeys.mjs',import.meta.url),'utf8');
const start=harness.indexOf('  const preparedLink=');
const end=harness.indexOf('\n  const edit=',start);
assert.ok(start>0&&end>start);
const snippet=harness.slice(start,end);
const source={id:'source-id',proposals:[{id:'proposal-id'}]},href='#review/proposal-id';

// Execute the actual harness helper against a changing document model. This
// verifies synchronization and failure semantics, not browser acceptance.
async function run({mode='candidate',initial='pending',next='prepared',wrongRoute=false,wrongRender=false,open=true}={}){
  const previous={document:globalThis.document,location:globalThis.location},events=[];
  let state=initial,generation=1,oldRefresh={isConnected:true};
  const main={dataset:{renderedRoute:wrongRender?'source/another':'source/source-id'},
    querySelector:()=>['pending','partial','stopped'].includes(state)?{textContent:state==='stopped'?'Автообновление остановлено. Нажмите «Обновить» или вернитесь позже; оригинал сохранён.':'Подготовка обновляется автоматически. Можно вернуться позже.'}:null,
    querySelectorAll:()=>['prepared','partial'].includes(state)?[{getAttribute:()=>href}]:[]};
  const replace=()=>{oldRefresh.isConnected=false;state=next;generation++;events.push(['rerender',state]);};
  const locator=selector=>({selector,first(){return this;},
    async count(){return selector==='#main [data-preparation-refresh]'?Number(Boolean(main.querySelector())):
      selector==='[data-action=refresh]'?1:main.querySelectorAll().length;},
    async waitFor(options){assert.equal(options.timeout,20000);assert.equal(state,'prepared');events.push(['visible-target',generation]);}});
  const page={locator,waitForFunction:async(predicate,arg,options)=>{
    assert.equal(options.timeout,20000);events.push(['wait-render',arg.allowStopped]);
    if(!predicate(arg)){await Promise.resolve();replace();}
    if(!predicate(arg))throw Error('Exact terminal source target did not render');
  }};
  const click=async(target,label)=>{
    if(target.selector==='[data-action=refresh]'){
      assert.ok(mode==='baseline'||state==='stopped','Refresh is allowed only when automatic reads visibly stopped');
      events.push(['click-refresh',label]);
      if(mode==='baseline')replace();else state='pending';
    }else{
      assert.equal(target.selector,`#main a[href="${href}"]`);assert.equal(state,'prepared');
      events.push(['click-target',label,generation]);
    }
  };
  const ready=async prefix=>{assert.equal(prefix,'review/');events.push(['review-ready']);};
  try{
    globalThis.document={querySelector:selector=>selector==='#main'?main:null};
    globalThis.location={hash:wrongRoute?'#source/another':'#source/source-id'};
    const helper=new Function('page','click','ready','mode',`${snippet};return {preparedLink,openPrepared};`)(page,click,ready,mode);
    if(open)await helper.openPrepared(source);else await helper.preparedLink(source,'proposal-id');
    return {events,oldRefresh};
  }finally{globalThis.document=previous.document;globalThis.location=previous.location;}
}

test('API-complete source waits for asynchronous terminal rerender before one original measured click',async()=>{
  const {events,oldRefresh}=await run();
  assert.equal(oldRefresh.isConnected,false,'The former Refresh node really is replaced');
  assert.deepEqual(events,[['wait-render',true],['rerender','prepared'],['visible-target',2],
    ['click-target','Open prepared action',2],['review-ready']]);
});

test('a proposal link in a still-pending source is not accepted as a terminal render',async()=>{
  const {events}=await run({initial:'partial'});
  assert.equal(events[1][0],'rerender');assert.equal(events.filter(event=>event[0]==='click-target').length,1);
  assert.equal(events.some(event=>event[0]==='click-refresh'),false);
});

for(const options of [{next:'terminal-without-target'},{wrongRoute:true},{wrongRender:true}]){
  test(`missing exact source/terminal target fails: ${JSON.stringify(options)}`,async()=>{
    await assert.rejects(run(options),/Exact terminal source target did not render/);
  });
}

test('visible exhausted auto-refresh retains one explicit measured refresh and waits for its terminal result',async()=>{
  const {events}=await run({initial:'stopped'});
  assert.deepEqual(events.slice(0,4),[['wait-render',true],['click-refresh','Refresh actual prepared source'],['wait-render',false],['rerender','prepared']]);
  assert.equal(events.filter(event=>event[0]==='click-refresh').length,1);
  assert.equal(events.filter(event=>event[0]==='click-target').length,1);
});

test('an explicit refresh that never renders the prepared source still fails',async()=>{
  await assert.rejects(run({initial:'stopped',next:'pending'}),/Exact terminal source target did not render/);
});

test('historical baseline retains its measured manual refresh path',async()=>{
  const {events}=await run({mode:'baseline'});
  assert.equal(events.some(event=>event[0]==='wait-render'),false);
  assert.equal(events.filter(event=>event[0]==='click-refresh').length,1);
  assert.equal(events.filter(event=>event[0]==='click-target').length,1);
});

test('P01 uses shared readiness and preserves exact OCR/inertness assertions and trusted action proof',()=>{
  const p01=harness.slice(harness.indexOf("await run('P01'"),harness.indexOf("await run('P02'"));
  assert.match(p01,/const link=await preparedLink\(source,p\.id\);await click\(link,'Inspect known standalone invoice facts'\)/);
  assert.doesNotMatch(p01,/link\.count\(\)|Refresh prepared standalone/);
  for(const assertion of ["source.recognition.provider,'local'","source.recognition.engine_version,'local-tesseract-tur-eng-v1'",
    "['INVOICE_MATCH','PAYABLE']","p.revision.payload.amount,'500'","p.revision.payload.counterparty_id,setup.supplier_id",
    "p.revision.payload.event_date,'2026-10-12'","p.revision.payload.invoice_intent,'undecided'",
    'assert.deepEqual(c.no_effect_snapshot,c.before_capture)','await original(c.source_id,bytes)'])assert.ok(p01.includes(assertion),assertion);
  assert.match(harness,/events\.some\(x=>x\.trusted\)/);
  assert.match(harness,/await visibleAction\(locator,label\)/);
  assert.doesNotMatch(snippet,/force\s*:|waitForTimeout|setTimeout|\.catch\(|\.evaluate\(/);
});

test('correction branch runs this regression and both existing browser/SQL engines',()=>{
  const workflow=readFileSync(new URL('../../.github/workflows/capture-daily-checkpoint.yml',import.meta.url),'utf8');
  assert.match(workflow,/branches: \[[^\n]*16-day-workers-postmerge-capture-stability\]/);
  assert.match(workflow,/engine: \[chromium, webkit\]/);
  assert.match(workflow,/run: node --test tests\/frontend\/capture-prepared-source-readiness\.test\.mjs/);
  assert.match(workflow,/node tests\/e2e\/capture-daily-owner-journeys\.mjs\n\s+python scripts\/capture_daily_owner_sql_evidence\.py --verify/);
});
