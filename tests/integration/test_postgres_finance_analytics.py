"""Finance economic/cash contrast and read-only evidence on real PostgreSQL."""
from decimal import Decimal
from uuid import UUID,uuid4
import pytest
from sqlalchemy import text
from tests.integration.test_postgres_readiness import preview,setup,send
from tests.integration.test_postgres_harvest import prepare,receipt,draft,post
from tests.integration.test_postgres_harvest_journey import farm,active,command,estimate_body,field
from tests.integration.test_postgres_owner_capture import source,queue,revise,confirm
from tests.finance_analytics_cases import seed_finance_contrast


def get(c,e,**params):
    response=c.get(f'/v1/entities/{e}/finance/analytics',params=dict(date_from='2026-10-04',date_to='2026-10-31',currency='TRY',**params))
    assert response.status_code==200,response.text
    return response.json()


def seeded(preview,payments=True):
    c,e,cash,depot,areas=prepare(preview)
    cp=send(c,f'/entities/{e}/counterparties',dict(name='TEST repair supplier',role='supplier',source='test:synthetic'))['id']
    seed=seed_finance_contrast(c,e,cash,cp,depot,areas['İmamlı'],payments=payments)
    return c,e,cash,areas,seed


def snapshot(preview,e):
    with preview[0]() as db:
        result = {name:db.scalar(text('SELECT md5(coalesce(string_agg(row_to_json(t)::text,\'\' ORDER BY row_to_json(t)::text),\'\')) FROM '+name+' t WHERE entity_id=:e'),dict(e=e)) for name in (
            'finance.obligations','finance.payments','finance.postings','finance.direct_expenses','inventory.events','inventory.movements','core.proposed_actions')}
        result['finance.obligation_attributions']=db.scalar(text("SELECT md5(coalesce(string_agg(row_to_json(a)::text,'' ORDER BY row_to_json(a)::text),'')) FROM finance.obligation_attributions a JOIN finance.obligations o ON o.id=a.obligation_id WHERE o.entity_id=:e"),dict(e=e))
        return result


def test_profit_is_not_cash_or_debt_remaining_and_read_has_no_mutations(preview):
    c,e,cash,areas,seed=seeded(preview)
    before=snapshot(preview,e);result=get(c,e);after=snapshot(preview,e)
    assert before==after
    assert result['economics']['revenue']=='1000.00'
    assert result['economics']['operating_cost']=='500.00'
    assert result['economics']['operating_result']=='500.00'
    assert result['cash_totals']==dict(incoming='400.00',outgoing='200.00',net_change='200.00')
    assert result['debts']['receivable']=='600.00' and result['debts']['payable']=='300.00'
    assert result['harvest_denominator']['confirmed_kg']=='20.000'
    assert result['cost_per_kg']['known_finance_cost_per_kg']=='25.0000' and result['cost_per_kg']['value'] is None
    assert result['coverage']['complete_production_cost'] is False
    assert result['allocation_policy']['affects_ledger'] is False
    assert sum(Decimal(x['virtual_general_cost']) for x in result['areas'])==500
    with preview[0]() as db:
        assert db.scalar(text('SELECT count(*) FROM finance.obligation_attributions WHERE obligation_id=:o'),dict(o=seed['payable_id']))==0
    assert preview[3].reconcile(entity_id=UUID(e))['ok']


def test_debt_documents_keep_originals_full_history_and_entity_guard(preview):
    c,e,cash,areas,seed=seeded(preview)
    response=c.get(f'/v1/entities/{e}/finance/analytics/documents/{seed["payable_id"]}')
    assert response.status_code==200,response.text
    d=response.json();assert d['source']['original_id']==seed['original_id'] and d['source']['proposal_id']==seed['proposal_id']
    assert d['original_amount']=='500.00' and d['remaining_amount']=='300.00'
    assert d['history'][0]['kind']=='payment' and d['history'][0]['amount']=='200.00' and d['history'][0]['status']=='posted'
    other=send(c,'/entities',dict(name='TEST other entity '+str(uuid4()),currency='TRY',source='test:synthetic'))['id']
    assert c.get(f'/v1/entities/{other}/finance/analytics/documents/{seed["payable_id"]}').status_code==404
    assert c.get(f'/v1/entities/{uuid4()}/finance/analytics/documents/{seed["payable_id"]}').status_code in (403,404,422)


