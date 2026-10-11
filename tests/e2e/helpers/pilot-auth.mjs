import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
export const pilotMode=Boolean(process.env.PILOT_E2E_CREDENTIALS);
const secrets=[];
export const redactPilot=value=>secrets.reduce((text,secret)=>text.replaceAll(secret,'[private TEST credential redacted]'),String(value));
export async function pilotCredentials(){const value=JSON.parse(await fs.readFile(process.env.PILOT_E2E_CREDENTIALS,'utf8'));const remember=x=>{if(x&&typeof x==='object'){if(x.password)secrets.push(x.password);Object.values(x).forEach(remember);}};remember(value);return value;}
export async function fillPilotLogin(page,credentials){secrets.push(credentials.password);await page.locator('#username').fill(credentials.username);await page.locator('#password').fill(credentials.password);await page.getByRole('button',{name:'Войти',exact:true}).click();}
export async function ownerLogin(page,base,{navigate=true}={}){
 if(pilotMode){
  if(navigate)await page.goto(base+'/pilot/');
  await fillPilotLogin(page,(await pilotCredentials()).owner);
 }else{
  if(navigate)await page.goto(base+'/r3/');
  await page.locator('#secret').fill(process.env.PREVIEW_SECRET);
  await page.getByRole('button',{name:'Войти',exact:true}).click();
 }
 // Return fragments contain slashes. Match the destination, then let callers
 // assert their own route/bootstrap readiness, including deliberately held reads.
 const origin=new URL(base).origin;
 await page.waitForURL(url=>url.origin===origin&&url.pathname==='/r3/connected.html',{waitUntil:'domcontentloaded'});
}
export async function provisionWorker(workerId){if(!process.env.PILOT_PROVISION_SCRIPT)throw Error('Missing offline TEST worker provision script');const dir=await fs.mkdtemp(path.join(os.tmpdir(),'pilot-worker-'));const output=path.join(dir,'credentials.json');try{await promisify(execFile)(process.env.PYTHON||'python',[process.env.PILOT_PROVISION_SCRIPT,'--worker-id',workerId,'--output',output],{env:process.env});const credentials=JSON.parse(await fs.readFile(output,'utf8'));secrets.push(credentials.password);return credentials;}finally{await fs.rm(dir,{recursive:true,force:true});}}
export async function workerLogin(page,base,credentials){await page.goto(base+'/pilot/');await fillPilotLogin(page,credentials);await page.waitForURL('**/worker');await page.locator('#worker-app h1').waitFor();}
export const ownerDraftScope=config=>`${config.entity_id}:${config.owner_id}`+(config.daily_pilot?`:${config.participant_id}`:'');
export async function expiredCookie(participantId){const dir=await fs.mkdtemp(path.join(os.tmpdir(),'pilot-expiry-')),output=path.join(dir,'cookie.json');try{await promisify(execFile)(process.env.PYTHON||'python',[process.env.PILOT_PROVISION_SCRIPT,'--expired-cookie-participant',participantId,'--output',output],{env:process.env});const cookie=JSON.parse(await fs.readFile(output,'utf8'));secrets.push(cookie.value);return cookie;}finally{await fs.rm(dir,{recursive:true,force:true});}}
export async function resetFixtureRateLimits(){if(!pilotMode)return;await promisify(execFile)(process.env.PYTHON||'python',[process.env.PILOT_PROVISION_SCRIPT,'--reset-fixture-rate-limits'],{env:process.env});}
