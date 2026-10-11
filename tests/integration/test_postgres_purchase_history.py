"""Separate arrival/invoice/payment and exact physical transfer on real PostgreSQL."""
import base64
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from decimal import Decimal
from uuid import UUID,uuid4
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from greenhouse_owner_os.domain.owner_capture import CaptureSource
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyUnitOfWork
from greenhouse_owner_os.services.owner_capture import OwnerCaptureService
from tests.integration.test_postgres_readiness import preview,send
from tests.integration.test_postgres_inventory import inv,event,post,line,view,qty
from tests.integration.test_postgres_owner_capture import queue,revise,confirm


def read(i,path,**params):
    r=i['c'].get(f'/v1/entities/{i["e"]}'+path,params=params)
    assert r.status_code==200,r.text
    return r.json()


def invoice_body(receipt,intent='operating_expense',**kw):
    return dict(client_capture_id=str(uuid4()),captured_at='2026-10-06T12:00:00+03:00',capture_context='supplier_invoice',
        invoice_intent=intent,invoice_receipt_id=receipt['id'],mode='file',mime_type='application/pdf',filename='TEST-unreadable-invoice.pdf',
        file_base64=base64.b64encode(b'%PDF-1.4\nTEST original unreadable invoice\n%%EOF').decode(),**kw)


def capture(i,receipt,intent='operating_expense'):
    source=send(i['c'],f'/entities/{i["e"]}/owner/captures',invoice_body(receipt,intent))
    result=read(i,'/owner/captures/'+source['id']);ps=[read(i,'/owner/confirmations/'+p['id']+'/preview') for p in result['proposals']]
    return result,{p['action_type']:p for p in ps}


def payable(i,total='500',party=None):
    o=send(i['c'],'/obligations',dict(entity_id=i['e'],counterparty_id=party or i['cp'],direction='payable',original_amount=total,currency='TRY',event_date='2026-10-06',offset_ledger_account_id=i['ids']['GENERAL_EXPENSE']))
    return send(i['c'],f'/obligations/{o["id"]}/post?expected_version=1',expected=200)


def finances(i):
    return read(i,'/balances')['balances']['TRY']


def test_context_saved_before_preparation_and_retried_identity_no_hidden_payable(inv,monkeypatch):
    i=inv;r=post(i,event(i));sf,a,_,_,_=i['preview'];svc=OwnerCaptureService(lambda:SqlAlchemyUnitOfWork(sf))
    b=CaptureSource.model_validate(invoice_body(r,'existing_payable'))
    saved=svc.capture(UUID(i['e']),a,'initial',b)
    detail=read(i,'/purchases/'+r['id']);pending=detail['pending_invoice_actions'][0]
    assert pending['source_id']==saved['id'] and pending['job_state']=='QUEUED' and pending['proposals']==[]
    assert pending['invoice_receipt_id']==r['id'] and pending['invoice_intent']=='existing_payable'
    assert svc.capture(UUID(i['e']),a,'another-request',b)['id']==saved['id']
    from greenhouse_owner_os.services.ocr import LocalOCR
    monkeypatch.setattr(LocalOCR,'recognize',lambda *args:(_ for _ in ()).throw(AssertionError('Manual invoice must not depend on OCR')))
    svc.process(UUID(saved['id']));svc.process(UUID(saved['id']))
    readback=read(i,'/owner/captures/'+saved['id']);assert [x['action_type'] for x in readback['proposals']]==['INVOICE_MATCH']
    assert readback['invoice_receipt_id']==r['id'] and readback['invoice_intent']=='existing_payable'
    wrong=invoice_body(r,'operating_expense');wrong['client_capture_id']=str(b.client_capture_id)
    send(i['c'],f'/entities/{i["e"]}/owner/captures',wrong,expected=409)
    assert finances(i)['payable']=='0.00' and qty(i)==100


