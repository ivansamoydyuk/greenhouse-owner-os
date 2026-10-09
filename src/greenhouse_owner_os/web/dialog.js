// Keep keyboard navigation within an open native modal after its content changes.
// Native dialog still owns modal/inert semantics and Escape dismissal.
export function dialogControls(dialog){
  return [...dialog.querySelectorAll('button,input,select,textarea,a[href],summary,[tabindex]')].filter(node=>{
    if(node.disabled||node.matches(':disabled')||node.tabIndex<0||!node.getClientRects().length)return false;
    for(let ancestor=node;ancestor&&ancestor!==dialog;ancestor=ancestor.parentElement){
      const style=getComputedStyle(ancestor);
      if(ancestor.hidden||ancestor.inert||style.display==='none'||style.visibility==='hidden')return false;
      if(ancestor.tagName==='DETAILS'&&!ancestor.open){
        const summary=[...ancestor.children].find(child=>child.tagName==='SUMMARY');
        if(!summary?.contains(node))return false;
      }
    }
    return true;
  });
}
export function containDialogTab(dialog,event){
  if(!dialog.open||event.key!=='Tab'||event.altKey||event.ctrlKey||event.metaKey)return;
  const controls=dialogControls(dialog);if(!controls.length)return;
  const first=controls[0],last=controls.at(-1),active=dialog.ownerDocument.activeElement;
  if(event.shiftKey?(active===first||!dialog.contains(active)):(active===last||!dialog.contains(active))){
    event.preventDefault();(event.shiftKey?last:first).focus();
  }
}
