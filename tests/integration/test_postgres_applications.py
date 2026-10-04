from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from decimal import Decimal
from uuid import UUID,uuid4
import copy
import pytest
from sqlalchemy import select,func,text
from sqlalchemy.exc import DBAPIError
from greenhouse_owner_os.domain.commands import ReverseCommand
from greenhouse_owner_os.domain.errors import ValidationError,ConflictError
from greenhouse_owner_os.infrastructure.models import ApplicationRow,PostingRow,PostingEntryRow,AdvanceRow,ObligationRow,PaymentRow,AuditEventRow,IdempotencyRow,LedgerAccountRow,MoneyAccountRow,CounterpartyRow
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyFinanceRepository,SqlAlchemyAuditRepository
from greenhouse_owner_os.services.reconciliation import check_snapshot
from tests.application_cases import CASES,setup,command,apply,undo,remaining
from tests.finance_integrity_cases import obligation,payment,reverse

@pytest.mark.parametrize('case',CASES,ids=lambda c:c.__name__)
@pytest.mark.parametrize('direction',['paid','received'])
def test_pg_application_acceptance(pgfx,case,direction):case(pgfx,direction)


def race(calls):
    barrier=Barrier(len(calls))
    def run(call):
        barrier.wait(timeout=10)
        try: return call()
        except (ValidationError,ConflictError) as e:return e
    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        futures=[pool.submit(run,call) for call in calls]
        return [f.result(timeout=20) for f in futures]

@pytest.mark.parametrize('direction',['paid','received'])
@pytest.mark.parametrize('scenario',['one_advance','one_obligation','same_key','application_reversal','source_reversal','obligation_reversal'])
def test_application_races(pgfx,direction,scenario):
    fx=pgfx;pay,ob=setup(fx,direction,'100','100')
    cmd=command(fx,pay,ob,'60',direction)
    if scenario=='one_advance':
        other=obligation(fx,'100',direction='payable' if direction=='paid' else 'receivable')
        calls=[lambda:apply(fx,cmd),lambda:apply(fx,command(fx,pay,other,'60',direction))]
    elif scenario=='one_obligation':
        otherpay=payment(fx,amount='100',direction='out' if direction=='paid' else 'in')
        calls=[lambda:apply(fx,cmd),lambda:apply(fx,command(fx,otherpay,ob,'60',direction))]
    elif scenario=='same_key':calls=[lambda:apply(fx,cmd,key='race'),lambda:apply(fx,cmd,key='race')]
    elif scenario=='application_reversal':
        r=apply(fx,cmd);calls=[lambda:apply(fx,cmd),lambda:undo(fx,r)]
    elif scenario=='source_reversal':calls=[lambda:apply(fx,cmd),lambda:reverse(fx,'payment',pay)]
    else:calls=[lambda:apply(fx,cmd),lambda:reverse(fx,'obligation',ob)]
    results=race(calls)
    successes=[r for r in results if isinstance(r,dict)]
    if scenario=='same_key':assert results[0]==results[1]
    elif scenario=='application_reversal':assert len(successes) in (1,2)
    else:assert len(successes)==1,results
    assert all(Decimal(v)>=0 for v in remaining(fx,pay,ob))
    assert fx.service.reconcile()=={'ok':True,'findings':[]}

@pytest.mark.parametrize('direction',['paid','received'])
@pytest.mark.parametrize('same_key',[True,False])
def test_concurrent_application_reversals(pgfx,direction,same_key):
    pay,ob=setup(pgfx,direction,'100','100');r=apply(pgfx,command(pgfx,pay,ob,'60',direction))
    results=race([lambda:undo(pgfx,r,key='first'),lambda:undo(pgfx,r,key='first' if same_key else 'second')])
    if same_key:assert results[0]==results[1]
    else:assert sum(isinstance(r,dict) for r in results)==1
    assert remaining(pgfx,pay,ob)==('100.00','100.00')
    assert pgfx.service.reconcile()['ok']

@pytest.mark.parametrize('direction',['paid','received'])
@pytest.mark.parametrize('phase',['apply','reverse'])
def test_application_atomic_audit_failure(pgfx,monkeypatch,direction,phase):
    pay,ob=setup(pgfx,direction,'100','100');cmd=command(pgfx,pay,ob,'60',direction)
    r=apply(pgfx,cmd) if phase=='reverse' else None
    def counts():
        with pgfx.session_factory() as s:return [s.scalar(select(func.count()).select_from(m)) for m in (ApplicationRow,PostingRow,PostingEntryRow,AuditEventRow,IdempotencyRow)]
    before=counts();balances=remaining(pgfx,pay,ob)
    def call():return undo(pgfx,r,key='atomic') if r else apply(pgfx,cmd,key='atomic')
    def fail(*a,**kw):raise RuntimeError('audit failure')
    with monkeypatch.context() as m:
        m.setattr(SqlAlchemyAuditRepository,'append',fail)
        with pytest.raises(RuntimeError):call()
    assert counts()==before and remaining(pgfx,pay,ob)==balances
    call();assert pgfx.service.reconcile()['ok']