def test_late_invoice_three_separate_confirmations_unknown_cost_and_payment_history(inv,monkeypatch):
    i=inv;receipt=post(i,event(i));before=finances(i)
    from greenhouse_owner_os.services.ocr import LocalOCR
    monkeypatch.setattr(LocalOCR,'recognize',lambda *args:(_ for _ in ()).throw(AssertionError('OCR unavailable')))
    source,ps=capture(i,receipt)
    assert set(ps)=={'PAYABLE','INVOICE_MATCH'} and finances(i)==before and qty(i)==100
    match=ps['INVOICE_MATCH'];assert match['revision']['payload']['receipt_id']==receipt['id']
    assert match['revision']['provenance']['receipt_id']['category']=='OWNER CONTEXT'
    assert 'Подтверждённый долг' in ' '.join(match['revision']['missing'])
    p=revise(i,ps['PAYABLE'],dict(amount='500',currency='TRY',event_date='2026-10-06',counterparty_id=i['cp'],description='TEST owner-selected operating materials expense'))
    result=confirm(i,p);assert qty(i)==100 and finances(i)['payable']=='500.00' and finances(i)['cash']=='0.00'
    recovered=read(i,'/owner/confirmations/'+p['id']+'/preview')['confirmed_effect']
    assert recovered==result and recovered['effect']['id']==result['effect']['id']
    match=revise(i,match,dict(receipt_id=receipt['id'],finance_obligation_id=result['effect']['id'],note='TEST separately confirmed invoice link'))
    linked=confirm(i,match);assert confirm(i,match,key='lost-link-response')==linked
    assert qty(i)==100 and finances(i)['payable']=='500.00'
    payment=send(i['c'],'/payments',dict(entity_id=i['e'],counterparty_id=i['cp'],money_account_id=i['cash'],direction='out',amount='200',currency='TRY',event_date='2026-10-06',allocations=[dict(obligation_id=result['effect']['id'],amount='200')]))
    send(i['c'],f'/payments/{payment["id"]}/post?expected_version=1',expected=200)
    detail=read(i,'/purchases/'+receipt['id'])
    assert len(detail['invoice_links'])==1 and detail['invoice_links'][0]['valuation_state']=='UNKNOWN_UNCHANGED'
    doc=detail['linked_documents'][0];assert doc['remaining_amount']=='300.00' and doc['history'][0]['amount']=='200.00'
    assert detail['lines'][0]['unit_cost'] is None and Decimal(view(i)['stock'][0]['uncosted_quantity'])==100
    original=i['c'].get(f'/v1/entities/{i["e"]}/owner/captures/{source["id"]}/original')
    assert hashlib.sha256(original.content).hexdigest()==hashlib.sha256(b'%PDF-1.4\nTEST original unreadable invoice\n%%EOF').hexdigest()
    analytics=read(i,'/finance/analytics',date_from='2026-10-04',date_to='2026-10-31')
    assert analytics['economics']['operating_cost']=='500.00' and qty(i)==100


def test_existing_payable_context_confirms_only_link_and_deduplicates_documents(inv):
    i=inv;first=post(i,event(i));second=post(i,event(i,qty='50'));o=payable(i)
    for receipt in (first,second):
        s,ps=capture(i,receipt,'existing_payable');assert set(ps)=={'INVOICE_MATCH'}
        p=revise(i,ps['INVOICE_MATCH'],dict(receipt_id=receipt['id'],finance_obligation_id=o['id']))
        confirm(i,p)
    result=read(i,'/purchases')
    assert len(result['items'])==2 and len(result['linked_documents'])==1
    assert result['linked_documents'][0]['original_amount']=='500.00' and result['document_scope']=='CURRENT_PAGE_LINKED_WHOLE_DOCUMENTS'
    assert finances(i)['payable']=='500.00' and qty(i)==150
    with i['preview'][0]() as db:
        assert db.scalar(text("SELECT count(*) FROM core.proposed_actions WHERE entity_id=:e AND action_type='PAYABLE'"),dict(e=i['e']))==0


def test_wrong_supplier_service_and_sql_reject_link_and_direct_receipt(inv):
    i=inv;receipt=post(i,event(i,supplier_id=i['cp']));other=send(i['c'],f'/entities/{i["e"]}/counterparties',dict(name='TEST other supplier',role='supplier',source='test:synthetic'))['id'];o=payable(i,party=other)
    s,ps=capture(i,receipt,'existing_payable');p=ps['INVOICE_MATCH']
    send(i['c'],f'/entities/{i["e"]}/owner/confirmations/{p["id"]}/revise',dict(expected_version=p['version'],payload=dict(receipt_id=receipt['id'],finance_obligation_id=o['id'])),expected=422)
    with pytest.raises(DBAPIError) as exc:
        with i['preview'][0].begin() as db:
            db.execute(text('INSERT INTO core.invoice_receipt_links(id,entity_id,inbox_id,proposal_id,receipt_id,finance_obligation_id,reviewed_by) VALUES(:id,:e,:s,:p,:r,:o,:a)'),dict(id=uuid4(),e=i['e'],s=s['id'],p=p['id'],r=receipt['id'],o=o['id'],a=i['preview'][1]))
    assert exc.value.orig.sqlstate=='23514' and 'Invoice payable or supplier mismatch' in str(exc.value)
    send(i['c'],i['root']+'/events',dict(kind='receipt',event_date='2026-10-06',supplier_id=i['cp'],finance_obligation_id=o['id'],lines=[line(i)]),expected=422)
    assert qty(i)==100 and read(i,'/purchases/'+receipt['id'])['invoice_links']==[]


