"""Synthetic service-created Finance contrast reusable by PostgreSQL/browser gates."""
from uuid import uuid4


def post_json(client, path, payload=None, expected=201):
    response=client.post('/v1'+path,json=payload,headers={'Idempotency-Key':str(uuid4())})
    assert response.status_code==expected,response.text
    return response.json()


def seed_finance_contrast(client, entity_id, money_account_id, supplier_id, depot_id, work_area_id=None, *, payments=False):
    """No real data: one depot sale 1000 and one owner-confirmed expense payable 500."""
    allocations=[] if not work_area_id else [dict(work_area_id=str(work_area_id),net_weight_kg='20',amount='1000',confirmed_count=2)]
    receipt=post_json(client,f'/entities/{entity_id}/harvest/settlements',dict(depot_id=str(depot_id),event_date='2026-10-06',currency='TRY',
        lines=[dict(original_quality_name='TEST Birinci',net_weight_kg='20',unit_price='50',count=2,source_line='1',allocations=allocations)],
        evidence=dict(source='test:synthetic:finance-analytics',source_reference=str(uuid4()),raw_text='TEST synthetic depot sale 1000 TRY; no receipt of money')))
    receipt=post_json(client,f'/entities/{entity_id}/harvest/settlements/{receipt["id"]}/post',dict(expected_version=receipt['version'],confirmed=True,
        reviewed_issues=receipt['issues'],review_reason='TEST explicit owner source review'),200)
    source=post_json(client,f'/entities/{entity_id}/owner/captures',dict(client_capture_id=str(uuid4()),captured_at='2026-10-06T12:00:00+03:00',text='Должен Серкану 500 за ремонт.'))
    response=client.get(f'/v1/entities/{entity_id}/owner/confirmations');assert response.status_code==200,response.text
    proposal=next(p for p in response.json()['items'] if p['inbox_id']==source['id'] and p['action_type']=='PAYABLE')
    proposal=post_json(client,f'/entities/{entity_id}/owner/confirmations/{proposal["id"]}/revise',dict(expected_version=proposal['version'],
        payload=dict(amount='500',currency='TRY',event_date='2026-10-06',counterparty_id=str(supplier_id),description='TEST operating repair cost')),200)
    payable=post_json(client,f'/entities/{entity_id}/owner/confirmations/{proposal["id"]}/confirm',dict(expected_version=proposal['version'],
        preview_digest=proposal['revision']['preview_digest'],confirmed=True),200)['effect']
    result=dict(entity_id=str(entity_id),cash=str(money_account_id),depot_id=str(depot_id),supplier_id=str(supplier_id),receipt_id=receipt['id'],
        receivable_id=receipt['obligation_id'],payable_id=payable['id'],original_id=source['id'],proposal_id=proposal['id'],payments=[])
    if payments:
        for direction,total,party,oid in [('in','400',depot_id,receipt['obligation_id']),('out','200',supplier_id,payable['id'])]:
            payment=post_json(client,'/payments',dict(entity_id=str(entity_id),counterparty_id=str(party),money_account_id=str(money_account_id),direction=direction,
                amount=total,currency='TRY',event_date='2026-10-06',allocations=[dict(obligation_id=oid,amount=total)]))
            result['payments'].append(post_json(client,f'/payments/{payment["id"]}/post?expected_version=1',None,200))
    return result