@pytest.mark.parametrize('bypass',['advance','obligation','journal'])
def test_application_db_validation_independent_of_service(pgfx,monkeypatch,bypass):
    pay,ob=setup(pgfx,'paid','100' if bypass!='advance' else '50','100' if bypass!='obligation' else '50')
    before=remaining(pgfx,pay,ob)
    with monkeypatch.context() as m:
        m.setattr(SqlAlchemyFinanceRepository,'advance_remaining',lambda *a:Decimal('1000'))
        m.setattr(SqlAlchemyFinanceRepository,'obligation_remaining',lambda *a:Decimal('1000'))
        if bypass=='journal':
            original=SqlAlchemyFinanceRepository.add_posting
            def wrong(self,p):
                if p.source_type=='advance_application':
                    from greenhouse_owner_os.domain.finance import PostingLine
                    p.lines=[PostingLine(l.ledger_account_id,l.side,Decimal('10')) for l in p.lines]
                return original(self,p)
            m.setattr(SqlAlchemyFinanceRepository,'add_posting',wrong)
        with pytest.raises(DBAPIError):apply(pgfx,command(pgfx,pay,ob,'60'))
    assert remaining(pgfx,pay,ob)==before
    assert pgfx.service.reconcile()['ok']

@pytest.mark.parametrize('action',['update','delete','move','reverse_advance','reverse_payment','reverse_obligation'])
def test_application_history_and_dependency_db_guards(pgfx,action):
    pay,ob=setup(pgfx,'paid','100','100');r=apply(pgfx,command(pgfx,pay,ob,'60'))
    with pgfx.session_factory() as s:
        with pytest.raises(DBAPIError):
            if action=='update':s.execute(text('UPDATE finance.advance_applications SET amount=61 WHERE id=:id'),{'id':r['id']})
            elif action=='delete':s.execute(text('DELETE FROM finance.advance_applications WHERE id=:id'),{'id':r['id']})
            elif action=='move':s.execute(text('UPDATE finance.advance_applications SET obligation_id=:o WHERE id=:id'),{'o':uuid4(),'id':r['id']})
            else:
                table,ident={'reverse_advance':('advances',pay['advance_id']),'reverse_payment':('payments',pay['id']),'reverse_obligation':('obligations',ob['id'])}[action]
                s.execute(text(f"UPDATE finance.{table} SET status='reversed' WHERE id=:id"),{'id':ident})
            s.commit()
        s.rollback()
    assert pgfx.service.reconcile()['ok']


def test_counterparty_position_currency_and_entity_isolation(pgfx):
    pay,ob=setup(pgfx,'paid','50000','30000');apply(pgfx,command(pgfx,pay,ob,'30000'))
    p2,o2=setup(pgfx,'received','20000','15000');apply(pgfx,command(pgfx,p2,o2,'15000','received'))
    position=pgfx.service.get_counterparty_position(pgfx.entity_id,pgfx.counterparty_id,'TRY')
    assert {k:position[k] for k in ('open_payable','open_receivable','supplier_advance_remaining','customer_advance_remaining')}==dict(open_payable='0.00',open_receivable='0.00',supplier_advance_remaining='20000.00',customer_advance_remaining='5000.00')
    assert set(position)=={'entity_id','counterparty_id','currency','open_payable','open_receivable','supplier_advance_remaining','customer_advance_remaining'}
    assert all(v=='0.00' for k,v in pgfx.service.get_counterparty_position(pgfx.entity_id,pgfx.counterparty_id,'USD').items() if k.endswith('remaining') or k.startswith('open_'))
    assert pgfx.service.get_counterparty_position(pgfx.entity_id,pgfx.other_counterparty_id,'TRY')['supplier_advance_remaining']=='0.00'
    with pytest.raises(ValidationError):pgfx.service.get_counterparty_position(uuid4(),pgfx.counterparty_id,'TRY')