def test_reversed_receipt_revalidates_link_but_original_and_retry_survive(inv):
    i=inv;receipt=post(i,event(i));body=invoice_body(receipt,'existing_payable');saved=send(i['c'],f'/entities/{i["e"]}/owner/captures',body)
    source=read(i,'/owner/captures/'+saved['id']);p=read(i,'/owner/confirmations/'+source['proposals'][0]['id']+'/preview');o=payable(i)
    p=revise(i,p,dict(receipt_id=receipt['id'],finance_obligation_id=o['id']))
    send(i['c'],i['root']+f'/events/{receipt["id"]}/reverse',dict(event_date='2026-10-06',reason='TEST correction'),expected=200)
    confirm(i,p,expected=422)
    assert send(i['c'],f'/entities/{i["e"]}/owner/captures',body)['id']==saved['id']
    detail=read(i,'/purchases/'+receipt['id']);assert detail['status']=='reversed' and detail['pending_invoice_actions'][0]['source_id']==saved['id']
    assert finances(i)['payable']=='500.00' and qty(i)==0


def transfer_form(i,quantity='20,00',**changes):
    ident=str(uuid4());original=dict(schema_version=1,form_kind='inventory_transfer',form_id=ident,captured_at='2026-10-06T12:00:00+03:00',timezone='Europe/Istanbul',
        fields=dict(event_date='2026-10-06',product_id=i['p']['id'],original_product_name='KNO3',location_id=i['l']['id'],destination_id=i['b']['id'],quantity=quantity,unit='kg',notes='TEST exact transfer note'))
    result=dict(kind='transfer',event_date='2026-10-06',lines=[dict(product_id=i['p']['id'],original_product_name='KNO3',quantity=quantity.replace(',','.'),unit='kg',location_id=i['l']['id'],destination_id=i['b']['id'])],
        evidence=dict(source='owner_form:v1:inventory_transfer',locator=ident,structured_original=original))
    result.update(changes);return result


def test_typed_transfer_exactly_once_original_reload_and_no_consumption_or_finance(inv):
    i=inv;post(i,event(i));before=finances(i);body=transfer_form(i)
    r=send(i['c'],i['root']+'/events',body);original=r['evidence']['structured_original']
    again=send(i['c'],i['root']+'/events',body);assert again['id']==r['id']
    posted=post(i,r);assert post(i,r,key='lost-response')==posted
    assert qty(i,i['l']['id'])==80 and qty(i,i['b']['id'])==20 and qty(i)==100 and finances(i)==before
    history=read(i,'/inventory/history/'+r['id']);assert history['original']['structured_original']==original
    assert history['original']['raw_text'] is None and history['original']['hash_basis']=='CANONICAL_TYPED_FORM_JSON'
    assert history['lines'][0]['destination_id']==i['b']['id'] and len(history['movements'])==2
    assert {m['role'] for m in history['movements']}=={'transfer_in','transfer_out'}
    assert sum(Decimal(m['quantity']) for m in history['movements'])==0
    assert sum(Decimal(m['known_cost']) for m in history['movements'])==0
    assert sum(Decimal(m['uncosted_quantity']) for m in history['movements'])==0
    changed=json.loads(json.dumps(body));changed['lines'][0]['destination_id']=i['l']['id']
    send(i['c'],i['root']+'/events',changed,expected=422)
    changed=json.loads(json.dumps(body));changed['evidence']['structured_original']['fields']['notes']='changed original'
    send(i['c'],i['root']+'/events',changed,expected=409)
    filtered=read(i,'/inventory/history',kind='transfer',location_id=i['b']['id'])
    assert filtered['total']==1 and filtered['events'][0]['id']==r['id']


