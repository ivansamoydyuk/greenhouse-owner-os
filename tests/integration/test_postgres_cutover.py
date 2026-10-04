from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, uuid4
import pytest
from sqlalchemy import select, func, text
from sqlalchemy.exc import DBAPIError
from greenhouse_owner_os.infrastructure.models import (
    EntityRow, ImportBatchRow, HistoricalCandidateRow, PostingRow, PostingEntryRow,
    ObligationRow, PaymentRow, FundingRow, AuditEventRow, IdempotencyRow)
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyFinanceRepository, SqlAlchemyAuditRepository
from greenhouse_owner_os.services.reconciliation import check_snapshot
from tests.cutover_cases import CASES, batch, candidate, draft, post, zero, CUTOVER, OLD
from tests.integration.test_postgres_cash import cashpg
from tests.integration.test_postgres_applications import race

@pytest.mark.parametrize('case',CASES,ids=lambda c:c.__name__)
def test_postgres_cutover_acceptance(pgfx,case):case(pgfx)


def counts(fx):
    with fx.session_factory() as s:
        return [s.scalar(select(func.count()).select_from(m)) for m in
                (PostingRow,PostingEntryRow,ImportBatchRow,HistoricalCandidateRow,AuditEventRow,IdempotencyRow)]


def test_zero_opening_has_no_synthetic_journal_or_pnl(pgfx):
    with pgfx.session_factory() as s:
        assert s.scalar(select(func.count()).select_from(PostingRow).where(PostingRow.entity_id==pgfx.entity_id))==0
        assert s.scalar(select(func.count()).select_from(FundingRow).where(FundingRow.entity_id==pgfx.entity_id))==0
        assert s.scalar(text('SELECT balance FROM finance.money_account_balances WHERE money_account_id=:id'),{'id':pgfx.money_account_id})==0
    before=counts(pgfx)
    pgfx.service.get_cutover_status(pgfx.entity_id)
    assert counts(pgfx)==before
    r=post(pgfx,draft(pgfx,day=CUTOVER))
    with pgfx.session_factory() as s:
        posts=s.scalars(select(PostingRow).where(PostingRow.entity_id==pgfx.entity_id)).all()
        assert len(posts)==1 and str(posts[0].source_id)==r['id'] and posts[0].posting_type=='obligation_posted'
        assert s.scalar(select(func.count()).select_from(PostingEntryRow).where(PostingEntryRow.posting_id==posts[0].id))==2
    assert pgfx.service.reconcile()['ok']


@pytest.mark.parametrize('kind',['obligation','payment'])
def test_database_guard_even_when_service_validation_bypassed(pgfx,monkeypatch,kind):
    r=draft(pgfx,kind)
    before=counts(pgfx)
    monkeypatch.setattr(SqlAlchemyFinanceRepository,'assert_live_date',lambda *a:None)
    with pytest.raises(DBAPIError,match='pre-cut-over'):post(pgfx,r,kind)
    assert counts(pgfx)==before
    zero(pgfx)
    with pgfx.session_factory() as s:
        with pytest.raises(DBAPIError,match='pre-cut-over'):
            s.execute(text(f"UPDATE finance.{'obligations' if kind=='obligation' else 'payments'} SET status='posted' WHERE id=:id"),{'id':r['id']})
            s.commit()


@pytest.mark.parametrize('change',["finance_cutover_date=DATE '2025-01-01'", "finance_cutover_date=DATE '2026-10-05'", "finance_opening_policy='NONZERO'"])
def test_cutover_config_cannot_be_relaxed(pgfx,change):
    with pgfx.session_factory() as s:
        with pytest.raises(DBAPIError):
            s.execute(text(f'UPDATE core.entities SET {change} WHERE id=:id'),{'id':pgfx.entity_id});s.commit()
    assert pgfx.service.get_cutover_status(pgfx.entity_id)['cutover_date']=='2026-10-04'


@pytest.mark.parametrize('state',['HISTORY','VERIFY','VERIFIED'])
def test_candidate_never_posts_even_with_new_date_or_forged_type(pgfx,state):
    b=batch(pgfx)
    r=candidate(pgfx,b,event_date=CUTOVER,verification_state=state)
    with pgfx.session_factory() as s:
        with pytest.raises(DBAPIError,match='non-posting evidence'):
            s.execute(text("""INSERT INTO finance.postings(id,entity_id,currency,event_date,posting_type,source_type,source_id,created_by)
                VALUES(:id,:entity,'TRY',:day,'payment_posted','payment',:source,:actor)"""),
                {'id':uuid4(),'entity':pgfx.entity_id,'day':CUTOVER,'source':UUID(r['id']),'actor':pgfx.actor_id})
            s.commit()
    zero(pgfx)
    assert pgfx.service.reconcile(include_classification=True)['ok']


@pytest.mark.parametrize('table,change',[
    ('import_batches',"source='changed'"),('import_batches',None),
    ('historical_candidates',"event_date=DATE '2026-10-04'"),
    ('historical_candidates',"verification_state='VERIFIED'"),
    ('historical_candidates',"import_batch_id=gen_random_uuid()"),('historical_candidates',None)])
