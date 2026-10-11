/** TEST browser persistence. Keys never persist; AES-GCM binds ciphertext to account and record. */
const encoder=new TextEncoder(),decoder=new TextDecoder();
const hex=bytes=>[...new Uint8Array(bytes)].map(x=>x.toString(16).padStart(2,'0')).join('');
const encode=bytes=>{const data=new Uint8Array(bytes);let value='';for(let offset=0;offset<data.length;offset+=8192)value+=String.fromCharCode(...data.subarray(offset,offset+8192));return btoa(value);};
const decode=text=>Uint8Array.from(atob(text.replace(/-/g,'+').replace(/_/g,'/')),c=>c.charCodeAt(0));
export class PilotCipher {
 constructor(participant,key){if(!participant||!key)throw Error('Не подтверждён ключ локального черновика.');this.participant=participant;this.ready=crypto.subtle.importKey('raw',decode(key),{name:'AES-GCM'},false,['encrypt','decrypt']);this.prefix=crypto.subtle.digest('SHA-256',encoder.encode(participant)).then(hex);}
 async address(logical){return 'pilot-v1:'+await this.prefix+':'+hex(await crypto.subtle.digest('SHA-256',encoder.encode(logical)));}
 async seal(logical,value){const address=await this.address(logical),iv=crypto.getRandomValues(new Uint8Array(12));const payload=encoder.encode(JSON.stringify({logical,value}));const cipher=await crypto.subtle.encrypt({name:'AES-GCM',iv,additionalData:encoder.encode(this.participant+'|'+address)},await this.ready,payload);return {key:address,iv:encode(iv),ciphertext:encode(cipher),version:1};}
 async open(record){try{const plain=await crypto.subtle.decrypt({name:'AES-GCM',iv:decode(record.iv),additionalData:encoder.encode(this.participant+'|'+record.key)},await this.ready,decode(record.ciphertext));const parsed=JSON.parse(decoder.decode(plain));if(await this.address(parsed.logical)!==record.key)throw Error('binding');return parsed;}catch{throw Error('Не удалось открыть локальную копию этой учётной записи. Исходная копия сохранена.');}}
}
export class EncryptedRecordStore {
 constructor(raw,participant,key){this.raw=raw;this.cipher=new PilotCipher(participant,key);this.clean=null;}
 async guard(){await (this.clean||(this.clean=this.raw.all().then(rows=>assertNoLegacyDrafts(rows,typeof window==='undefined'?[]:[window.localStorage,window.sessionStorage]))));}
 async envelope(record){await this.guard();return {...await this.cipher.seal(record.key,record),state:record.state||null};}
 async put(record){await this.raw.put(await this.envelope(record));}
 async get(key){await this.guard();const row=await this.raw.get(await this.cipher.address(key));return row?(await this.cipher.open(row)).value:undefined;}
 async delete(key){await this.guard();await this.raw.delete(await this.cipher.address(key));}
 async all(){await this.guard();const prefix='pilot-v1:'+await this.cipher.prefix+':';const values=[];for(const row of await this.raw.all())if(row.key.startsWith(prefix))values.push((await this.cipher.open(row)).value);return values;}
 async claim(record){const row=await this.raw.claim(await this.envelope(record));return (await this.cipher.open(row)).value;}
}
export class EncryptedWorkerDraftStore {
 constructor(storage,participant,key,changed=()=>{}){this.storage=storage;this.scope=participant;this.cipher=new PilotCipher(participant,key);this.changed=changed;this.memory=new Map();this.dirty=new Map();this.tail=Promise.resolve();this.failure=null;this.sequence=0;}
 static async create(storage,participant,key,changed){await assertPilotOriginClean();assertNoLegacyDrafts([],[storage]);const store=new EncryptedWorkerDraftStore(storage,participant,key,changed);const prefix='pilot-v1:'+await store.cipher.prefix+':';try{for(let i=0;i<storage.length;i++){const name=storage.key(i);if(!name?.startsWith(prefix))continue;const row=JSON.parse(storage.getItem(name)),decoded=await store.cipher.open(row);store.memory.set(decoded.logical,decoded.value);}}catch(error){throw Error('Не удалось открыть сохранённые черновики. '+error.message);}return store;}
 get failed(){return this.dirty.size>0||Boolean(this.failure);}
 getItem(key){return this.memory.get(key)??null;}
 setItem(key,value){this.memory.set(key,String(value));this.dirty.set(key,{value:String(value),sequence:++this.sequence});return this.schedule();}
 removeItem(key){this.memory.delete(key);this.dirty.set(key,{deleted:true,sequence:++this.sequence});return this.schedule();}
 schedule(){this.changed('saving');this.tail=this.tail.catch(()=>{}).then(async()=>{for(const [key,snapshot] of [...this.dirty]){try{if(snapshot.deleted)this.storage.removeItem(await this.cipher.address(key));else{const row=await this.cipher.seal(key,snapshot.value);this.storage.setItem(row.key,JSON.stringify(row));}if(this.dirty.get(key)?.sequence===snapshot.sequence)this.dirty.delete(key);}catch(error){this.failure=error;this.changed('error');throw Error('Черновик не сохранён на устройстве. Оставьте страницу открытой и повторите.');}}this.failure=null;this.changed(this.dirty.size?'saving':'saved');});this.tail.catch(()=>{});return this.tail;}
 async flush(){if(this.failure)this.schedule();for(;;){const tail=this.tail;await tail;if(tail===this.tail&&!this.dirty.size)return;}}
 protectUnload(event){if(!this.failed)return;event.preventDefault();event.returnValue='';}
}
export function assertNoLegacyDrafts(records=[],storages=[]){
 const legacyRecords=records.some(record=>!String(record.key||'').startsWith('pilot-v1:'));
 let legacyStorage=false;
 for(const storage of storages.filter(Boolean))for(let i=0;i<storage.length;i++){const key=storage.key(i)||'';if(/^(worker-write:|worker-preview-token|private-worker-draft:)/.test(key)||/^\["worker-(r2|fertilizer)/.test(key))legacyStorage=true;}
 if(legacyRecords||legacyStorage)throw Error('На этом адресе остались незашифрованные черновики прежнего TEST. Они сохранены без изменений. Для закрытого пилота нужен отдельный чистый профиль браузера или выделенный TEST-адрес.');
}
export async function assertPilotOriginClean(){
 const storages=typeof window==='undefined'?[]:[window.localStorage,window.sessionStorage];
 let records=[];
 if(typeof indexedDB!=='undefined'){
  const db=await new Promise((resolve,reject)=>{const request=indexedDB.open('r3-connected-test-v1',1);request.onupgradeneeded=()=>request.result.createObjectStore('records',{keyPath:'key'});request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);});
  try{records=await new Promise((resolve,reject)=>{const request=db.transaction('records','readonly').objectStore('records').getAll();request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);});}finally{db.close();}
 }
 assertNoLegacyDrafts(records,storages);
}
