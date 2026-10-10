"""Bounded direct-expense history is visible without replacing Finance balances."""
from decimal import Decimal
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.integration.test_postgres_harvest_evidence_read import context


def post(client, path, body):
    result=client.post('/v1'+path,json=body,headers={'Idempotency-Key':str(uuid4())})
    assert result.status_code in (200,201), result.text
    return result.json()


def finance_context(url, currencies=('TRY',)):
    sf,actor,entity,client,app=context(url)
    accounts={}
    for currency in currencies:
        post(client,'/entities/'+entity+'/reference-bootstrap',dict(currency=currency))
        accounts[currency]=post(client,'/entities/'+entity+'/money-accounts',dict(name='TEST Expense '+currency,
            account_type='cash',currency=currency,source='test:direct-expense-view'))['id']
    return sf,actor,entity,client,app,accounts


def expense(client, entity, account, amount='7.25', currency='TRY', day='2026-10-10'):
    root='/entities/'+entity+'/owner'
    raw=f'Купил бензин за {amount} {currency}.'
    source=post(client,root+'/captures',dict(client_capture_id=str(uuid4()),captured_at='2026-10-10T10:00:00+03:00',
        timezone='Europe/Istanbul',mode='text',text=raw))
    source=client.get('/v1'+root+'/captures/'+source['id']).json()
    assert len(source['proposals']) == 1
    proposal_path=root+'/confirmations/'+source['proposals'][0]['id']
    proposal=client.get('/v1'+proposal_path+'/preview').json()
    assert proposal['action_type'] == 'DIRECT_EXPENSE'
    proposal=post(client,proposal_path+'/revise',dict(expected_version=proposal['version'],payload=dict(amount=amount,currency=currency,
        event_date=day,money_account_id=account,description='TEST топливо из исходной записи'),reason='TEST explicit source-linked expense'))
    result=post(client,proposal_path+'/confirm',dict(expected_version=proposal['version'],preview_digest=proposal['revision']['preview_digest'],confirmed=True))
    assert result['state'] == 'POSTED'
    return result['effect'], source, raw


def snapshot(sf,actor,entity):
    with sf() as db:
        result={}
        for table in ('finance.direct_expenses','finance.postings','finance.payments','finance.obligations','finance.advances','core.confirmation_effects'):
            result[table]=db.execute(text('SELECT to_jsonb(t) FROM '+table+' t WHERE entity_id=:e ORDER BY to_jsonb(t)::text'),dict(e=entity)).scalars().all()
        result['audit']=db.scalar(text('SELECT count(*) FROM audit.events WHERE actor_id=:a'),dict(a=actor))
        result['keys']=db.scalar(text('SELECT count(*) FROM audit.idempotency_keys WHERE actor_id=:a'),dict(a=actor))
        return result


def test_direct_expense_read_tracks_source_currency_reversal_and_scope(migrated_database):
    sf,actor,entity,client,app,accounts=finance_context(migrated_database,('TRY','USD'))
    first,source,raw=expense(client,entity,accounts['TRY'],'10.10','TRY','2026-10-09')
    second,_,_=expense(client,entity,accounts['USD'],'20.25','USD','2026-10-10')
    path='/v1/entities/'+entity+'/finance-overview'
    before=snapshot(sf,actor,entity)
    reloaded=TestClient(app())
    view=reloaded.get(path).json()
    assert view['direct_expenses_total'] == 2 and view['direct_expenses_limit'] == 50 and view['direct_expenses_has_more'] is False
    assert view['direct_expenses_order'] == 'created_at_desc_id_desc' and view['direct_expenses_period'] == 'ALL_DATES'
    assert [r['id'] for r in view['direct_expenses']] == [second['id'],first['id']]
    result=view['direct_expenses'][1]
    assert result['amount'] == '10.10' and result['currency'] == 'TRY' and result['event_date'] == '2026-10-09'
    assert result['status'] == 'posted' and result['entity_id'] == entity
    assert result['proposal_id'] == first['proposal_id'] and result['source_id'] == source['id']
    assert reloaded.get('/v1/entities/'+entity+'/owner/captures/'+result['source_id']+'/original').text == raw
    assert view['balances']['TRY']['cash'] == '-10.10' and view['balances']['USD']['cash'] == '-20.25'
    assert all(b['payable']=='0.00' and b['receivable']=='0.00' for b in view['balances'].values())
    assert view['operations'] == [], 'Legacy operation family paging is unchanged'
    assert snapshot(sf,actor,entity) == before
    post(client,'/entities/'+entity+'/owner/direct-expenses/'+first['id']+'/reverse',
        dict(expected_version=1,event_date='2026-10-10',reason='TEST explicit correction'))
    reversed_view=client.get(path).json()
    assert reversed_view['direct_expenses_total'] == 2
    assert next(r for r in reversed_view['direct_expenses'] if r['id']==first['id'])['status'] == 'reversed'
    assert reversed_view['balances']['TRY']['cash'] == '0.00' and reversed_view['balances']['USD']['cash'] == '-20.25'
    assert client.get('/v1/finance/reconciliation',params={'entity_id':entity}).json()['ok']
    _,_,other,other_client,_=context(migrated_database)
    assert other_client.get(path).status_code == 403
    assert client.get('/v1/entities/'+other+'/finance-overview').status_code == 403
    def unauthenticated():raise HTTPException(401,'Login required')
    assert TestClient(app(unauthenticated)).get(path).status_code == 401


def test_direct_expense_recent_limit_is_explicit_and_not_full_history(migrated_database):
    sf,actor,entity,client,app,accounts=finance_context(migrated_database)
    records=[expense(client,entity,accounts['TRY'],f'{n+1}.01')[0] for n in range(51)]
    before=snapshot(sf,actor,entity)
    view=client.get('/v1/entities/'+entity+'/finance-overview?offset=3&limit=2').json()
    assert view['direct_expenses_total'] == 51 and view['direct_expenses_limit'] == 50 and view['direct_expenses_has_more'] is True
    assert len(view['direct_expenses']) == 50
    assert [r['id'] for r in view['direct_expenses']] == [r['id'] for r in reversed(records[1:])]
    assert records[0]['id'] not in {r['id'] for r in view['direct_expenses']}
    expected=-sum((Decimal(r['amount']) for r in records),Decimal('0'))
    assert view['balances']['TRY']['cash'] == f'{expected:.2f}'
    assert sum((Decimal(r['amount']) for r in view['direct_expenses']),Decimal('0')) != -expected
    assert view['offset'] == 3 and view['limit'] == 2 and view['operations'] == []
    assert snapshot(sf,actor,entity) == before