def test_transfer_reversal_and_replacement_preserve_original_and_guard_negative(inv):
    i=inv;post(i,event(i,qty='30'));body=transfer_form(i);r=post(i,send(i['c'],i['root']+'/events',body))
    too_much=transfer_form(i,'11');draft=send(i['c'],i['root']+'/events',too_much);post(i,draft,422)
    send(i['c'],i['root']+f'/events/{r["id"]}/reverse',dict(event_date='2026-10-07',reason='TEST transfer correction'),expected=200)
    correction=dict(body,replacement_of_id=r['id']);correction['lines']=[dict(body['lines'][0],quantity='10')]
    replacement=post(i,send(i['c'],i['root']+'/events',correction))
    history=read(i,'/inventory/history/'+r['id']);assert history['replacement_id']==replacement['id']
    assert {m['event_date'] for m in history['movements'] if m['role']=='reversal'}=={'2026-10-07'}
    assert read(i,'/inventory/history/'+replacement['id'])['original']==history['original']
    assert qty(i,i['l']['id'])==20 and qty(i,i['b']['id'])==10


def test_paginated_history_not_recent_hundred_and_foreign_filters_rejected(inv):
    i=inv
    # Distinct immutable drafts are sufficient to exercise full source history;
    # they intentionally create no physical or financial effects.
    for n in range(103):event(i,qty=str(n+1),notes='TEST distinct history '+str(n))
    pages=[read(i,'/purchases',offset=offset,limit=40) for offset in (0,40,80,120)]
    assert [len(p['items']) for p in pages]==[40,40,23,0]
    assert all(p['total']==103 for p in pages)
    ids=[r['id'] for p in pages for r in p['items']];assert len(set(ids))==103
    assert read(i,'/inventory/history',kind='receipt')['total']==103
    for params in (dict(product_id=str(uuid4())),dict(location_id=str(uuid4())),dict(supplier_id=str(uuid4())),dict(date_from='2026-10-31',date_to='2026-10-01')):
        assert i['c'].get(f'/v1/entities/{i["e"]}/purchases',params=params).status_code==422
    assert i['c'].get(f'/v1/entities/{i["e"]}/purchases/{uuid4()}').status_code==404
    assert qty(i)==0 and finances(i)['payable']=='0.00'


def test_concurrent_invoice_confirmation_one_link_and_source_original_immutable(inv):
    i=inv;r=post(i,event(i));o=payable(i);s,ps=capture(i,r,'existing_payable');p=revise(i,ps['INVOICE_MATCH'],dict(receipt_id=r['id'],finance_obligation_id=o['id']))
    sf,a,*_=i['preview'];svc=OwnerCaptureService(lambda:SqlAlchemyUnitOfWork(sf))
    from greenhouse_owner_os.domain.owner_capture import ConfirmAction
    b=ConfirmAction(expected_version=p['version'],preview_digest=p['revision']['preview_digest'],confirmed=True)
    with ThreadPoolExecutor(2) as pool:
        outcomes=list(pool.map(lambda n:svc.confirm(UUID(i['e']),a,UUID(p['id']),'concurrent-'+str(n),b),range(2)))
    assert outcomes[0]==outcomes[1] and len(read(i,'/purchases/'+r['id'])['invoice_links'])==1
    with pytest.raises(DBAPIError):
        with sf.begin() as db:db.execute(text("UPDATE core.inbox_items SET metadata=metadata||'{\"invoice_intent\":\"operating_expense\"}' WHERE id=:s"),dict(s=s['id']))
    assert read(i,'/owner/captures/'+s['id'])['invoice_intent']=='existing_payable'