def test_original_import_evidence_is_immutable(pgfx,table,change):
    b=batch(pgfx);r=candidate(pgfx,b);rid=b['id'] if table=='import_batches' else r['id']
    with pgfx.session_factory() as s:
        with pytest.raises(DBAPIError):
            stmt=f'UPDATE core.{table} SET {change} WHERE id=:id' if change else f'DELETE FROM core.{table} WHERE id=:id'
            s.execute(text(stmt),{'id':rid});s.commit()
    assert pgfx.service.get_historical_candidate(UUID(r['id']))==r


@pytest.mark.parametrize('phase',['batch','candidate'])
def test_concurrent_import_sources_dedupe_across_keys_and_actors(pgfx,phase):
    if phase=='batch':
        results=race([lambda:batch(pgfx,key='one'),lambda:batch(pgfx,key='two')])
    else:
        b=batch(pgfx)
        results=race([lambda:candidate(pgfx,b,key='one'),lambda:candidate(pgfx,b,key='two')])
    assert results[0]==results[1] and isinstance(results[0],dict),results
    with pgfx.session_factory() as s:
        batches=s.scalars(select(ImportBatchRow).where(ImportBatchRow.entity_id==pgfx.entity_id)).all()
        assert len(batches)==1
        assert s.scalar(select(func.count()).select_from(HistoricalCandidateRow).where(HistoricalCandidateRow.import_batch_id==batches[0].id))==(1 if phase=='candidate' else 0)
    zero(pgfx)


@pytest.mark.parametrize('phase',['batch','candidate'])
def test_import_late_failure_rolls_back_and_retry_safe(pgfx,monkeypatch,phase):
    b=batch(pgfx) if phase=='candidate' else None
    before=counts(pgfx)
    def call():return candidate(pgfx,b,key='atomic') if b else batch(pgfx,key='atomic')
    def fail(*a,**kw):raise RuntimeError('audit failure')
    with monkeypatch.context() as m:
        m.setattr(SqlAlchemyAuditRepository,'append',fail)
        with pytest.raises(RuntimeError):call()
    assert counts(pgfx)==before
    result=call();assert call()==result
    zero(pgfx)


def test_reconciliation_classifies_and_detects_historical_leakage_without_repair(pgfx):
    before=pgfx.service.reconcile(include_classification=True)['classification']
    b=batch(pgfx);r=candidate(pgfx,b)
    candidate(pgfx,b,source_row='verify',verification_state='VERIFY')
    candidate(pgfx,b,source_row='verified',verification_state='VERIFIED')
    draft(pgfx)
    p=post(pgfx,draft(pgfx,day=CUTOVER))
    result=pgfx.service.reconcile(include_classification=True)
    assert result['ok']
    for key in ('live_posted','draft','imported_HISTORY','imported_VERIFY','imported_VERIFIED_nonposting'):
        assert result['classification'][key]==before[key]+1
    from greenhouse_owner_os.services import reconciliation as rec
    models=[rec.ObligationRow,rec.PaymentRow,rec.PaymentAllocationRow,rec.AdvanceRow,rec.ApplicationRow,
            rec.PostingRow,rec.PostingEntryRow,rec.LedgerAccountRow,rec.MoneyAccountRow,rec.CounterpartyRow,
            rec.TransferRow,rec.FundingRow,rec.SettlementRow,rec.EntityRow,rec.ImportBatchRow,rec.HistoricalCandidateRow]
    with pgfx.session_factory() as s:
        data={m:{row.id:row for row in s.scalars(select(m))} for m in models}
        s.expunge_all()
    old=deepcopy(data)
    data[PostingRow][UUID(p['posting_id'])].source_id=UUID(r['id'])
    findings=check_snapshot(data)['findings']
    assert any(f['code']=='historical_ledger_effect' for f in findings)
    assert check_snapshot(data)['findings']==findings
    data=old
    data[PostingRow][UUID(p['posting_id'])].event_date=OLD
    assert any(f['code']=='pre_cutover_live_posting' for f in check_snapshot(data)['findings'])
    assert pgfx.service.reconcile(include_classification=True)==result


@pytest.mark.parametrize('kind',['transfer','owner_funding','direct_settlement'])
def test_cash_posting_cutover_guards(cashpg,kind):
    from tests.cash_cases import create, post as post_cash, transfer, funding, settlement
    fx=cashpg;cmd={'transfer':transfer,'owner_funding':funding,'direct_settlement':settlement}[kind](fx)
    cmd=cmd.model_copy(update={'event_date':CUTOVER-timedelta(days=1)})
    if kind=='direct_settlement':
        # Its own source-date rule also rejects a settlement before live obligations.
        from greenhouse_owner_os.domain.errors import ValidationError
        with pytest.raises(ValidationError):create(fx,kind,cmd)
        return
    r=create(fx,kind,cmd)
    before=counts(fx)
    from greenhouse_owner_os.domain.errors import ValidationError
    with pytest.raises(ValidationError,match='pre-cut-over'):post_cash(fx,kind,r)
    assert counts(fx)==before
