import test from 'node:test';
import assert from 'node:assert/strict';
import {inboxStates,inboxUI} from '../../src/greenhouse_owner_os/web/inbox.js';
test('Inbox renders source status and escapes untrusted original/filename',()=>{
 const ui=inboxUI({getData:()=>({inbox:{items:[{id:'test',summary:'<img src=x onerror=evil()>',source:'<script>sender</script>',received_at:'2026-10-05',state:'NEEDS_REVIEW',duplicate_of_id:'x'}],next_offset:null}})});
 const html=ui.render();assert.ok(html.includes('&lt;img'));assert.ok(html.includes('&lt;script'));assert.ok(!html.includes('<img'));assert.ok(html.includes('Возможный дубль'));
});
test('Inbox finalized and conflict states remain explicit to the owner',()=>{
 assert.equal(inboxStates.CONFIRMED,'Черновик подтверждён');assert.equal(inboxStates.CONFLICT,'Источник изменился');assert.equal(inboxStates.REJECTED,'Отклонён');
});