@pytest.mark.parametrize('first',['link','reversal'])
def test_invoice_sql_link_and_finance_reversal_serialize_both_orders(inv,first):
    """The raw-SQL guard must wait for the same row as trusted Finance reversal."""
    from threading import Event
    from time import monotonic,sleep
    from greenhouse_owner_os.services.finance import FinanceService
    from greenhouse_owner_os.domain.commands import ReverseCommand
    i=inv;r=post(i,event(i));o=payable(i);source,ps=capture(i,r,'existing_payable')
    sf,actor,*_=i['preview'];held=Event();release=Event();pids={};link_id=uuid4()
    class ReversalUow(SqlAlchemyUnitOfWork):
        def __enter__(self):
            super().__enter__();pids['reversal']=self.session.scalar(text('SELECT pg_backend_pid()'));return self
        def commit(self):
            if first=='reversal':
                held.set();assert release.wait(10),'test release timed out'
            super().commit()
    service=FinanceService(lambda:ReversalUow(sf))
    def reverse():
        return service.reverse_obligation(actor_id=actor,idempotency_key='race-reverse',obligation_id=UUID(o['id']),
            command=ReverseCommand(expected_version=o['version'],event_date='2026-10-07',reason='TEST concurrent correction'))
    def raw_link():
        with sf.begin() as db:
            pids['link']=db.scalar(text('SELECT pg_backend_pid()'))
            db.execute(text('INSERT INTO core.invoice_receipt_links(id,entity_id,inbox_id,proposal_id,receipt_id,finance_obligation_id,reviewed_by) VALUES(:id,:e,:s,:p,:r,:o,:a)'),
                dict(id=link_id,e=i['e'],s=source['id'],p=ps['INVOICE_MATCH']['id'],r=r['id'],o=o['id'],a=actor))
            if first=='link':
                held.set();assert release.wait(10),'test release timed out'
        return str(link_id)
    calls={'link':raw_link,'reversal':reverse};second='reversal' if first=='link' else 'link'
    with ThreadPoolExecutor(2) as pool:
        first_result=pool.submit(calls[first]);assert held.wait(10)
        second_result=pool.submit(calls[second])
        try:
            blocked=False;deadline=monotonic()+5
            while monotonic()<deadline:
                if second in pids:
                    with sf() as db:blocked=db.scalar(text('SELECT cardinality(pg_blocking_pids(:p))>0'),dict(p=pids[second]))
                    if blocked:break
                sleep(.01)
            assert blocked,'concurrent operation did not wait on the authority row'
        finally:release.set()
        assert first_result.result(timeout=10)
        if first=='reversal':
            with pytest.raises(DBAPIError) as exc:second_result.result(timeout=10)
            assert exc.value.orig.sqlstate=='23514'
        else:assert second_result.result(timeout=10)['status']=='reversed'
    detail=read(i,'/purchases/'+r['id'])
    assert len(detail['invoice_links'])==(1 if first=='link' else 0)
    if first=='link':assert detail['linked_documents'][0]['status']=='reversed'
    assert finances(i)['payable']=='0.00' and qty(i)==100


def test_existing_nonexpense_invoice_link_does_not_guess_cost_and_reads_write_nothing(inv):
    from greenhouse_owner_os.infrastructure.models import LedgerAccountRow
    i=inv;r=post(i,event(i));asset=uuid4()
    # A synthetic unclassified asset is nonexpense without fabricating an advance.
    with i['preview'][0].begin() as db:
        db.add(LedgerAccountRow(id=asset,entity_id=UUID(i['e']),code='TEST_ASSET',name='TEST asset',account_class='asset',normal_side='debit',currency='TRY'))
    o=send(i['c'],'/obligations',dict(entity_id=i['e'],counterparty_id=i['cp'],direction='payable',
        original_amount='900',currency='TRY',event_date='2026-10-06',offset_ledger_account_id=str(asset)))
    o=send(i['c'],f'/obligations/{o["id"]}/post?expected_version=1',expected=200)
    s,ps=capture(i,r,'existing_payable');confirm(i,revise(i,ps['INVOICE_MATCH'],dict(receipt_id=r['id'],finance_obligation_id=o['id'])))
    sf=i['preview'][0]
    tables=('inventory.events','inventory.movements','core.inbox_items','core.proposal_revisions','core.proposed_actions','core.invoice_receipt_links',
        'core.confirmation_effects','finance.obligations','finance.postings','finance.posting_entries','finance.obligation_attributions','finance.payment_allocations')
    def fingerprint():
        with sf() as db:
            return {table:db.scalar(text('SELECT md5(COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text)::text,\'[]\')) FROM '+table+' t')) for table in tables}
    before=fingerprint()
    detail=read(i,'/purchases/'+r['id']);read(i,'/purchases');read(i,'/inventory/history');read(i,'/inventory/history/'+r['id'])
    analytics=read(i,'/finance/analytics',date_from='2026-10-04',date_to='2026-10-31')
    assert before==fingerprint()
    assert detail['linked_documents'][0]['remaining_amount']=='900.00' and len(ps)==1
    assert analytics['economics']['operating_cost']=='0.00' and Decimal(view(i)['stock'][0]['uncosted_quantity'])==100
    assert i['c'].get('/v1/finance/reconciliation',params={'entity_id':i['e']}).json()=={'ok':True,'findings':[]}