@pytest.mark.parametrize('corruption',['unbalanced','allocation','advance','application','scope','reversal','replacement','orphan'])
def test_reconciliation_detects_invalid_snapshot_without_repair(pgfx,corruption):
    from greenhouse_owner_os.services.reconciliation import ObligationRow,PaymentRow,PaymentAllocationRow,AdvanceRow,ApplicationRow,PostingRow,PostingEntryRow,LedgerAccountRow,MoneyAccountRow,CounterpartyRow,TransferRow,FundingRow,SettlementRow
    pay,ob=setup(pgfx,'paid','100','100');r=apply(pgfx,command(pgfx,pay,ob,'60'))
    assert pgfx.service.reconcile()=={'ok':True,'findings':[]}
    models=[ObligationRow,PaymentRow,PaymentAllocationRow,AdvanceRow,ApplicationRow,PostingRow,PostingEntryRow,LedgerAccountRow,MoneyAccountRow,CounterpartyRow,TransferRow,FundingRow,SettlementRow]
    with pgfx.session_factory() as s:
        data={m:{row.id:row for row in s.scalars(select(m)).all()} for m in models}
        # Detached copy simulates a corrupt persisted snapshot without disabling
        # production integrity guards or committing invalid financial data.
        s.expunge_all()
    data=copy.deepcopy(data);a=data[ApplicationRow][UUID(r['id'])]
    if corruption=='unbalanced':next(e for e in data[PostingEntryRow].values() if e.posting_id==a.posting_id).amount+=1
    elif corruption=='allocation':data[PaymentRow][UUID(pay['id'])].amount=Decimal('1')
    elif corruption=='advance':data[AdvanceRow][UUID(pay['advance_id'])].original_amount=Decimal('1')
    elif corruption=='application':a.amount=Decimal('101')
    elif corruption=='scope':a.currency='USD'
    elif corruption=='reversal':a.status='reversed';a.reversal_posting_id=uuid4()
    elif corruption=='replacement':data[ObligationRow][UUID(ob['id'])].replacement_of_id=uuid4()
    else:del data[AdvanceRow][UUID(pay['advance_id'])]
    result=check_snapshot(data)
    assert not result['ok'] and result['findings']
    assert result==check_snapshot(data)
    assert pgfx.service.reconcile()=={'ok':True,'findings':[]}

@pytest.mark.parametrize('kind',['payment','obligation'])
def test_active_application_blocks_valid_source_reversal_at_database(pgfx,monkeypatch,kind):
    pay,ob=setup(pgfx,'paid','100','100');r=apply(pgfx,command(pgfx,pay,ob,'60'))
    with monkeypatch.context() as m:
        # Bypass only service dependency checks; still construct the correct
        # exact compensating journal and full reversal lifecycle metadata.
        m.setattr(SqlAlchemyFinanceRepository,'application_total',lambda *a,**k:Decimal('0'))
        with pytest.raises(DBAPIError,match='reverse active advance applications first'):
            reverse(pgfx,kind,pay if kind=='payment' else ob)
    assert remaining(pgfx,pay,ob)==('40.00','40.00')
    assert pgfx.service.reconcile()['ok']
    undo(pgfx,r);reverse(pgfx,kind,pay if kind=='payment' else ob)

@pytest.mark.parametrize('direction',['paid','received'])
def test_application_combines_with_payment_allocation(pgfx,direction):
    pay,ob=setup(pgfx,direction,'100','100');r=apply(pgfx,command(pgfx,pay,ob,'60',direction))
    bad=payment(pgfx,[(ob['id'],'50')],'50',direction='out' if direction=='paid' else 'in',post=False)
    with pytest.raises(ValidationError):pgfx.service.post_payment(actor_id=pgfx.actor_id,idempotency_key='bad',payment_id=UUID(bad['id']),expected_version=1)
    p2=payment(pgfx,[(ob['id'],'40')],'40',direction='out' if direction=='paid' else 'in')
    assert remaining(pgfx,pay,ob)==('40.00','0.00')
    reverse(pgfx,'payment',p2);undo(pgfx,r)
    assert remaining(pgfx,pay,ob)==('100.00','100.00')
    assert pgfx.service.reconcile()['ok']


def test_application_combines_with_direct_settlement(pgfx):
    from greenhouse_owner_os.domain.cash import SettlementCommand
    from tests.finance_integrity_cases import DAY
    pay,ob=setup(pgfx,'paid','100','100');apply(pgfx,command(pgfx,pay,ob,'60'))
    receivable=obligation(pgfx,'100',direction='receivable',counterparty=pgfx.other_counterparty_id)
    draft=pgfx.service.create_cash(kind='direct_settlement',actor_id=pgfx.actor_id,idempotency_key='direct',
        command=SettlementCommand(entity_id=pgfx.entity_id,receivable_id=UUID(receivable['id']),payable_id=UUID(ob['id']),amount='40',currency='TRY',event_date=DAY))
    result=pgfx.service.post_cash(kind='direct_settlement',resource_id=UUID(draft['id']),actor_id=pgfx.actor_id,idempotency_key='direct-post',expected_version=1)
    assert remaining(pgfx,pay,ob)==('40.00','0.00')
    assert pgfx.service.reconcile()['ok']
    pgfx.service.reverse_cash(kind='direct_settlement',resource_id=UUID(result['id']),actor_id=pgfx.actor_id,idempotency_key='direct-reverse',command=ReverseCommand(expected_version=2,event_date=DAY,reason='restore'))
    assert remaining(pgfx,pay,ob)==('40.00','40.00')
