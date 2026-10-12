import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {historySelection,historyRoute,historyQuery,purchaseHistoryView,purchaseDetailView,purchaseOriginal,stockEventView,invoiceTargetMatches} from '../../prototype/owner-r3/purchases-connected.js';
import {ownerFormSource} from '../../prototype/owner-r3/connected-adapter.js';
const original={source:'owner_form:v1:inventory_receipt',locator:'source1',structured_original:{form_id:'source1',fields:{quantity:'120,00',unit:'kg',notes:''}},raw_text:null,has_file:false,sha256:'a'.repeat(64)};
const event={id:'r',kind:'receipt',event_date:'2026-10-10',status:'posted',version:2,lines:[{original_product_name:'KNO3',quantity:'120',unit:'kg',location_name:'Склад A',unit_cost:null,currency:null}],movements:[],original,invoice_links:[],pending_invoice_actions:[],linked_documents:[]};
const page={available_products:[],available_locations:[],available_suppliers:[],items:[event],events:[event],stock:[],total:22,next_offset:20};
test('purchase history preserves all filters and page without silently resetting scope',()=>{
 const s=historySelection('purchases/2026-10-01/2026-10-10/p/l/s/all/20');assert.equal(s.product_id,'p');assert.equal(s.location_id,'l');assert.equal(s.supplier_id,'s');assert.equal(s.offset,20);assert.equal(historyRoute(s),'purchases/2026-10-01/2026-10-10/p/l/s/all/20');assert.match(historyQuery(s),/date_from=2026-10-01/);assert.match(historyQuery(s),/offset=20/);
});
test('stock history kind is explicit and does not turn periods into opening stock',()=>{
 const s=historySelection('stock-history/all/all/p/l/all/transfer/0');assert.match(historyQuery(s),/kind=transfer/);const html=purchaseHistoryView(page,s);assert.match(html,/не начальный остаток периода/);assert.match(html,/Начальная позиция неизвестна/);
});
test('purchase shows separate invoice intent and no invented debt from physical receipt',()=>{
 const html=purchaseDetailView(event);assert.match(html,/Добавить счёт к приходу/);assert.match(html,/Связанного финансового документа нет/);assert.match(html,/не создаёт второй приход/);assert.match(html,/Неизвестно/);
});
test('whole financial document is not receipt valuation and points to allocation history',()=>{
 const d={obligation_id:'debt',original_amount:'500',allocated_amount:'200',remaining_amount:'300',currency:'TRY',source:{description:'Счёт'}};
 const html=purchaseDetailView({...event,linked_documents:[d]});assert.match(html,/500 TRY/);assert.match(html,/200 TRY/);assert.match(html,/300 TRY/);assert.match(html,/не является стоимостью этой строки/);assert.match(html,/#money-document\/debt/);
});
test('durable source pending before preparation remains discoverable by receipt',()=>{
 const html=purchaseDetailView({...event,pending_invoice_actions:[{source_id:'s',filename:'invoice.pdf',invoice_intent:'existing_payable',job_state:'QUEUED',proposals:[]}]});assert.match(html,/#invoice-source\/r\/s/);assert.match(html,/Связь с существующим долгом/);assert.match(html,/Источник сохранён/);
});
test('legacy mismatch and archived nonfinancial link remain explicit',()=>{
 const html=purchaseDetailView({...event,invoice_links:[{original_source_id:'s',created_at:'2026-10-10',supplier_consistency:'LEGACY_MISMATCH',finance_obligation_id:null}]});assert.match(html,/Историческое несоответствие/);assert.match(html,/Архивная связь без финансового документа/);
});
test('structured original preserves exact lexical values and escapes owner text',()=>{
 const html=purchaseOriginal({...original,structured_original:{form_id:'s',fields:{quantity:'120,00',notes:'<img src=x onerror=alert(1)>'}}});assert.match(html,/120,00/);assert.match(html,/&lt;img/);assert.doesNotMatch(html,/<img/);
});
test('transfer original is bounded exact field snapshot and retains source identity',()=>{
 const d={id:'s',captured_at:'2026-10-10T20:00:00Z'},f={event_date:'2026-10-10',product_id:'p',original_product_name:' KNO3 ',location_id:'a',destination_id:'b',quantity:'020,00',unit:'kg',notes:'',payment:'200'};
 const o=ownerFormSource('inventory_transfer',d,f);assert.equal(o.fields.quantity,'020,00');assert.equal(o.fields.destination_id,'b');assert.equal(o.fields.original_product_name,' KNO3 ');assert.equal(o.form_id,'s');assert.equal('payment' in o.fields,false);assert.deepEqual(o,ownerFormSource('inventory_transfer',structuredClone(d),f));
});
test('transfer history shows paired movements and unknown cost without an expense',()=>{
 const html=stockEventView({...event,kind:'transfer',movements:[{seq:1,product_name:'KNO3',quantity:'-20',base_unit:'kg',location_name:'A',uncosted_quantity:'-20',known_cost:'0',currency:null,event_date:'2026-10-10'},{seq:2,product_name:'KNO3',quantity:'20',base_unit:'kg',location_name:'B',uncosted_quantity:'20',known_cost:'0',currency:null,event_date:'2026-10-10'}]});assert.match(html,/-20 kg/);assert.match(html,/20 kg/);assert.match(html,/Без известной стоимости/);assert.match(html,/Перемещение/);
});
test('invoice UI uses immutable source context and separate versioned confirmations',()=>{
 const s=fs.readFileSync('prototype/owner-r3/connected.js','utf8');assert.match(s,/capture_context:'supplier_invoice',invoice_receipt_id:current.id/);assert.match(s,/invoice-confirm-payable/);assert.match(s,/invoice-confirm-link/);assert.match(s,/preview_digest:proposal.revision.preview_digest/);assert.match(s,/confirmed_effect\?\.effect\?\.id/);assert.match(s,/Источник не связан с этим приходом/);assert.match(s,/draft.choose==='yes'/);
});

test('current explicit invoice target cannot be hidden by an older source context',()=>{
 const source={invoice_receipt_id:'A'};assert.equal(invoiceTargetMatches('A',source,null),true);
 const match={revision:{payload:{receipt_id:'B'}}};assert.equal(invoiceTargetMatches('A',source,match),false);assert.equal(invoiceTargetMatches('B',source,match),true);
});

test('non-receipt history never offers a misleading arrival action',()=>{
 for(const kind of ['application','writeoff','plan']){const html=stockEventView({...event,kind});assert.doesNotMatch(html,/#receipt\/r/);assert.doesNotMatch(html,/Подтвердить.*приход/);}
});

test('purchase keeps original context but opens the current explicit invoice target',()=>{
 const html=purchaseDetailView({...event,pending_invoice_actions:[{source_id:'s',invoice_receipt_id:'r',invoice_intent:'existing_payable',proposals:[{action_type:'INVOICE_MATCH',state:'READY',current_receipt_id:'other'}]}]});assert.match(html,/текущей ревизии выбран другой приход/);assert.match(html,/#invoice-source\/other\/s/);
});

test('legacy direct financial supplier mismatch is disclosed without rewriting history',()=>{
 const html=purchaseDetailView({...event,supplier_id:'A',finance_obligation_id:'o',linked_documents:[{obligation_id:'o',counterparty_id:'B',source:{description:'Исторический счёт'},original_amount:'500',allocated_amount:'0',remaining_amount:'500',currency:'TRY'}]});assert.match(html,/Историческая прямая финансовая ссылка указывает другого поставщика/);assert.match(html,/500 TRY/);
});
