/** Isolated, authenticated, synthetic R3 prototype. No backend imports or database. */
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {createHash, randomBytes, timingSafeEqual} from 'node:crypto';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {dirname, extname, join, resolve, sep} from 'node:path';
import {readFile, writeFile, readdir, mkdir, copyFile, lstat, realpath} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';

const SOURCE_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');
const TYPES = {'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.svg':'image/svg+xml','.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.webp':'image/webp','.woff2':'font/woff2'};
const REQUIRED = ['index.html','app.js','styles.css','components.js','demo-adapter.js','fixtures.js'];
const same = (a,b) => typeof a === 'string' && typeof b === 'string' && Buffer.byteLength(a) === Buffer.byteLength(b) && timingSafeEqual(Buffer.from(a),Buffer.from(b));

async function inventory(root, prefix = '') {
  const found = [];
  for (const item of await readdir(join(root,prefix), {withFileTypes:true})) {
    const name = prefix ? `${prefix}/${item.name}` : item.name;
    if (item.isSymbolicLink()) throw Error(`Symlinks are forbidden in the R3 build: ${name}`);
    if (item.isDirectory()) found.push(...await inventory(root,name));
    else {
      assert.ok(item.isFile() && TYPES[extname(name)], `Unsupported static artifact: ${name}`);
      const bytes = await readFile(join(root,name));
      found.push({path:name,bytes:bytes.length,sha256:sha256(bytes)});
    }
  }
  return found.sort((a,b)=>a.path.localeCompare(b.path));
}

export async function buildPrototype({root=SOURCE_ROOT,out=join(SOURCE_ROOT,'artifacts/owner-r3-build'),sha=process.env.OWNER_PREVIEW_FEATURE_SHA}={}) {
  const head = execFileSync('git',['rev-parse','HEAD'],{cwd:root,encoding:'utf8'}).trim();
  if (sha) assert.equal(head,sha,'Build must use the exact requested checkout SHA');
  sha ||= head;
  assert.match(sha,/^[a-f0-9]{40}$/);
  const source = join(root,'prototype/owner-r3');
  const files = await inventory(source);
  for (const name of REQUIRED) assert.ok(files.some(f=>f.path===name),`Missing prototype file ${name}`);
  await mkdir(join(out,'static'),{recursive:true});
  for (const file of files) {
    await mkdir(dirname(join(out,'static',file.path)),{recursive:true});
    await copyFile(join(source,file.path),join(out,'static',file.path));
  }
  const manifest = {schema:1,feature_sha:sha,mode:'R3_SYNTHETIC_PROTOTYPE',backend_connected:false,files};
  await writeFile(join(out,'manifest.json'),JSON.stringify(manifest,null,2)+'\n');
  await copyFile(fileURLToPath(import.meta.url),join(out,'server.mjs'));
  await writeFile(join(out,'README.txt'),`Owner UI R3 static prototype\nExact source SHA: ${sha}\nSynthetic data only. No backend connection.\n\nRestore without changing the build:\nPREVIEW_SECRET=<new random 32-byte key> PREVIEW_PUBLIC_ORIGIN=<approved HTTPS test origin> node server.mjs serve\nA fresh launch lasts at most 35 minutes. Access and expiry are not embedded in this build.\nFor local browser tests use R3_LOCAL_TEST=1; the listener always binds 127.0.0.1.\n`);
  return manifest;
}

export async function verifyBuild(buildDir) {
  const manifest = JSON.parse(await readFile(join(buildDir,'manifest.json'),'utf8'));
  assert.equal(manifest.mode,'R3_SYNTHETIC_PROTOTYPE');
  assert.equal(manifest.backend_connected,false);
  assert.match(manifest.feature_sha,/^[a-f0-9]{40}$/);
  assert.deepEqual(await inventory(join(buildDir,'static')),manifest.files,'Static build file or content differs from manifest');
  return manifest;
}

function loginPage(nonce) {
  return `<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Greenhouse · TEST prototype</title><style nonce="${nonce}">*{box-sizing:border-box}body{margin:0;background:#f6f7f3;color:#172b24;font:16px system-ui;display:grid;min-height:100dvh;place-items:center;padding:24px}main{width:min(100%,380px)}h1{font-size:30px}p{line-height:1.5;color:#627169}label{display:block;margin:24px 0 8px}input,button{font:inherit;width:100%;min-height:48px;border-radius:12px;padding:12px}input{border:1px solid #a1b3a9}button{margin-top:12px;background:#123f32;color:white;border:0}input:focus-visible,button:focus-visible{outline:3px solid #799f86;outline-offset:3px}#error{color:#9e332d}</style><main><p>GREENHOUSE · OWNER UI R3</p><h1>Закрытый TEST-прототип</h1><p>ДЕМО — данные не проводятся. Ключ доступа находится в отдельном приватном файле.</p><form id="login"><label for="secret">Ключ доступа</label><input id="secret" type="password" autocomplete="off" required><button>Открыть прототип</button><p id="error" role="alert"></p></form></main><script nonce="${nonce}">document.querySelector('#login').addEventListener('submit',async(event)=>{event.preventDefault();const button=document.querySelector('button');button.disabled=true;try{const response=await fetch('/preview/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({secret:document.querySelector('#secret').value})});if(response.ok)location.reload();else document.querySelector('#error').textContent=response.status===410?'Время доступа закончилось.':'Не удалось открыть. Проверьте ключ или попробуйте позже.';}catch{document.querySelector('#error').textContent='Нет соединения. Попробуйте ещё раз.';}finally{button.disabled=false;}});</script></html>`;
}

