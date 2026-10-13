// Read-only evidence for the existing held-read logout journey. Never collect
// query strings, fragments, credentials, headers, response bodies or draft data.
const allowedPaths=new Set(['/r3/connected.html','/pilot/','/pilot/logout','/pilot/session','/worker']);
const pathname = value => {try{const path=new URL(value).pathname;return allowedPaths.has(path)?path:'other';}catch{return 'unavailable';}};
function locationFlags(value){try{const url=new URL(value);return{pathname:pathname(value),query_present:!!url.search,fragment_present:!!url.hash};}catch{return{pathname:'unavailable',query_present:null,fragment_present:null};}}
async function bounded(read, timeoutMs) {
  let timer;
  try{return await Promise.race([Promise.resolve().then(read).then(value=>({state:'ok',value}),()=>({state:'error'})),new Promise(resolve=>{timer=setTimeout(()=>resolve({state:'timeout'}),timeoutMs);})]);}
  finally{clearTimeout(timer);}
}
export function observeOwnerLogout(page, context, base, {timeoutMs=1500,maxEvents=40}={}) {
  const events=[],started=Date.now();let logoutStatus=null,completion=null;
  const add=(event,fields={})=>{if(events.length<maxEvents)events.push({event,elapsed_ms:Date.now()-started,...fields});};
  const navigation=frame=>{if(frame===page.mainFrame())add('main-frame-navigation',locationFlags(frame.url()));};
  const domReady=()=>add('domcontentloaded',locationFlags(page.url()));
  const load=()=>add('load',locationFlags(page.url()));
  const closed=()=>add('page-closed');
  const failed=request=>{try{if(request.isNavigationRequest()&&request.frame()===page.mainFrame())add('navigation-request-failed',{pathname:pathname(request.url())});}catch{add('navigation-request-check-unavailable');}};
  const response=reply=>{
    if(reply.request().method()!=='POST'||pathname(reply.url())!=='/pilot/logout')return;
    logoutStatus=reply.status();add('logout-response',{status:logoutStatus});
    completion=Promise.resolve().then(()=>reply.finished()).then(error=>{const completed=!error;add('logout-response-finished',{completed});return completed;},()=>{add('logout-response-finished',{completed:false});return false;});
  };
  const listeners={framenavigated:navigation,domcontentloaded:domReady,load,close:closed,requestfailed:failed,response};
  for(const [event,handler] of Object.entries(listeners))page.on(event,handler);
  add('observe-start',locationFlags(page.url()));
  return {
    async capture(outcome){
      const pageClosed=page.isClosed(),[document,session,logoutCompletion]=await Promise.all([
        pageClosed?{state:'closed'}:bounded(()=>page.evaluate(()=>({ready_state:document.readyState,login_dom:!!document.querySelector('#pilot-login'),owner_dom:!!document.querySelector('[data-testid=nav-more]'),owner_hidden:document.querySelector('#app')?getComputedStyle(document.querySelector('#app')).visibility==='hidden':null})),timeoutMs),
        bounded(async()=>({status:(await context.request.get(new URL('/pilot/session',base).href,{timeout:timeoutMs})).status()}),timeoutMs),
        completion?bounded(()=>completion,timeoutMs):{state:'not-observed'},
      ]);
      return {outcome,page_closed:pageClosed,...locationFlags(page.url()),document,session,logout_status:logoutStatus,logout_completion:logoutCompletion,events:[...events]};
    },
    stop(){for(const [event,handler] of Object.entries(listeners))page.off(event,handler);},
  };
}
