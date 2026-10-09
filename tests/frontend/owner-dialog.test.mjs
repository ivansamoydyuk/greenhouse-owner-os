import test from 'node:test';
import assert from 'node:assert/strict';
import {containDialogTab,dialogControls} from '../../src/greenhouse_owner_os/web/dialog.js';
function fixture(){
 const before=globalThis.getComputedStyle;globalThis.getComputedStyle=node=>node.style||{display:'block',visibility:'visible'};
 const doc={activeElement:null},dialog={open:true,ownerDocument:doc,nodes:[],querySelectorAll(){return this.nodes;},contains(node){return this.nodes.includes(node);}};
 function node(extra={}){const n={tagName:'BUTTON',disabled:false,tabIndex:0,parentElement:dialog,children:[],getClientRects:()=>[{}],matches:()=>false,contains(other){return other===this;},focus(){doc.activeElement=this;},...extra};dialog.nodes.push(n);return n;}
 const event=(extra={})=>({key:'Tab',prevented:false,preventDefault(){this.prevented=true;},...extra});
 return {dialog,doc,node,event,cleanup(){globalThis.getComputedStyle=before;}};
}
test('native modal tab wraps at current first and last, preserving normal interior navigation',()=>{
 const h=fixture();try{const first=h.node(),middle=h.node(),last=h.node();h.doc.activeElement=last;let ev=h.event();containDialogTab(h.dialog,ev);assert.equal(ev.prevented,true);assert.equal(h.doc.activeElement,first);ev=h.event({shiftKey:true});containDialogTab(h.dialog,ev);assert.equal(h.doc.activeElement,last);h.doc.activeElement=middle;ev=h.event();containDialogTab(h.dialog,ev);assert.equal(ev.prevented,false);}finally{h.cleanup();}
});
test('closed details, disabled and invisible controls do not become tab-wrap targets',()=>{
 const h=fixture();try{const first=h.node(),details={tagName:'DETAILS',open:false,parentElement:h.dialog,children:[]},summary=h.node({tagName:'SUMMARY',parentElement:details});details.children=[summary];h.node({tagName:'INPUT',parentElement:details});h.node({disabled:true});h.node({style:{display:'none'}});h.node({tabIndex:-1});assert.deepEqual(dialogControls(h.dialog),[first,summary]);h.doc.activeElement=summary;containDialogTab(h.dialog,h.event());assert.equal(h.doc.activeElement,first);}finally{h.cleanup();}
});
test('tab trap uses newly replaced content and ignores closed modal or browser shortcuts',()=>{
 const h=fixture();try{h.node();h.dialog.nodes=[];const replacement=h.node();h.doc.activeElement={};containDialogTab(h.dialog,h.event());assert.equal(h.doc.activeElement,replacement);for(const opts of [{key:'Escape'},{ctrlKey:true},{altKey:true},{metaKey:true}]){const ev=h.event(opts);containDialogTab(h.dialog,ev);assert.equal(ev.prevented,false);}h.dialog.open=false;const ev=h.event();containDialogTab(h.dialog,ev);assert.equal(ev.prevented,false);}finally{h.cleanup();}
});

test('unresolved queue primary action honestly asks to clarify without authorizing an effect',async()=>{
 const {proposalCard}=await import('../../src/greenhouse_owner_os/web/owner-r2.js');
 const html=proposalCard({id:'unknown',action_type:'UNRESOLVED',state:'NEEDS_INPUT',revision:{payload:{note:'Нужно разобраться'},missing:['Событие не определено'],basis:{}},source:{text:'Нужно разобраться'}});
 assert.match(html,/data-r2-open="unknown"[^>]*>Уточнить<\/button>/);
 assert.doesNotMatch(html,/id="r2-confirm"/);
});
