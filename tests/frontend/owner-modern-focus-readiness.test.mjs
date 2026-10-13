import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

const harness=readFileSync(new URL('../e2e/owner-modern-ui.mjs',import.meta.url),'utf8');
const app=readFileSync(new URL('../../src/greenhouse_owner_os/web/app.js',import.meta.url),'utf8');
const assertionEnd=harness.indexOf("'Dismissed editor restores focus to its Review surface');")+"'Dismissed editor restores focus to its Review surface');".length;
const start=harness.lastIndexOf("  await page.keyboard.press('Escape');",assertionEnd);
const snippet=harness.slice(start,assertionEnd);
const closeHandler=app.match(/sheet\.addEventListener\('close',(\(\)=>\{[^\n]+\})\);/)[1];

// Evaluate the actual harness boundary with the actual existing app close
// handler. This models event ordering; it is not a local browser verification.
async function run({openerConnected=false, restore=true}={}){
  const originalDocument=globalThis.document,events=[];
  const body={classList:{remove:value=>events.push(['remove-class',value])}},otherNav={};
  const document={activeElement:body,body,querySelector:selector=>selector==='#app'?surface:selector==='#nav [aria-current=page]'?review:null};
  const opener={isConnected:openerConnected,focus(){events.push(['app-focus','opener']);document.activeElement=this;}};
  const review={focus(){events.push(['app-focus','review']);document.activeElement=this;}};
  const surface={contains:node=>node===opener&&openerConnected};
  const close=new Function('$','document','sheetReturnFocus',`let sheetRevision=0;return ${closeHandler};`)(selector=>selector==='#nav .active'?review:null,document,opener);
  const sheet={open:true,waitFor:async options=>{assert.equal(options.state,'hidden');assert.equal(sheet.open,false);events.push(['hidden']);}};
  const page={keyboard:{press:async key=>{assert.equal(key,'Escape');sheet.open=false;events.push(['escape']);}},
    waitForFunction:async(predicate,arg,options)=>{
      events.push(['wait-focus']);assert.equal(arg,undefined);assert.equal(options.timeout,5000);
      assert.equal(predicate(),false,'Hidden dialog alone does not prove focus restoration');
      document.activeElement=otherNav;assert.equal(predicate(),false,'Unrelated navigation focus is insufficient');document.activeElement=body;
      if(restore)close();
      if(!predicate())throw Error('Timed out waiting for actual restored focus');
    },evaluate:async predicate=>{events.push(['assert-focus']);return predicate();}};
  try{globalThis.document=document;await new Function('page','sheet','assert',`return(async()=>{${snippet}})();`)(page,()=>sheet,assert);return events;}
  finally{globalThis.document=originalDocument;}
}

for(const openerConnected of [false,true])test(`Escape waits for genuine close-handler focus restoration (connected opener=${openerConnected})`,async()=>{
  const events=await run({openerConnected});
  assert.deepEqual(events.slice(0,3),[['escape'],['hidden'],['wait-focus']]);
  assert.deepEqual(events.filter(e=>e[0]==='app-focus'),[['app-focus',openerConnected?'opener':'review']]);
  assert.deepEqual(events.at(-1),['assert-focus']);
});

test('missing restoration still fails and harness never forces focus or sleeps',async()=>{
  await assert.rejects(run({restore:false}),/Timed out waiting for actual restored focus/);
  assert.doesNotMatch(snippet,/\.focus\(|waitForTimeout|force\s*:/);
  assert.match(snippet,/assert\.equal\(await page\.evaluate\(/);
});