def test_account_filter_and_geography_do_not_invent_cash_or_debt_geography(preview):
    c,e,cash,areas,seed=seeded(preview)
    result=get(c,e,greenhouse_id=str(areas['İmamlı']),money_account_id=cash)
    assert result['cash_totals']['net_change']=='200.00' and result['debts']['payable']=='300.00'
    assert result['economics']['revenue']=='1000.00' and result['economics']['operating_cost']=='142.86'
    assert len(result['available_accounts'])==2 and len(result['accounts'])==1
    response=c.get(f'/v1/entities/{e}/finance/analytics',params=dict(date_from='2026-10-04',date_to='2026-10-31',money_account_id=str(uuid4())))
    assert response.status_code==422
    response=c.get(f'/v1/entities/{e}/finance/analytics',params=dict(date_from='2026-10-04',date_to='2026-10-31',greenhouse_id=str(uuid4())))
    assert response.status_code==422


def test_pre_cutover_opening_is_unknown_not_zero(preview):
    c,e,*_=seeded(preview)
    response=c.get(f'/v1/entities/{e}/finance/analytics',params=dict(date_from='2026-10-01',date_to='2026-10-31'))
    assert response.status_code==200,response.text
    result=response.json();assert all(x['opening_balance'] is None and x['opening_basis']=='UNKNOWN_BEFORE_CUTOVER' for x in result['accounts'])
    assert result['cash_totals']['net_change']=='200.00'
    assert 'PERIOD_START_BEFORE_VERIFIED_FINANCE_CUTOVER' in result['coverage']['warnings']


def test_reversal_current_active_economics_distinct_from_dated_cash(preview):
    c,e,cash,areas,seed=seeded(preview)
    paid=seed['payments'][1]
    send(c,f'/payments/{paid["id"]}/reverse',dict(expected_version=2,event_date='2026-10-09',reason='TEST cash correction'),expected=200)
    send(c,f'/obligations/{seed["payable_id"]}/reverse',dict(expected_version=2,event_date='2026-10-09',reason='TEST expense correction'),expected=200)
    response=c.get(f'/v1/entities/{e}/finance/analytics',params=dict(date_from='2026-10-06',date_to='2026-10-06'))
    assert response.status_code==200,response.text
    result=response.json();assert result['economics']['operating_cost']=='0.00' and result['cash_totals']['net_change']=='200.00'
    assert result['debts']['payable']=='0.00'
    all_dates=get(c,e);assert all_dates['cash_totals']['net_change']=='400.00'
    assert any(x['reversal_of_id'] for x in all_dates['movements'])
    doc=next(x for x in all_dates['debts']['documents'] if x['obligation_id']==seed['payable_id'])
    assert doc['status']=='reversed' and doc['history'][0]['status']=='reversed'


def test_transfer_fee_once_funding_and_principal_excluded(preview):
    c,e,cash,bank,cp,ids=setup(preview)
    funding=send(c,'/owner-fundings',dict(entity_id=e,money_account_id=cash,amount='1000',currency='TRY',event_date='2026-10-06',source='test:synthetic'))
    send(c,f'/owner-fundings/{funding["id"]}/post?expected_version=1',expected=200)
    transfer=send(c,'/transfers',dict(entity_id=e,source_account_id=cash,destination_account_id=bank,amount='100',fee='5',fee_account_id=ids['TRANSFER_FEE'],currency='TRY',event_date='2026-10-06'))
    send(c,f'/transfers/{transfer["id"]}/post?expected_version=1',expected=200)
    principal=send(c,'/obligations',dict(entity_id=e,counterparty_id=cp,direction='payable',original_amount='300',currency='TRY',event_date='2026-10-06',obligation_class='STRATEGIC',offset_ledger_account_id=ids['STRATEGIC_PRINCIPAL']))
    send(c,f'/obligations/{principal["id"]}/post?expected_version=1',expected=200)
    result=get(c,e)
    assert result['economics']['revenue']=='0.00' and result['economics']['operating_cost']=='5.00'
    assert result['cash_totals']['net_change']=='995.00' and result['debts']['payable']=='300.00'
    assert len(result['economic_events'])==1 and result['economic_events'][0]['source']['kind']=='transfer'


def test_legacy_absent_and_partial_attribution_are_unresolved_not_general(preview):
    c,e,cash,depot,areas=prepare(preview)
    ids={r['system_code']:r['id'] for r in c.get(f'/v1/entities/{e}/references').json()['references'] if r.get('system_code')}
    for attrs in ([],[dict(work_area_id=str(areas['İmamlı']),amount='100')]):
        o=send(c,'/obligations',dict(entity_id=e,counterparty_id=depot,direction='payable',original_amount='500',currency='TRY',event_date='2026-10-06',offset_ledger_account_id=ids['GENERAL_EXPENSE'],attributions=attrs))
        send(c,f'/obligations/{o["id"]}/post?expected_version=1',expected=200)
    result=get(c,e)
    assert result['economics']['operating_cost']=='1000.00' and result['economics']['exact_cost']=='100.00'
    assert result['economics']['virtual_general_cost']=='0.00' and result['economics']['unresolved_cost']=='900.00'


