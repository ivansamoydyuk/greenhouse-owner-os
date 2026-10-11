// Owner form autosaves use the existing scoped store. Keep one active write and
// only the newest waiting snapshot: obsolete edits must not delay durability.
// A queued snapshot is not durable until its transaction completes.
export class FertilizerDraftWrites {
  constructor(api, changed=()=>{}) { this.api=api;this.changed=changed;this.records=new Map(); }
  key(route,scope=this.api.scope,generation=this.api.generation){return JSON.stringify([scope,generation,route]);}
  state(route){return this.records.get(this.key(route))?.state||'idle';}
  unsaved(route){return ['saving','error'].includes(this.state(route));}
  enqueue(route,value){
    const scope=this.api.scope,generation=this.api.generation,key=this.key(route),snapshot=structuredClone(value);
    let record=this.records.get(key);
    if(!record){record={scope,generation,route,tail:Promise.resolve(),sequence:0,waiters:[],running:false};this.records.set(key,record);}
    const sequence=++record.sequence;record.pending={sequence,snapshot};record.state='saving';record.error=null;this.changed(route,'saving');
    const promise=new Promise((resolve,reject)=>record.waiters.push({sequence,resolve,reject}));
    // Superseded callers await the newer covering write; none acknowledge an
    // edit merely because its obsolete snapshot was removed from the queue.
    promise.catch(()=>{});
    if(!record.running)this.drain(record);
    return promise;
  }
  drain(record){
    record.running=true;
    const current=()=>record.scope===this.api.scope&&record.generation===this.api.generation;
    const guard=()=>{if(!current())throw Error('Сессия изменилась. Черновик прежнего владельца не перенесён.');};
    record.tail=(async()=>{
      while(record.pending){
        const {sequence,snapshot}=record.pending;record.pending=null;
        let failure=null;
        try{guard();await this.api.draft(record.route,snapshot);guard();}catch(error){failure=error;}
        const covered=record.waiters.filter(waiter=>waiter.sequence<=sequence);
        record.waiters=record.waiters.filter(waiter=>waiter.sequence>sequence);
        if(sequence===record.sequence){
          record.state=failure?'error':'saved';record.error=failure;
          if(current())this.changed(record.route,record.state,failure);
        }
        for(const waiter of covered)failure?waiter.reject(failure):waiter.resolve();
      }
    })().finally(()=>{
      record.running=false;
      // An edit can arrive after the loop finishes but before this microtask.
      if(record.pending)this.drain(record);
    });
    record.tail.catch(()=>{});
  }
  async wait(record){for(;;){const tail=record.tail;await tail;if(tail===record.tail){if(record.state==='error')throw record.error;return;}}}
  async flush(route){const record=this.records.get(this.key(route));if(record)await this.wait(record);}
  async settle(){await Promise.allSettled([...this.records.values()].map(record=>this.wait(record)));}
  protectUnload(route,event){if(!this.unsaved(route))return false;event.preventDefault();event.returnValue='';return true;}
}