def test_context_scope_direction_and_supplier_history_filters(inv):
    i=inv;r=post(i,event(i,supplier_id=i['cp']));o=payable(i)
    s,ps=capture(i,r,'existing_payable');confirm(i,revise(i,ps['INVOICE_MATCH'],dict(receipt_id=r['id'],finance_obligation_id=o['id'])))
    assert read(i,'/purchases',supplier_id=i['cp'],date_from='2026-10-05',date_to='2026-10-05')['total']==1
    assert read(i,'/purchases',date_from='2026-10-06')['total']==0
    foreign=send(i['c'],'/entities',dict(name='TEST different history entity',currency='TRY',source='test:synthetic'))['id']
    send(i['c'],f'/entities/{foreign}/owner/captures',invoice_body(r,'existing_payable'),expected=422)
    assert i['c'].get(f'/v1/entities/{foreign}/purchases/{r["id"]}').status_code==404
    assert i['c'].get(f'/v1/entities/{foreign}/inventory/history/{r["id"]}').status_code==404
    ar=send(i['c'],'/obligations',dict(entity_id=i['e'],counterparty_id=i['cp'],direction='receivable',original_amount='10',currency='TRY',event_date='2026-10-06',offset_ledger_account_id=i['ids']['GENERAL_REVENUE']))
    ar=send(i['c'],f'/obligations/{ar["id"]}/post?expected_version=1',expected=200)
    s2,p2=capture(i,r,'existing_payable')
    with pytest.raises(DBAPIError) as exc:
        with i['preview'][0].begin() as db:
            db.execute(text('INSERT INTO core.invoice_receipt_links(id,entity_id,inbox_id,proposal_id,receipt_id,finance_obligation_id,reviewed_by) VALUES(:id,:e,:s,:p,:r,:o,:a)'),
                dict(id=uuid4(),e=i['e'],s=s2['id'],p=p2['INVOICE_MATCH']['id'],r=r['id'],o=ar['id'],a=i['preview'][1]))
    assert exc.value.orig.sqlstate=='23514'
    assert len(read(i,'/purchases/'+r['id'])['invoice_links'])==1 and qty(i)==100
    direct=post(i,event(i,qty='10',finance_obligation_id=o['id']))
    filtered=read(i,'/purchases',supplier_id=i['cp'])
    assert filtered['total']==2 and {item['id'] for item in filtered['items']}=={r['id'],direct['id']}
    direct_card=next(item for item in filtered['items'] if item['id']==direct['id'])
    assert direct_card['supplier_id'] is None and direct_card['supplier_name'] is None
    assert direct_card['linked_documents'][0]['counterparty_id']==i['cp'] and qty(i)==110


def test_contextual_text_invoice_reuses_known_facts_without_extra_physical_slots(inv):
    i=inv;r=post(i,event(i));party=send(i['c'],f'/entities/{i["e"]}/counterparties',dict(name='Serkan',role='supplier',source='test:synthetic'))['id']
    raw='Fatura Serkan 500 TRY 06.10.2026'
    body=dict(client_capture_id=str(uuid4()),captured_at='2026-10-07T12:00:00+03:00',capture_context='supplier_invoice',invoice_intent='operating_expense',invoice_receipt_id=r['id'],text=raw)
    s=send(i['c'],f'/entities/{i["e"]}/owner/captures',body);source=read(i,'/owner/captures/'+s['id'])
    proposals=[read(i,'/owner/confirmations/'+p['id']+'/preview') for p in source['proposals']]
    assert {p['action_type'] for p in proposals}=={'PAYABLE','INVOICE_MATCH'}
    payable=next(p for p in proposals if p['action_type']=='PAYABLE');facts=payable['revision']['payload']
    assert facts['amount']=='500' and facts['currency']=='TRY' and facts['event_date']=='2026-10-06' and facts['counterparty_id']==party
    assert payable['revision']['provenance']['amount']['source_text']==raw
    assert payable['revision']['provenance']['amount']['category']=='SOURCE FACT'
    assert finances(i)['payable']=='0.00' and qty(i)==100
    # Known physical receipt values may not fill missing invoice facts.
    body.update(client_capture_id=str(uuid4()),text='Fatura KNO3 100 kg')
    saved=send(i['c'],f'/entities/{i["e"]}/owner/captures',body);source=read(i,'/owner/captures/'+saved['id'])
    p=next(p for p in source['proposals'] if p['action_type']=='PAYABLE');facts=read(i,'/owner/confirmations/'+p['id']+'/preview')['revision']['payload']
    assert all(facts[k] is None for k in ('amount','currency','event_date','counterparty_id'))