export async function createPreview({buildDir=process.env.R3_BUILD_DIR || dirname(fileURLToPath(import.meta.url)),secret=process.env.PREVIEW_SECRET,localTest=process.env.R3_LOCAL_TEST==='1',publicOrigin=process.env.PREVIEW_PUBLIC_ORIGIN,port=Number(process.env.PREVIEW_PORT||4173),expiresAt=process.env.PREVIEW_EXPIRES_AT,expectedSha=process.env.OWNER_PREVIEW_FEATURE_SHA,now=()=>Date.now()}={}) {
  assert.ok(typeof secret==='string' && /^[A-Za-z0-9_-]{32,}$/.test(secret),'A fresh random PREVIEW_SECRET of at least 32 URL-safe characters is required');
  assert.ok(Number.isInteger(port)&&port>=0&&port<65536);
  if (localTest) assert.ok(!publicOrigin,'Local-test mode cannot expose a public preview origin');
  else {
    const url = new URL(publicOrigin);
    assert.ok(url.protocol==='https:' && !url.username && !url.password && url.pathname==='/' && !url.search && !url.hash && url.origin===publicOrigin,'An exact HTTPS public origin is required');
  }
  const manifest=await verifyBuild(buildDir);
  if(expectedSha) assert.equal(manifest.feature_sha,expectedSha);
  const started=now(),expires=expiresAt?Date.parse(expiresAt):started+35*60*1000;
  assert.ok(Number.isFinite(expires)&&expires>started&&expires<=started+35*60*1000,'Preview expiry must be within the next 35 minutes');
  const staticRoot=await realpath(join(buildDir,'static'));
  const files=new Map(manifest.files.map(f=>['/'+f.path,f]));
  const session=randomBytes(48).toString('base64url');
  const cookieName=localTest?'r3_local_test':'__Host-r3_preview';
  const attempts=new Map();
  let origin=publicOrigin;
  const server=createServer(async(req,res)=>{
    const nonce=randomBytes(18).toString('base64');
    const headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer','X-Frame-Options':'DENY','Permissions-Policy':'camera=(self), microphone=(self), geolocation=()','Content-Security-Policy':`default-src 'self'; script-src 'self' 'nonce-${nonce}'; style-src 'self' 'nonce-${nonce}'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'`};
    const reply=(status,body,type='application/json; charset=utf-8',extra={})=>{res.writeHead(status,{...headers,'Content-Type':type,...extra});res.end(req.method==='HEAD'?'':typeof body==='string'||Buffer.isBuffer(body)?body:JSON.stringify(body));};
    try {
      if(now()>=expires) return reply(410,{error:'preview_expired',expires_at:new Date(expires).toISOString()});
      const url=new URL(req.url,'http://127.0.0.1');
      if(url.pathname==='/health'&&['GET','HEAD'].includes(req.method))return reply(200,{ok:true,mode:manifest.mode});
      if(req.method==='POST') {
        if(req.headers.origin!==origin || req.headers['sec-fetch-site']==='cross-site')return reply(403,{error:'same_origin_required'});
        if(url.pathname==='/preview/login') {
          const address=req.socket.remoteAddress, attempt=attempts.get(address)||{count:0,until:now()+60000};
          if(now()>attempt.until){attempt.count=0;attempt.until=now()+60000;}
          if(attempt.count>=8)return reply(429,{error:'try_later'},undefined, {'Retry-After':'60'});
          let body='';
          for await(const chunk of req){body+=chunk;if(Buffer.byteLength(body)>4096)return reply(413,{error:'request_too_large'});}
          let candidate;try{candidate=JSON.parse(body).secret;}catch{return reply(400,{error:'invalid_request'});}
          if(!same(candidate,secret)){attempt.count++;attempts.set(address,attempt);return reply(401,{error:'access_denied'});}
          attempts.delete(address);
          return reply(200,{ok:true},undefined,{'Set-Cookie':`${cookieName}=${session}; Path=/; HttpOnly; SameSite=Strict; Max-Age=${Math.max(1,Math.floor((expires-now())/1000))}${localTest?'':'; Secure'}`});
        }
        if(url.pathname==='/preview/logout')return reply(200,{ok:true},undefined,{'Set-Cookie':`${cookieName}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0${localTest?'':'; Secure'}`});
        return reply(404,{error:'no_business_api_in_this_prototype'});
      }
      if(!['GET','HEAD'].includes(req.method))return reply(405,{error:'method_not_allowed'},undefined,{'Allow':'GET, HEAD'});
      const cookies=String(req.headers.cookie||'').split(';').map(v=>v.trim().split('='));
      const authenticated=cookies.some(([key,value])=>key===cookieName&&same(value,session));
      if(!authenticated){if(url.pathname==='/')return reply(200,loginPage(nonce),'text/html; charset=utf-8');return reply(401,{error:'preview_login_required'});}
      if(url.pathname==='/preview/config')return reply(200,{feature_sha:manifest.feature_sha,mode:manifest.mode,backend_connected:false,expires_at:new Date(expires).toISOString()});
      const pathname=url.pathname==='/'?'/index.html':url.pathname;
      if(!files.has(pathname))return reply(404,{error:'not_found'});
      const file=await realpath(join(staticRoot,pathname));
      if(!file.startsWith(staticRoot+sep)||(await lstat(file)).isSymbolicLink())return reply(404,{error:'not_found'});
      const bytes=await readFile(file);
      if(sha256(bytes)!==files.get(pathname).sha256)return reply(503,{error:'build_integrity_failed'});
      return reply(200,bytes,TYPES[extname(file)]);
    } catch {if(!res.headersSent)reply(400,{error:'invalid_request'});else res.end();}
  });
  await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(port,'127.0.0.1',resolve);});
  origin ||= `http://127.0.0.1:${server.address().port}`;
  return {server,origin,manifest,expiresAt:new Date(expires).toISOString(),close:()=>new Promise((resolve,reject)=>server.close(error=>error?reject(error):resolve()))};
}

export async function selfTest(buildDir) {
  const secret=randomBytes(32).toString('base64url');
  let time=Date.now();
  const preview=await createPreview({buildDir,secret,port:0,localTest:false,publicOrigin:'https://r3-test.example',now:()=>time});
  const base=`http://127.0.0.1:${preview.server.address().port}`;
  const request=(path,options={})=>fetch(base+path,options);
  const login=(key=secret,origin='https://r3-test.example')=>request('/preview/login',{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify({secret:key})});
  try {
    assert.equal((await request('/health')).status,200);
    assert.equal((await request('/preview/config')).status,401);
    assert.equal((await request('/app.js')).status,401);
    assert.equal((await login(secret,'https://unapproved.example')).status,403);
    assert.equal((await request('/preview/login',{method:'POST',body:'{}'})).status,403);
    const response=await login();assert.equal(response.status,200);
    const setCookie=response.headers.get('set-cookie');
    for(const flag of ['__Host-r3_preview=','HttpOnly','SameSite=Strict','Secure','Path=/'])assert.ok(setCookie.includes(flag));
    assert.ok(!setCookie.includes(secret),'Session credential must differ from the access key');
    const headers={Cookie:setCookie.split(';')[0]};
    const config=await (await request('/preview/config',{headers})).json();
    assert.equal(config.feature_sha,preview.manifest.feature_sha);
    assert.equal(config.backend_connected,false);
    assert.equal(Date.parse(config.expires_at)-time,35*60*1000);
    assert.equal((await request('/app.js',{headers})).status,200);
    for(const path of ['/v1/entities','/config/owner_finance_opening.json','/../package.json','/%2e%2e/package.json','/manifest.json','/server.mjs'])assert.equal((await request(path,{headers})).status,404,path);
    assert.equal((await request('/v1/events',{method:'POST',headers:{...headers,Origin:'https://r3-test.example'},body:'{}'})).status,404);
    for(let n=0;n<8;n++)assert.equal((await login('incorrect')).status,401);
    assert.equal((await login('incorrect')).status,429);
    time+=35*60*1000;
    assert.equal((await request('/health')).status,410);
    assert.equal((await request('/app.js',{headers})).status,410);
    console.log('R3 preview security PASS: exact SHA, authenticated assets, origin checks, Secure/HttpOnly/Strict cookie, no business API, rate limit, absolute 35-minute expiry');
  } finally {await preview.close();}
}

if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href){
  const command=process.argv[2]||'serve';
  if(command==='build') {
    const manifest=await buildPrototype({out:process.argv[3]?resolve(process.argv[3]):undefined});
    console.log(`R3 static build: ${manifest.feature_sha}; ${manifest.files.length} verified files`);
  }else if(command==='verify') {
    const manifest=await verifyBuild(resolve(process.argv[3]||'artifacts/owner-r3-build'));
    console.log(`R3 build integrity PASS: ${manifest.feature_sha}`);
  }else if(command==='self-test') {
    await selfTest(resolve(process.argv[3]||'artifacts/owner-r3-build'));
  }else if(command==='serve') {
    const preview=await createPreview();
    console.log(`R3 synthetic prototype listening on 127.0.0.1:${preview.server.address().port}; SHA ${preview.manifest.feature_sha}; expires ${preview.expiresAt}`);
    const timer=setTimeout(()=>preview.close().then(()=>process.exit(0)),Math.max(0,Date.parse(preview.expiresAt)-Date.now()));
    for(const signal of ['SIGINT','SIGTERM'])process.once(signal,()=>{clearTimeout(timer);preview.close().then(()=>process.exit(0));});
  }else throw Error('Use build, verify, or serve');
}
