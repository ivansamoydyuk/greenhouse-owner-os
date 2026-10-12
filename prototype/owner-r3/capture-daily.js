// Read-only preparation refreshes never own an edit or an authority decision.
export class PreparationRefreshWindow {
  constructor({durationMs=120000,maxReads=48,now=()=>Date.now()}={}) {
    Object.assign(this,{durationMs,maxReads,now});this.window=null;
  }
  enter(identity) {
    if(!this.window || !this.matches(identity))this.window={...identity,deadline:this.now()+this.durationMs,reads:0};
  }
  matches(identity) {
    return !!this.window && ['route','scope','generation'].every(k=>this.window[k]===identity[k]);
  }
  available(identity) {
    return this.matches(identity) && this.now()<this.window.deadline && this.window.reads<this.maxReads;
  }
  claim(identity) {
    if(!this.available(identity))return false;
    this.window.reads++;return true;
  }
  reset() {this.window=null;}
}
export const preparationPending=source=>['QUEUED','RUNNING'].includes(source?.job?.state || source?.state);
export function depotEditorFields(proposal) {
  const f=proposal.revision.payload;
  const fields={source_id:proposal.source.id,proposal_id:proposal.id,line_count:String(f.lines?.length || 1),date:f.event_date || '',depot:f.depot_id || '',currency:f.currency || '',link_evidence:f.link_evidence || '',stated_total:f.stated_total ?? '',alacak_value:f.alacak_value ?? '',due_date:f.due_date || '',document_number:f.document_number || '',document_count:f.document_count ?? '',caption:proposal.source.text || ''};
  (f.lines || []).forEach((l,i)=>{const prefix=i?`line_${i}_`:'';for(const [key,value] of Object.entries({quality:l.original_quality_name,quality_category:f.qualities?.[i],kg:l.net_weight_kg,price:l.unit_price,stated_amount:l.stated_amount,count:l.count,source_line:l.source_line,confirmed_amount:l.confirmed_amount!=null&&l.confirmed_amount===l.stated_amount?'stated':'calculated'}))fields[prefix+key]=value??'';});
  return fields;
}
export function mergeDepotEditor(saved,proposal) {
  const fresh=depotEditorFields(proposal);
  if(saved?.editor_base)for(const [key,value] of Object.entries(saved)) {
    if(Object.hasOwn(saved.editor_base,key) && value!==saved.editor_base[key])fresh[key]=value;
  }
  else if(saved?.proposal_id)Object.assign(fresh,saved);
  return {...saved,...fresh,editor_base:depotEditorFields(proposal),proposal_version:proposal.version};
}

// OCR candidates and a single corrected row do not authorize every quality.
// Keep an earlier explicit review only while its depot/label/category basis is identical.
export function preservesDepotQualityReview(previous,next) {
  if(!previous.quality_reviewed || !next.lines?.length || next.depot_id!==previous.depot_id || next.qualities?.length!==next.lines.length || !next.qualities.every(Boolean))return false;
  const basis=value=>(value.lines||[]).map((line,i)=>[line.original_quality_name,value.qualities?.[i]??null]);
  return JSON.stringify(basis(previous))===JSON.stringify(basis(next));
}
