"""Exact owner friction cases on real PostgreSQL, with immutable evidence."""
import base64,copy
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID,uuid4
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from tests.integration.test_postgres_readiness import preview,send
from tests.integration.test_postgres_inventory import inv,post
from tests.integration.test_postgres_owner_capture import source,queue,revise,confirm
from tests.unit.test_owner_form_source import harvest,receipt

def context(url):
    return inv.__wrapped__(preview.__wrapped__(url))

def counts(i):
    with i['preview'][0]() as db:
        return {table:db.scalar(text('SELECT count(*) FROM '+table+' WHERE entity_id=:e'),dict(e=i['e'])) for table in ('finance.postings','finance.payments','finance.obligations','finance.direct_expenses','inventory.movements','harvest.depot_settlements')}

def test_structured_field135_original_reload_retry_scope_and_no_depot_effect(migrated_database):
    i=context(migrated_database);b=harvest();before=counts(i)
    raw=b'%PDF-1.4\nTEST synthetic original\n%%EOF'
    b['evidence'].update(filename='TEST-field.pdf',mime_type='application/pdf',file_base64=base64.b64encode(raw).decode())
    path=f"/entities/{i['e']}/harvest/reports"
    saved=send(i['c'],path,b,key='field135')
    assert send(i['c'],path,b,key='field135')==saved
    assert send(i['c'],path,b,key='lost-response-retry')['id']==saved['id']
    meta=f"/v1/entities/{i['e']}/harvest/evidence/{saved['evidence_id']}/metadata"
    original=i['c'].get(meta).json()
    assert original['raw_text'] is None and original['structured_original']==b['evidence']['structured_original']
    assert original['received_at'] and original['created_by']==str(i['preview'][1])
    assert i['c'].get(original['original_url']).content==raw
    assert saved['cut_count']==135 and saved['field_weight_kg'] is None
    # Reusing a field form UUID must not masquerade as a depot original.
    depot=dict(depot_id=i['cp'],event_date='2026-10-10',currency='TRY',lines=[dict(original_quality_name='Grand',net_weight_kg='10',unit_price='2',source_line='TEST separate depot')],evidence=dict(source='manual',existing_id=saved['evidence_id']))
    send(i['c'],f"/entities/{i['e']}/harvest/settlements",depot,expected=422)
    changed=copy.deepcopy(b);changed['cut_count']=136
    send(i['c'],path,changed,expected=422)
    changed['evidence']['structured_original']['fields']['cut_count']='136'
    send(i['c'],path,changed,expected=409)
    assert i['c'].get(meta).json()==original
    other=context(migrated_database)
    assert other['c'].get(meta).status_code==403
    with pytest.raises(DBAPIError),i['preview'][0].begin() as db:
        db.execute(text("UPDATE harvest.evidence SET raw_text='changed' WHERE id=:id"),dict(id=saved['evidence_id']))
    assert counts(i)==before

def test_structured_receipt120_concurrent_confirm_unknown_cost_original_and_no_money(migrated_database):
    i=context(migrated_database);b=receipt(i['p']['id'],i['l']['id']);before=counts(i)
    path=i['root']+'/events'
    raw=b'%PDF-1.4\nTEST warehouse original\n%%EOF'
    b['evidence'].update(filename='TEST-stock.pdf',mime_type='application/pdf',file_base64=base64.b64encode(raw).decode())
    saved=send(i['c'],path,b,key='receipt120')
    assert counts(i)==before
    assert send(i['c'],path,b,key='receipt120')['id']==saved['id']
    assert send(i['c'],path,b,key='receipt-retry')['id']==saved['id']
    changed=copy.deepcopy(b);changed['lines'][0]['quantity']='121';changed['evidence']['structured_original']['fields']['quantity']='121'
    send(i['c'],path,changed,expected=409)
    with ThreadPoolExecutor(2) as pool:
        result=list(pool.map(lambda n:post(i,saved,key='concurrent-'+str(n)),range(2)))
    assert result[0]['id']==result[1]['id']==saved['id']
    assert post(i,saved,key='lost-confirm-response')['id']==saved['id']
    after=counts(i)
    assert after['inventory.movements']==before['inventory.movements']+1
    assert all(after[k]==v for k,v in before.items() if k!='inventory.movements')
    reloaded=i['c'].get('/v1'+path+'/'+saved['id']).json()
    assert reloaded['evidence']['structured_original']==b['evidence']['structured_original']
    assert reloaded['evidence']['raw_text'] is None
    assert reloaded['payload']['lines'][0]['unit_cost'] is None
    assert i['c'].get('/v1'+i['root']).json()['opening_state']=='UNINITIALIZED_VERIFY'
    with i['preview'][0]() as db:
        row=db.execute(text('SELECT evidence FROM inventory.events WHERE id=:id'),dict(id=saved['id'])).scalar_one()
        assert row['file_base64']==b['evidence']['file_base64']
        movements=db.execute(text('SELECT quantity,uncosted_quantity,known_cost FROM inventory.movements WHERE event_id=:id'),dict(id=saved['id'])).all()
        assert len(movements)==1 and str(movements[0].quantity)=='120' and movements[0].uncosted_quantity==120 and movements[0].known_cost==0
    with pytest.raises(DBAPIError),i['preview'][0].begin() as db:
        db.execute(text("UPDATE inventory.events SET evidence='{}'::jsonb WHERE id=:id"),dict(id=saved['id']))

