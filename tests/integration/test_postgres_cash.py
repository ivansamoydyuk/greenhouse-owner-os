from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4
from decimal import Decimal
import pytest
from sqlalchemy import text,select,func
from sqlalchemy.exc import DBAPIError
from greenhouse_owner_os.infrastructure.models import LedgerAccountRow,MoneyAccountRow,EntityRow,CounterpartyRow,TransferRow,FundingRow,SettlementRow,PostingRow,PostingEntryRow,AuditEventRow,IdempotencyRow
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyAuditRepository
from greenhouse_owner_os.domain.cash import TransferCommand, FundingCommand, SettlementCommand, SupplierAdvanceCommand
from greenhouse_owner_os.domain.commands import ReverseCommand
from greenhouse_owner_os.domain.errors import ValidationError, ConflictError
from tests.cash_cases import CASES, create,post,reverse,transfer,funding,settlement,balance,base
from tests.finance_integrity_cases import DAY,obligation,payment


@pytest.fixture
def cashpg(pgfx):
    from types import SimpleNamespace
    fx=SimpleNamespace(**pgfx.__dict__)
    fx.destination_id=uuid4();ledger=uuid4();fx.funding_ledger_id=uuid4()
    with fx.session_factory.begin() as s:
        s.add_all([LedgerAccountRow(id=ledger,entity_id=fx.entity_id,code='BANK2',name='Bank 2',account_class='cash',normal_side='debit',currency='TRY'),
          LedgerAccountRow(id=fx.funding_ledger_id,entity_id=fx.entity_id,code='OWNER',name='Owner funding',account_class='owner_funding',normal_side='credit',currency='TRY',system_code='OWNER_FUNDING')])
        s.flush()
        s.add(MoneyAccountRow(id=fx.destination_id,entity_id=fx.entity_id,ledger_account_id=ledger,account_type='bank',name='Bank 2',currency='TRY'))
    return fx


@pytest.mark.parametrize('case',CASES,ids=lambda c:c.__name__)
def test_postgres_cash_acceptance(cashpg,case): case(cashpg)


def counts(fx):
    with fx.session_factory() as s:
        return [s.scalar(select(func.count()).select_from(m)) for m in (TransferRow,FundingRow,SettlementRow,PostingRow,PostingEntryRow,AuditEventRow,IdempotencyRow)]


@pytest.mark.parametrize('kind',['transfer','owner_funding','direct_settlement'])
@pytest.mark.parametrize('phase',['create','post','reverse'])
def test_atomic_failure_and_safe_retry(cashpg,monkeypatch,kind,phase):
    fx=cashpg;cmd={'transfer':lambda:transfer(fx,fee='50'),'owner_funding':lambda:funding(fx),'direct_settlement':lambda:settlement(fx)}[kind]()
    r=None
    if phase!='create':r=create(fx,kind,cmd)
    if phase=='reverse':r=post(fx,kind,r)
    before=counts(fx)
    def call():
        if phase=='create':return create(fx,kind,cmd,key='failed')
        if phase=='post':return post(fx,kind,r,key='failed')
        return reverse(fx,kind,r,key='failed')
    def fail(*a,**kw):raise RuntimeError('injected late audit failure')
    with monkeypatch.context() as m:
        m.setattr(SqlAlchemyAuditRepository,'append',fail)
        with pytest.raises(RuntimeError):call()
    assert counts(fx)==before
    result=call();assert result['id']


@pytest.mark.parametrize('phase',['create','post','reverse'])
def test_concurrent_transfer_retry(cashpg,phase):
    fx=cashpg;cmd=transfer(fx);r=None
    if phase!='create':r=create(fx,'transfer',cmd)
    if phase=='reverse':r=post(fx,'transfer',r)
    barrier=Barrier(2)
    def call(i):
        barrier.wait(timeout=5)
        if phase=='create':return create(fx,'transfer',cmd,key='race')
        if phase=='post':return post(fx,'transfer',r,key='race')
        return reverse(fx,'transfer',r,key='race')
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(call,range(2)))
    assert results[0]==results[1]
    if phase=='create':r=post(fx,'transfer',results[0])
    assert balance(fx,fx.money_account_id)==(0 if phase=='reverse' else Decimal('-20000'))
    assert balance(fx,fx.destination_id)==(0 if phase=='reverse' else Decimal('20000'))


def test_opposite_transfers_and_shared_cash_account(cashpg):
    fx=cashpg
    a=create(fx,'transfer',transfer(fx,'20000'))
    b=create(fx,'transfer',TransferCommand(**base(fx,'7000'),source_account_id=fx.destination_id,destination_account_id=fx.money_account_id))
    f=create(fx,'owner_funding',funding(fx,'100000'))
    barrier=Barrier(3)
    def call(pair):
        barrier.wait(timeout=5);return post(fx,*pair)
    with ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(call,[('transfer',a),('transfer',b),('owner_funding',f)]))
    assert balance(fx,fx.money_account_id)==Decimal('87000') and balance(fx,fx.destination_id)==Decimal('13000')


