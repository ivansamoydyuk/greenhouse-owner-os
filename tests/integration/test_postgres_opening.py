"""Owner correction: explicit opening AP with ordinary subsequent settlement."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4
import pytest
from sqlalchemy import select, func, text
from sqlalchemy.exc import DBAPIError
from greenhouse_owner_os.api.app import create_app
from greenhouse_owner_os.domain.opening import OpeningPayableCommand
from greenhouse_owner_os.domain.errors import ConflictError
from greenhouse_owner_os.infrastructure.models import (
    ActorRow, EntityRow, OpeningPayableRow, ObligationRow, PostingRow, PostingEntryRow,
    AuditEventRow, IdempotencyRow, LedgerAccountRow, MoneyAccountRow,
)
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyAuditRepository, SqlAlchemyFinanceRepository
from greenhouse_owner_os.services.references import ReferenceService, provision_authenticated_owner
from tests.integration.test_postgres_readiness import preview, setup, send
from scripts.validate_owner_opening import setup_confirmed_profile


def command(cp, **changes):
    return OpeningPayableCommand(**dict(counterparty_id=UUID(cp),amount='2600.00',currency='TRY',
        original_event_date='2026-10-02',description='TEST PREVIEW pipe repair',
        source='test:opening:pipe-repair',approval_reference='test:explicit-owner-approval',**{}) | changes)


def recognize(preview,eid,cp,key=None,**changes):
    return preview[3].recognize_opening_payable(entity_id=UUID(eid),actor_id=preview[1],
        idempotency_key=key or str(uuid4()),command=command(cp,**changes))


def counts(preview):
    with preview[0]() as s:
        return [s.scalar(select(func.count()).select_from(m)) for m in
            (OpeningPayableRow,ObligationRow,PostingRow,PostingEntryRow,AuditEventRow,IdempotencyRow)]


def test_explicit_opening_preserves_original_date_and_no_pnl_cash(preview):
    c,eid,cash,bank,cp,ids=setup(preview)
    before=counts(preview)
    opening=recognize(preview,eid,cp,key='opening')
    assert recognize(preview,eid,cp,key='opening')==opening
    assert recognize(preview,eid,cp)==opening
    assert opening['status']=='posted' and opening['event_date']=='2026-10-02'
    proof=opening['opening_provenance']
    assert proof['opening_date']=='2026-10-04' and proof['original_event_date']=='2026-10-02'
    assert proof['verification_state']=='OWNER_VERIFIED' and proof['status_at_cutover']=='unpaid'
    assert counts(preview)[:4]==[before[0]+1,before[1]+1,before[2]+1,before[3]+2]
    sf,actor,refs,finance,_=preview
    balances=finance.get_currency_balances(UUID(eid))['balances']['TRY']
    assert balances==dict(cash='0.00',payable='2600.00',receivable='0.00',advance_paid='0.00',advance_received='0.00')
    with sf() as s:
        p=s.get(PostingRow,UUID(opening['posting_id']))
        assert p.event_date.isoformat()=='2026-10-04' and p.posting_type=='opening_payable'
        effects=s.execute(select(LedgerAccountRow.account_class,PostingEntryRow.side,PostingEntryRow.amount)
            .join(PostingEntryRow,PostingEntryRow.ledger_account_id==LedgerAccountRow.id)
            .where(PostingEntryRow.posting_id==p.id)).all()
        assert sorted(effects)==[('opening_equity','debit',2600),('payable','credit',2600)]
    status=finance.get_cutover_status(UUID(eid))
    assert status['opening_policy']=='EXPLICIT_AP'
    assert status['opening_balances']['payable']=={'TRY':'2600.00'}
    assert status['opening_payables']==[proof]
    assert finance.get_linkage('obligation',UUID(opening['id']))['opening_provenance']==proof
    assert refs.readiness(UUID(eid))['ok'] and finance.reconcile(entity_id=UUID(eid))['ok']
    audit=c.get(f"/v1/entities/{eid}/resources/{opening['id']}/audit").json()['events']
    assert [e['action'] for e in audit]==['create','opening_approve','post']


def test_owner_confirmed_profile_normal_payment_allocation_and_reversal(preview):
    sf,_,_,finance,_=preview
    actor=provision_authenticated_owner(sf,external_ref='test:confirmed-profile-owner',
        display_name='TEST VALIDATION authenticated owner adapter',environment='OWNER_VERIFIED')
    refs=ReferenceService(finance.uow_factory,environment='OWNER_VERIFIED')
    from fastapi.testclient import TestClient
    c=TestClient(create_app(finance_service=finance,reference_service=refs,actor_provider=lambda:actor))
    manifest=json.loads((Path(__file__).parents[2]/'config/owner_finance_opening.json').read_text())
    profile=setup_confirmed_profile(c,manifest)
    assert setup_confirmed_profile(c,manifest)==profile
    eid=profile['entity']['id'];ob=profile['opening'];cp=profile['counterparty']['id']
    assert profile['entity']['original_name']==manifest['entity']
    assert [(a['original_name'],a['account_type'],a['currency']) for a in profile['money_accounts']]==[
        ('Банк TR','bank','TRY'),('Банк USD','bank','USD'),('Банк EUR','bank','EUR'),('Cash TR','cash','TRY'),('Cash USD','cash','USD')]
    assert profile['counterparty']['original_name']=='Серкан Ремонт насосов'
    assert all(r['classification']=='OWNER_VERIFIED' for r in [profile['entity'],profile['counterparty'],*profile['money_accounts']])
    balances=finance.get_currency_balances(UUID(eid))['balances']
    assert set(balances)=={'TRY','USD','EUR'}
    assert balances['TRY']['payable']=='2600.00'
    assert all(v=='0.00' for currency,b in balances.items() for k,v in b.items() if (currency,k)!=('TRY','payable'))
    with sf() as s:
        for account in profile['money_accounts']:
            assert s.scalar(text('SELECT balance FROM finance.money_account_balances WHERE money_account_id=:id'),{'id':UUID(account['id'])})==0
    bank=profile['money_accounts'][0]['id']
    # Synthetic settlement only, not an owner-reported actual payment.
    for amount,remaining in [('600.00','2000.00'),('2600.00','0.00')]:
        pay=send(c,'/payments',dict(entity_id=eid,counterparty_id=cp,money_account_id=bank,
            direction='out',amount=amount,currency='TRY',event_date='2026-10-04',
            source='test:synthetic-settlement-not-actual',allocations=[{'obligation_id':ob['id'],'amount':amount}]))
        pay=send(c,f"/payments/{pay['id']}/post?expected_version=1",expected=200)
        assert finance.get_obligation(UUID(ob['id']))['remaining_amount']==remaining
        assert finance.get_currency_balances(UUID(eid))['balances']['TRY']['cash']=='-'+amount
        assert finance.reconcile(entity_id=UUID(eid))['ok']
        send(c,f"/payments/{pay['id']}/reverse",dict(expected_version=pay['version'],event_date='2026-10-04',reason='TEST restore preview, not actual payment'),expected=200)
        assert finance.get_currency_balances(UUID(eid))['balances']['TRY']==balances['TRY']
        assert finance.get_obligation(UUID(ob['id']))['event_date']=='2026-10-02'
    assert refs.readiness(UUID(eid))['ok']
    position=finance.get_counterparty_position(UUID(eid),UUID(cp),'TRY')
    assert position['open_payable']=='2600.00'
    assert finance.get_cutover_status(UUID(eid))['opening_balances']['payable']=={'TRY':'2600.00'}


@pytest.mark.parametrize('change',[
    {'amount':'2601.00'}, {'original_event_date':'2026-10-01'},
    {'description':'different repair'}, {'approval_reference':'another-approval-same-source'},
    {'source':'changed-source-same-approval'},
])
def test_changed_opening_facts_or_duplicate_source_conflict(preview,change):
    c,eid,cash,bank,cp,ids=setup(preview)
    recognize(preview,eid,cp)
    before=counts(preview)
    with pytest.raises(ConflictError):recognize(preview,eid,cp,**change)
    assert counts(preview)==before


@pytest.mark.parametrize('same_key',[True,False])
def test_concurrent_owner_opening_no_double_recognition(preview,same_key):
    c,eid,cash,bank,cp,ids=setup(preview)
    barrier=Barrier(2)
    def run(i):
        barrier.wait(timeout=10)
        return recognize(preview,eid,cp,key='same' if same_key else str(i))
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(run,range(2)))
    assert results[0]==results[1]
    assert preview[3].get_currency_balances(UUID(eid))['balances']['TRY']['payable']=='2600.00'


def test_opening_late_failure_rolls_back_approval_journal_policy(preview,monkeypatch):
    c,eid,cash,bank,cp,ids=setup(preview)
    before=counts(preview)
    original=SqlAlchemyAuditRepository.append
    def fail(self,**kw):
        if kw['action']=='post':raise RuntimeError('late opening audit failure')
        return original(self,**kw)
    with monkeypatch.context() as m:
        m.setattr(SqlAlchemyAuditRepository,'append',fail)
        with pytest.raises(RuntimeError):recognize(preview,eid,cp,key='retry')
    assert counts(preview)==before
    assert preview[3].get_cutover_status(UUID(eid))['opening_policy']=='ZERO'
    assert recognize(preview,eid,cp,key='retry')['status']=='posted'


@pytest.mark.parametrize('corruption',['expense_offset','amount','recognition_date'])
def test_database_rejects_forged_opening_effect_when_service_bypassed(preview,monkeypatch,corruption):
    c,eid,cash,bank,cp,ids=setup(preview)
    before=counts(preview)
    original_get=SqlAlchemyFinanceRepository.get_system_ledger_account
    original_post=SqlAlchemyFinanceRepository.add_posting
    def wrong_account(self,entity,currency,code):
        return original_get(self,entity,currency,'GENERAL_EXPENSE' if code=='OPENING_PAYABLE_OFFSET' else code)
    def wrong_post(self,p):
        from dataclasses import replace
        from datetime import date
        if corruption=='recognition_date':p=replace(p,event_date=date(2026,10,5))
        if corruption=='amount':p=replace(p,lines=[replace(l,amount=l.amount-1) for l in p.lines])
        return original_post(self,p)
    with monkeypatch.context() as m:
        if corruption=='expense_offset':
            m.setattr(SqlAlchemyFinanceRepository,'get_system_ledger_account',wrong_account)
            m.setattr(SqlAlchemyFinanceRepository,'assert_account_class',lambda *a,**kw:None)
        else:m.setattr(SqlAlchemyFinanceRepository,'add_posting',wrong_post)
        with pytest.raises(DBAPIError):recognize(preview,eid,cp)
    assert counts(preview)==before
    assert preview[3].get_cutover_status(UUID(eid))['opening_policy']=='ZERO'


def test_old_normal_posting_and_reserved_offset_still_forbidden(preview):
    c,eid,cash,bank,cp,ids=setup(preview)
    recognize(preview,eid,cp)
    common=dict(entity_id=eid,counterparty_id=cp,direction='payable',original_amount='1.00',currency='TRY')
    for day in ('2026-10-02','2026-10-04'):
        response=c.post('/v1/obligations',json=dict(common,event_date=day,offset_ledger_account_id=ids['OPENING_PAYABLE_OFFSET']),headers={'Idempotency-Key':str(uuid4())})
        assert response.status_code==422
    old=send(c,'/obligations',dict(common,event_date='2026-10-02',offset_ledger_account_id=ids['GENERAL_EXPENSE']))
    assert c.post(f"/v1/obligations/{old['id']}/post?expected_version=1",headers={'Idempotency-Key':'normal-old'}).status_code==422
    assert c.post('/v1/entities/'+eid+'/opening-payables',json=command(cp,original_event_date='2026-10-04').model_dump(mode='json'),headers={'Idempotency-Key':'not-opening'}).status_code==422
    # No bare configuration change can grant a historical posting permission.
    with pytest.raises(DBAPIError):
        with preview[0].begin() as s:
            s.execute(text("UPDATE core.entities SET finance_cutover_date='2026-10-02' WHERE id=:id"),{'id':UUID(eid)})


def test_opening_approval_immutable_and_normal_reversal(preview):
    c,eid,cash,bank,cp,ids=setup(preview)
    opening=recognize(preview,eid,cp)
    for sql in ("UPDATE finance.opening_payables SET amount=1 WHERE obligation_id=:id",
                "DELETE FROM finance.opening_payables WHERE obligation_id=:id",
                "UPDATE finance.obligations SET event_date='2026-10-04' WHERE id=:id"):
        with pytest.raises(DBAPIError):
            with preview[0].begin() as s:s.execute(text(sql),{'id':UUID(opening['id'])})
    send(c,f"/obligations/{opening['id']}/reverse",dict(expected_version=opening['version'],event_date='2026-10-04',reason='TEST opening correction'),expected=200)
    balance=preview[3].get_currency_balances(UUID(eid))['balances']['TRY']
    assert set(balance.values())=={'0.00'}
    assert preview[3].get_obligation(UUID(opening['id']))['event_date']=='2026-10-02'
    assert preview[3].reconcile(entity_id=UUID(eid))['ok']
    assert preview[3].get_cutover_status(UUID(eid))['opening_balances']['payable']=={'TRY':'2600.00'}


@pytest.mark.parametrize('corruption',['amount','original_date','opening_date','missing_approval','missing_source'])
def test_reconciliation_requires_complete_opening_proof_without_repair(preview,corruption):
    c,eid,cash,bank,cp,ids=setup(preview)
    opening=recognize(preview,eid,cp)
    from greenhouse_owner_os.services import reconciliation as rec
    models=[rec.ObligationRow,rec.PaymentRow,rec.PaymentAllocationRow,rec.AdvanceRow,rec.ApplicationRow,
        rec.PostingRow,rec.PostingEntryRow,rec.LedgerAccountRow,rec.MoneyAccountRow,rec.CounterpartyRow,
        rec.TransferRow,rec.FundingRow,rec.SettlementRow,rec.EntityRow,rec.ImportBatchRow,rec.HistoricalCandidateRow]
    with preview[0]() as s:
        rows={m:{r.id:r for r in s.scalars(select(m))} for m in models}
        rows[OpeningPayableRow]={r.obligation_id:r for r in s.scalars(select(OpeningPayableRow))}
        s.expunge_all()
    rows=copy.deepcopy(rows);a=rows[OpeningPayableRow][UUID(opening['id'])]
    from datetime import date
    if corruption=='amount':a.amount+=1
    elif corruption=='original_date':a.original_event_date=date(2026,10,1)
    elif corruption=='opening_date':a.opening_date=date(2026,10,5)
    elif corruption=='missing_source':a.source=''
    else:del rows[OpeningPayableRow][UUID(opening['id'])]
    result=rec.check_snapshot(rows)
    assert not result['ok'] and result==rec.check_snapshot(rows)
    assert preview[3].reconcile(entity_id=UUID(eid))['ok']
