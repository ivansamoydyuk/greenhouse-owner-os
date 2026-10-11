// Fertilizer form autosaves use the existing scoped IndexedDB store. A queued
// snapshot is not durable until its transaction completes; it is never a source.
export class FertilizerDraftWrites {
  constructor(api, changed=()=>{}) { this.api=api;this.changed=changed;this.records=new Map(); }
  key(route,scope=this.api.scope,generation=this.api.generation){return JSON.stringify([scope,generation,route]);}
  state(route){return this.records.get(this.key(route))?.state||'idle';}
  unsaved(route){return ['saving','error'].includes(this.state(route));}
  enqueue(route,value){
    const scope=this.api.scope,generation=this.api.generation,key=this.key(route),snapshot=structuredClone(value);
    let record=this.records.get(key);if(!record){record={tail:Promise.resolve(),sequence:0};this.records.set(key,record);}
    const sequence=++record.sequence;record.state='saving';record.error=null;this.changed(route,record.state);
    const guard=()=>{if(scope!==this.api.scope||generation!==this.api.generation)throw Error('Сессия изменилась. Черновик прежнего владельца не перенесён.');};
    const pending=record.tail.catch(()=>{}).then(async()=>{guard();await this.api.draft(route,snapshot);guard();});
    record.tail=pending.then(()=>{if(sequence===record.sequence){record.state='saved';record.error=null;if(scope===this.api.scope&&generation===this.api.generation)this.changed(route,'saved');}},error=>{if(sequence===record.sequence){record.state='error';record.error=error;if(scope===this.api.scope&&generation===this.api.generation)this.changed(route,'error',error);}throw error;});
    // The caller can await a write; autosave errors also remain observable by flush.
    record.tail.catch(()=>{});return record.tail;
  }
  async flush(route){let record=this.records.get(this.key(route));while(record){const tail=record.tail;await tail;if(tail===record.tail)return;record=this.records.get(this.key(route));}}
  protectUnload(route,event){if(!this.unsaved(route))return false;event.preventDefault();event.returnValue='';return true;}
}
