from datetime import date
from decimal import Decimal
from uuid import UUID
from greenhouse_owner_os.services.finance_analytics import allocate_cost, area_policy, POLICY_DATE, root_id


def areas():
    return {str(UUID(int=i)):dict(id=str(UUID(int=i)),name=n,kind='greenhouse',parent_id=None) for i,n in enumerate(('Yeşilöz','İmamlı','Özvadi'),1)}


def test_exact_fractions_residual_conservation_and_determinism():
    a=areas();policy=area_policy(a)
    result=allocate_cost(Decimal('500'),POLICY_DATE,[],True,a,policy)
    assert [r['amount'] for r in result]==['142.86','142.86','214.28']
    assert sum(Decimal(r['amount']) for r in result)==Decimal('500')
    for cents in range(1,120):
        total=Decimal(cents)/100
        forward=allocate_cost(total,POLICY_DATE,[],True,a,policy)
        backward=allocate_cost(total,POLICY_DATE,[],True,dict(reversed(list(a.items()))),area_policy(dict(reversed(list(a.items())))))
        assert forward==backward and sum(Decimal(r['amount']) for r in forward)==total
        assert all(r['classification']=='GENERAL_VIRTUAL' and r['policy_id']=='ADR033-AREA-2026-10-01' for r in forward)


def test_exact_precedence_general_residual_only_and_historical_policy():
    a=areas();policy=area_policy(a);aid=next(iter(a));direct=[dict(work_area_id=aid,amount='100',source='owner:exact')]
    general=allocate_cost(Decimal('500'),POLICY_DATE,direct,True,a,policy)
    assert general[0]['amount']=='100.00' and general[0]['classification']=='EXACT'
    assert sum(Decimal(r['amount']) for r in general[1:])==400
    disputed=allocate_cost(Decimal('500'),POLICY_DATE,direct,False,a,policy)
    assert disputed[-1]['classification']=='UNRESOLVED' and disputed[-1]['amount']=='400.00'
    historical=allocate_cost(Decimal('500'),date(2026,9,30),[],True,a,policy)
    assert len(historical)==1 and historical[0]['provenance']=='GENERAL_POLICY_NOT_EFFECTIVE'


def test_policy_never_invents_ids_or_uses_alias_as_identity():
    a=areas();a.pop(str(UUID(int=1)))
    assert area_policy(a)['status']=='UNRESOLVED_REFERENCE'
    a[str(UUID(int=4))]=dict(id=str(UUID(int=4)),name='Arabik',kind='greenhouse',parent_id=None)
    assert area_policy(a)['targets'][0]['greenhouse_id'] is None
    result=allocate_cost(Decimal('1'),POLICY_DATE,[],True,a,area_policy(a))
    assert result[0]['classification']=='UNRESOLVED'
    assert root_id(UUID(int=999),a) is None


def test_very_large_amount_uses_exact_integer_allocation():
    a=areas();total=Decimal('999999999999999999.99')
    result=allocate_cost(total,POLICY_DATE,[],True,a,area_policy(a))
    assert sum(Decimal(r['amount']) for r in result)==total


def test_unknown_estimate_basis_stays_incomplete_after_zero_kg_rounding():
    from greenhouse_owner_os.services.finance_analytics import FinanceAnalyticsService
    svc=object.__new__(FinanceAnalyticsService);gid=str(UUID(int=1))
    receipt=dict(receipt=dict(id=str(UUID(int=4)),event_date='2026-10-06',evidence_id=str(UUID(int=5))),total=Decimal('.001'),
        exact={},estimated={gid:Decimal('.001')},unallocated=Decimal(0),linked_roots=set(),unknown_basis=Decimal(1),
        estimate=dict(id=str(UUID(int=6))),stale=None)
    result=svc._denominator([receipt],gid)
    assert result['confirmed_kg']=='0.001' and result['unallocated_kg']=='0.000'
    assert result['classification']=='PARTIAL' and result['basis_incomplete'] is True
    assert result['sources'][0]['unknown_basis_count']=='1'
    farm=svc._denominator([receipt],None)
    assert farm['classification']=='EXACT' and farm['exact_kg']=='0.001'


def test_read_only_openapi_contract_matches_live_typed_routes():
    from pathlib import Path
    import yaml
    from greenhouse_owner_os.api.app import create_app
    actual=create_app(finance_service=None,actor_provider=lambda:None).openapi()
    saved=yaml.safe_load((Path(__file__).parents[2]/'openapi/owner_core_v1.yaml').read_text())
    for key in ('/entities/{entity_id}/finance/analytics','/entities/{entity_id}/finance/analytics/documents/{obligation_id}'):
        assert set(saved['paths'][key])=={'get'}
        assert saved['paths'][key]==actual['paths']['/v1'+key]
    assert saved['components']['schemas']['FinanceAnalytics']==actual['components']['schemas']['FinanceAnalytics']
    assert saved['components']['schemas']['DebtDocument']==actual['components']['schemas']['DebtDocument']
    assert saved['components']['schemas']['AnalyticsAccount']['properties']['opening_balance']['anyOf'][-1]['type']=='null'