def test_estimated_and_stale_harvest_provenance_never_reposts_finance(preview):
    i=farm(preview);s,p,r=active(i)
    estimate=command(i,r['id'],'estimates',estimate_body(i))['current_estimate']
    result=get(i['c'],i['e'],greenhouse_id=str(i['areas']['İmamlı']))
    assert result['harvest_denominator']['classification']=='ESTIMATED' and result['harvest_denominator']['estimated_kg']=='600.000'
    assert result['economics']['revenue']=='30000.00'
    assert result['harvest_denominator']['sources'][0]['estimate_revision_id']==estimate['id']
    report,batch=field(i)
    command(i,r['id'],'links',dict(expected_version=1,confirmed=True,batch_ids=[batch['id']],reason='TEST later source link'))
    stale=get(i['c'],i['e'],greenhouse_id=str(i['areas']['İmamlı']))
    assert stale['harvest_denominator']['confirmed_kg'] is None and stale['harvest_denominator']['classification']=='UNAVAILABLE'
    assert stale['harvest_denominator']['sources'][0]['stale_estimate_revision_id']==estimate['id']
    assert stale['economics']['revenue']=='0.00' and stale['coverage']['financial_period_policy']=='D05_OPEN_NOT_HISTORICAL_AS_OF'


def test_read_all_direct_expenses_beyond_recent_fifty(preview):
    c,e,cash,bank,cp,ids=setup(preview);i=dict(c=c,e=e)
    for n in range(51):
        s=source(i,'Потратил 1 TRY на топливо наличными.',capture_context='quick_expense')
        p=next(x for x in queue(i) if x['inbox_id']==s['id'])
        assert p['action_type']=='DIRECT_EXPENSE'
        p=revise(i,p,dict(amount='1',currency='TRY',event_date='2026-10-06',money_account_id=cash,payment_purpose='expense',description='TEST fuel '+str(n)))
        confirm(i,p)
    result=get(c,e)
    assert result['economics']['operating_cost']=='51.00' and len(result['economic_events'])==51
    assert result['cash_totals']['outgoing']=='51.00'


def test_invalid_period_currency_and_unverified_actor_are_rejected(preview):
    c,e,*_=setup(preview)
    for params in [dict(date_from='2026-10-31',date_to='2026-10-01'),dict(date_from='2026-10-01',date_to='2026-10-31',currency='TRYUSD')]:
        assert c.get(f'/v1/entities/{e}/finance/analytics',params=params).status_code==422
    from greenhouse_owner_os.api.app import create_app
    from fastapi.testclient import TestClient
    unauth=TestClient(create_app(finance_service=preview[3],actor_provider=lambda:uuid4()))
    assert unauth.get(f'/v1/entities/{e}/finance/analytics',params=dict(date_from='2026-10-04',date_to='2026-10-31')).status_code==403


def test_non_strategic_asset_offset_does_not_become_operating_expense(preview):
    from greenhouse_owner_os.infrastructure.models import LedgerAccountRow
    c,e,cash,bank,cp,ids=setup(preview);asset=uuid4()
    with preview[0].begin() as db:
        db.add(LedgerAccountRow(id=asset,entity_id=UUID(e),code='TEST_ASSET',name='TEST asset',account_class='asset',normal_side='debit',currency='TRY'))
    ob=send(c,'/obligations',dict(entity_id=e,counterparty_id=cp,direction='payable',original_amount='900',currency='TRY',event_date='2026-10-06',offset_ledger_account_id=str(asset)))
    send(c,f'/obligations/{ob["id"]}/post?expected_version=1',expected=200)
    result=get(c,e)
    assert result['debts']['payable']=='900.00' and result['economics']['operating_cost']=='0.00'
    assert result['coverage']['unclassified_obligation_ids']==[ob['id']]
    assert 'ECONOMIC_CLASSIFICATION_UNRESOLVED' in result['coverage']['warnings']


