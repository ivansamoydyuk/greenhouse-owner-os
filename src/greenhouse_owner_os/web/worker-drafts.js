/** Browser-only drafts, isolated by the authenticated participant. No credentials. */
export class WorkerDraftStore {
 constructor(storage, scope, changed=()=>{}){if(!scope)throw Error('Не подтверждена учётная запись.');this.storage=storage;this.scope=scope;this.changed=changed;this.memory=new Map();this.dirty=new Set();this.readFailed=false;}
 get failed(){return this.readFailed||this.dirty.size>0;}
 key(key){return 'private-worker-draft:'+JSON.stringify([this.scope,key]);}
 getItem(key){if(this.memory.has(key))return this.memory.get(key);try{return this.storage.getItem(this.key(key));}catch{this.readFailed=true;this.changed('error');throw Error('Память устройства недоступна. Не закрывайте введённый отчёт.');}}
 setItem(key,value){this.memory.set(key,String(value));this.dirty.add(key);try{this.storage.setItem(this.key(key),String(value));this.dirty.delete(key);this.changed(this.failed?'error':'saved');}catch{this.changed('error');throw Error('Черновик не сохранён на устройстве. Не закрывайте страницу; повторите сохранение.');}}
 removeItem(key){try{this.storage.removeItem(this.key(key));this.memory.delete(key);this.dirty.delete(key);this.changed(this.failed?'error':'saved');}catch{this.readFailed=true;this.changed('error');throw Error('Не удалось обновить локальную копию. Проверьте результат на сервере.');}}
 flush(){for(const key of [...this.dirty])this.setItem(key,this.memory.get(key));if(this.readFailed){try{this.storage.getItem(this.key('__availability__'));this.readFailed=false;}catch{throw Error('Память устройства недоступна. Оставьте страницу открытой.');}}this.changed(this.failed?'error':'saved');}
 protectUnload(event){if(!this.failed)return;event.preventDefault();event.returnValue='';}
}
export function workerDraftIdentity(worker,session=null){
 if(!worker?.id)throw Error('Не подтверждена личность работника.');
 if(!session)return worker; // Legacy worker DTO intentionally omits entity_id.
 if(session.role!=='worker'||session.worker_id!==worker.id||!session.entity_id||!session.participant_id)throw Error('Учётная запись не совпадает с работником.');
 return {...worker,entity_id:session.entity_id};
}
export function workerDraftScope(identity,participant){if(!identity?.id||(participant&&!identity.entity_id))throw Error('Не подтверждена принадлежность работника.');return JSON.stringify([identity.entity_id||'legacy-worker',identity.id,participant||identity.id]);}
export function r1DraftKey(task,assignment){return JSON.stringify(['worker-r1-form',task.entity_id,task.id,assignment.id,task.cycle]);}
export function stableWorkerIntent(storage,key,body){const saved=storage.getItem(key);if(saved){storage.setItem(key,saved);return JSON.parse(saved);}storage.setItem(key,JSON.stringify(body));return body;}