def test_quick8500_preserves_source_requires_payment_and_rebinds_review(migrated_database):
    i=context(migrated_database);before=counts(i)
    saved=source(i,'8500 насос ремонт',capture_context='quick_expense')
    p=next(p for p in queue(i) if p['inbox_id']==saved['id'])
    f=p['revision']['payload'];prov=p['revision']['provenance']
    assert p['action_type']=='DIRECT_EXPENSE' and f['amount']=='8500' and f['description']=='Ремонт насоса'
    assert p['source']['text']=='8500 насос ремонт' and f['money_account_id'] is None and f['payment_purpose'] is None
    assert prov['amount']['category']=='SOURCE FACT' and prov['currency']['basis']=='visible_context_default'
    assert counts(i)==before
    confirm(i,p,expected=422)
    f=dict(f,money_account_id=i['cash']);p=revise(i,p,f)
    assert p['state']=='NEEDS_INPUT' and 'Подтвердите факт оплаты расхода' in p['revision']['missing']
    f=dict(p['revision']['payload'],payment_purpose='expense');ready=revise(i,p,f)
    assert ready['state']=='READY'
    changed=revise(i,ready,dict(ready['revision']['payload'],amount='8501'))
    assert changed['revision']['payload']['payment_purpose'] is None and changed['state']=='NEEDS_INPUT'
    confirm(i,ready,expected=409)
    changed=revise(i,changed,dict(changed['revision']['payload'],amount='8500'))
    ready=revise(i,changed,dict(changed['revision']['payload'],payment_purpose='expense'))
    with ThreadPoolExecutor(2) as pool:
        effects=list(pool.map(lambda n:confirm(i,ready,key='expense-confirm-'+str(n)),range(2)))
    assert effects[0]==effects[1]==confirm(i,ready,key='lost-expense-response')
    after=counts(i)
    assert after['finance.direct_expenses']==before['finance.direct_expenses']+1
    assert after['finance.payments']==before['finance.payments']
    assert after['finance.obligations']==before['finance.obligations'] and after['inventory.movements']==before['inventory.movements']
    reopened=i['c'].get(f"/v1/entities/{i['e']}/owner/captures/{saved['id']}").json()
    assert reopened['text']=='8500 насос ремонт'
    with i['preview'][0]() as db:
        entries=db.execute(text('SELECT pe.side,sum(pe.amount) FROM finance.posting_entries pe JOIN finance.postings p ON p.id=pe.posting_id WHERE p.entity_id=:e GROUP BY pe.side'),dict(e=i['e'])).all()
        assert dict(entries)=={'debit':8500,'credit':8500}
        assert str(db.scalar(text('SELECT amount FROM finance.direct_expenses WHERE entity_id=:e'),dict(e=i['e'])))=='8500.00'

def test_typed_receipt_incomplete_and_replacement_keep_original(migrated_database):
    i=context(migrated_database);b=receipt(i['p']['id'],i['l']['id']);path=i['root']+'/events';before=counts(i)
    incomplete=copy.deepcopy(b);incomplete['lines'][0]['quantity']=None
    incomplete['evidence']['structured_original']['fields']['quantity']=''
    draft=send(i['c'],path,incomplete)
    post(i,draft,expected=422)
    assert counts(i)==before
    # Separate complete source then an explicit reverse-and-replace correction.
    b=receipt(i['p']['id'],i['l']['id']);saved=send(i['c'],path,b);post(i,saved);send(i['c'],path+'/'+saved['id']+'/reverse',dict(event_date='2026-10-10',reason='TEST explicit correction'),expected=200)
    corrected=copy.deepcopy(b);corrected['replacement_of_id']=saved['id'];corrected['lines'][0]['quantity']='121'
    changed=copy.deepcopy(corrected);changed['evidence']['structured_original']['fields']['quantity']='121'
    send(i['c'],path,changed,expected=409)
    child=send(i['c'],path,corrected)
    assert child['payload']['lines'][0]['quantity']=='121'
    assert child['evidence']['structured_original']['fields']['quantity']=='120.00'
    assert send(i['c'],path,corrected)['id']==child['id']
    post(i,child)
    assert i['c'].get('/v1'+path+'/'+saved['id']).json()['evidence']['structured_original']==b['evidence']['structured_original']
    assert counts(i)['finance.postings']==before['finance.postings']
def test_typed_receipt_notes_remain_source_only_and_duplicate_gate_survives(migrated_database):
    i=context(migrated_database);path=i['root']+'/events'
    first=receipt(i['p']['id'],i['l']['id']);first['evidence']['structured_original']['fields']['notes']='TEST first owner comment'
    saved=send(i['c'],path,first);post(i,saved)
    second=receipt(i['p']['id'],i['l']['id']);second['evidence']['structured_original']['fields']['notes']='TEST second owner comment'
    duplicate=send(i['c'],path,second)
    assert duplicate['payload']['notes'] is None
    assert duplicate['evidence']['structured_original']['fields']['notes']=='TEST second owner comment'
    assert 'duplicate' in duplicate['issues']
    post(i,duplicate,expected=422)
    changed=copy.deepcopy(second);changed['evidence']['structured_original']['fields']['notes']='TEST silently changed original'
    send(i['c'],path,changed,expected=409)
    posted=send(i['c'],path+'/'+duplicate['id']+'/post',dict(confirmed=True,expected_version=1,duplicate_reviewed=True),expected=200)
    assert posted['status']=='posted' and len(posted['movements'])==1
    assert i['c'].get('/v1'+path+'/'+posted['id']).json()['evidence']['structured_original']['fields']['notes']=='TEST second owner comment'