def test_currency_isolation_without_implicit_conversion(preview):
    c,e,cash,areas,seed=seeded(preview)
    bootstrap=send(c,f'/entities/{e}/reference-bootstrap',dict(currency='USD'),expected=200)
    ids={r['system_code']:r['id'] for r in bootstrap['references']}
    account=send(c,f'/entities/{e}/money-accounts',dict(name='TEST USD bank',currency='USD',account_type='bank',source='test:synthetic'))
    ob=send(c,'/obligations',dict(entity_id=e,counterparty_id=seed['supplier_id'],direction='payable',original_amount='125.37',currency='USD',event_date='2026-10-06',offset_ledger_account_id=ids['GENERAL_EXPENSE']))
    send(c,f'/obligations/{ob["id"]}/post?expected_version=1',expected=200)
    usd=c.get(f'/v1/entities/{e}/finance/analytics',params=dict(date_from='2026-10-04',date_to='2026-10-31',currency='USD')).json()
    assert usd['currency']=='USD' and usd['economics']['operating_cost']=='125.37' and usd['debts']['payable']=='125.37'
    assert usd['economics']['revenue']=='0.00' and usd['cash_totals']['net_change']=='0.00'
    assert all(x['currency']=='USD' for x in usd['accounts'])
    assert usd['harvest_denominator']['confirmed_kg']=='20.000' and 'ALL_CURRENCIES' in usd['harvest_denominator']['basis']
    assert get(c,e)['economics']['operating_cost']=='500.00'
    response=c.get(f'/v1/entities/{e}/finance/analytics',params=dict(date_from='2026-10-04',date_to='2026-10-31',currency='USD',money_account_id=cash))
    assert response.status_code==422


from tests.integration.test_postgres_inventory import inv

def test_purchase_and_consumption_never_double_count_finance_and_unknown_cost_stays_unknown(inv):
    from tests.integration.test_postgres_inventory import event,post as stock_post,line
    i=inv;c,e=i['c'],i['e']
    ob=send(c,'/obligations',dict(entity_id=e,counterparty_id=i['cp'],direction='payable',original_amount='2000',currency='TRY',event_date='2026-10-05',offset_ledger_account_id=i['ids']['GENERAL_EXPENSE']))
    send(c,f'/obligations/{ob["id"]}/post?expected_version=1',expected=200)
    receipt=stock_post(i,event(i,lines=[line(i,'100',unit_cost='20',currency='TRY')],finance_obligation_id=ob['id']))
    application=stock_post(i,event(i,'application','15'))
    unknown=stock_post(i,event(i,'receipt','10'))
    before=snapshot(i['preview'],e);result=get(c,e);assert snapshot(i['preview'],e)==before
    assert result['economics']['operating_cost']=='2000.00'
    coverage=result['coverage']['inventory_cost_rows']
    known=next(x for x in coverage if x['event_id']==receipt['id'])
    assert Decimal(known['known_cost'])==2000 and known['finance_obligation_ids']==[ob['id']] and known['recognition']=='FINANCE_BASIS_ONLY'
    used=next(x for x in coverage if x['event_id']==application['id']);assert Decimal(used['known_cost'])==300
    missing=next(x for x in coverage if x['event_id']==unknown['id']);assert Decimal(missing['uncosted_quantity'])==10
    assert result['coverage']['complete_production_cost'] is False and 'INVENTORY_COST_UNKNOWN' in result['coverage']['warnings']
    assert result['cost_per_kg']['value'] is None


def test_denominator_scope_omits_unlinked_and_other_house_sources(preview):
    i=farm(preview);c,e=i['c'],i['e'];area=i['areas']['İmamlı'];other=i['areas']['Özvadi']
    a_body=receipt(i['depot'],i['areas']);a_body['original_greenhouse']='İmamlı';a_body['lines'][0].update(net_weight_kg='1',unit_price='30',allocations=[dict(work_area_id=str(area),net_weight_kg='1',amount='30')])
    a=post(c,e,draft(c,e,a_body))
    b_body=receipt(i['depot'],i['areas']);b_body['lines'][0].update(net_weight_kg='2',unit_price='30',allocations=[dict(work_area_id=str(other),net_weight_kg='2',amount='60')])
    b=post(c,e,draft(c,e,b_body))
    s,p,unlinked=active(i)
    result=get(c,e,greenhouse_id=str(area))
    assert result['harvest_denominator']['confirmed_kg']=='1.000'
    assert result['harvest_denominator']['classification']=='EXACT'
    assert {x['settlement_id'] for x in result['harvest_denominator']['sources']}=={a['id']}
    report,batch=field(i)
    command(i,unlinked['id'],'links',dict(expected_version=0,confirmed=True,batch_ids=[batch['id']],reason='TEST source explicitly linked to Imamli'))
    linked=get(c,e,greenhouse_id=str(area))
    assert linked['harvest_denominator']['classification']=='PARTIAL'
    assert {x['settlement_id'] for x in linked['harvest_denominator']['sources']}=={a['id'],unlinked['id']}
    other_result=get(c,e,greenhouse_id=str(other))
    assert {x['settlement_id'] for x in other_result['harvest_denominator']['sources']}=={b['id']}