@pytest.mark.parametrize('same_key',[False,True])
def test_concurrent_direct_settlements_no_over_settle(cashpg,same_key):
    fx=cashpg;cmd=settlement(fx,'15000');a=create(fx,'direct_settlement',cmd)
    b=a if same_key else create(fx,'direct_settlement',cmd)
    barrier=Barrier(2)
    def call(i):
        barrier.wait(timeout=5)
        try:return post(fx,'direct_settlement',[a,b][i],key='race' if same_key else str(uuid4()))
        except ValidationError:return 'rejected'
    with ThreadPoolExecutor(max_workers=2) as pool:result=list(pool.map(call,range(2)))
    if same_key:assert result[0]==result[1]
    else:assert result.count('rejected')==1
    assert fx.service.get_obligation(cmd.payable_id)['remaining_amount']=='5000.00'
    assert fx.service.get_obligation(cmd.receivable_id)['remaining_amount']=='15000.00'
    assert balance(fx,fx.money_account_id)==0


def test_settlement_vs_payment_race(cashpg):
    fx=cashpg;cmd=settlement(fx,'15000');r=create(fx,'direct_settlement',cmd)
    p=payment(fx,[(str(cmd.payable_id),'15000')],'15000',post=False)
    barrier=Barrier(2)
    def call(i):
        barrier.wait(timeout=5)
        try:
            if i==0:return post(fx,'direct_settlement',r)
            return fx.service.post_payment(actor_id=fx.actor_id,idempotency_key='payment-race',payment_id=UUID(p['id']),expected_version=1)
        except ValidationError:return 'rejected'
    with ThreadPoolExecutor(max_workers=2) as pool:result=list(pool.map(call,range(2)))
    assert result.count('rejected')==1
    assert fx.service.get_obligation(cmd.payable_id)['remaining_amount']=='5000.00'


@pytest.mark.parametrize('bad',['over_receivable','over_payable','currency','entity','direction'])
def test_direct_validation(cashpg,bad):
    fx=cashpg;cmd=settlement(fx);data=cmd.model_dump()
    if bad=='over_receivable':
        ar=obligation(fx,'10000','receivable');data['receivable_id']=UUID(ar['id']);data['amount']=Decimal('15000')
    elif bad=='over_payable':data['amount']=Decimal('25000')
    elif bad=='currency':data['currency']='USD'
    elif bad=='entity':data['entity_id']=uuid4()
    else:data['receivable_id'],data['payable_id']=data['payable_id'],data['receivable_id']
    before=counts(fx)
    with pytest.raises(ValidationError):post(fx,'direct_settlement',create(fx,'direct_settlement',SettlementCommand(**data)))
    after=counts(fx)
    assert after[3:]==before[3:] if bad not in ('over_receivable','over_payable') else after[3]==before[3]


@pytest.mark.parametrize('kind,table',[('transfer','own_account_transfers'),('owner_funding','owner_fundings'),('direct_settlement','direct_settlements')])
@pytest.mark.parametrize('sql',['UPDATE {table} SET amount=1 WHERE id=:id','UPDATE {table} SET version=99 WHERE id=:id','DELETE FROM {table} WHERE id=:id'])
def test_posted_cash_history_immutable(cashpg,kind,table,sql):
    fx=cashpg;cmd={'transfer':lambda:transfer(fx),'owner_funding':lambda:funding(fx),'direct_settlement':lambda:settlement(fx)}[kind]()
    r=post(fx,kind,create(fx,kind,cmd))
    with pytest.raises(DBAPIError):
        with fx.session_factory.begin() as s:s.execute(text(sql.format(table='finance.'+table)),{'id':UUID(r['id'])})
    reverse(fx,kind,r)
    with pytest.raises(DBAPIError):
        with fx.session_factory.begin() as s:s.execute(text(sql.format(table='finance.'+table)),{'id':UUID(r['id'])})


def test_fee_class_rejected_and_supplier_scope(cashpg):
    fx=cashpg
    with fx.session_factory() as s:cash_ledger=s.scalar(select(MoneyAccountRow.ledger_account_id).where(MoneyAccountRow.id==fx.money_account_id))
    data=transfer(fx,fee='50').model_dump();data['fee_account_id']=cash_ledger
    with pytest.raises(ValidationError):create(fx,'transfer',TransferCommand(**data))
    cmd=SupplierAdvanceCommand(**base(fx,'50000'),money_account_id=fx.money_account_id,counterparty_id=uuid4())
    with pytest.raises(ValidationError):fx.service.create_supplier_advance(actor_id=fx.actor_id,idempotency_key='wrong-supplier',command=cmd)


def test_transfer_cross_currency_and_entity(cashpg):
    fx=cashpg
    for currency,entity in [('USD',fx.entity_id),('TRY',uuid4())]:
        aid=uuid4();lid=uuid4()
        with fx.session_factory.begin() as s:
            if entity!=fx.entity_id:s.add(EntityRow(id=entity,name='Other',default_currency='TRY'));s.flush()
            s.add(LedgerAccountRow(id=lid,entity_id=entity,code=str(lid),name='foreign',account_class='cash',normal_side='debit',currency=currency));s.flush()
            s.add(MoneyAccountRow(id=aid,entity_id=entity,ledger_account_id=lid,name='foreign',account_type='bank',currency=currency))
        data=transfer(fx).model_dump();data['destination_account_id']=aid
        with pytest.raises(ValidationError):create(fx,'transfer',TransferCommand(**data))
