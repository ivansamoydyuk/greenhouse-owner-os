from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text, func
from sqlalchemy.exc import DBAPIError
from greenhouse_owner_os.domain.errors import ConflictError, ValidationError
from greenhouse_owner_os.infrastructure.models import AuditEventRow, PostingRow, PostingEntryRow, PaymentRow, PaymentAllocationRow, AdvanceRow, IdempotencyRow, ObligationRow, LedgerAccountRow, MoneyAccountRow
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyAuditRepository
from tests.finance_integrity_cases import CASES, obligation, payment, reverse, DAY

@pytest.mark.parametrize("case", CASES, ids=lambda case: case.__name__)
def test_postgres_integrity_acceptance(pgfx, case):
    case(pgfx)


def counts(pgfx):
    with pgfx.session_factory() as s:
        return [s.scalar(select(func.count()).select_from(model)) for model in (PostingRow,PostingEntryRow,AuditEventRow,IdempotencyRow,PaymentAllocationRow,AdvanceRow)]


def test_postgres_failed_reversal_atomic_and_retry(pgfx,monkeypatch):
    ob=obligation(pgfx);pay=payment(pgfx,[(ob["id"],"10000")],"12000")
    before=counts(pgfx)
    def fail(*args,**kwargs): raise RuntimeError("Injected audit failure after journal and lifecycle writes")
    with monkeypatch.context() as m:
        m.setattr(SqlAlchemyAuditRepository,"append",fail)
        with pytest.raises(RuntimeError): reverse(pgfx,"payment",pay,key="retry-failed")
    assert counts(pgfx)==before
    assert pgfx.service.get_linkage("payment",UUID(pay["id"]))["status"]=="posted"
    assert pgfx.service.get_obligation(UUID(ob["id"]))["remaining_amount"]=="0.00"
    with pgfx.session_factory() as s:
        assert s.scalar(select(AdvanceRow.status).where(AdvanceRow.source_payment_id==UUID(pay["id"])))=="open"
    reverse(pgfx,"payment",pay,key="retry-failed")


@pytest.mark.parametrize("same_key",[True,False])
def test_postgres_concurrent_reversal_once(pgfx,same_key):
    ob=obligation(pgfx);pay=payment(pgfx,[(ob["id"],"4000")])
    def call(i):
        try: return reverse(pgfx,"payment",pay,key="concurrent" if same_key else f"concurrent-{i}")
        except ConflictError: return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(call,range(2)))
    if same_key: assert results[0]==results[1]
    else: assert sum(r=="conflict" for r in results)==1
    with pgfx.session_factory() as s:
        assert s.scalar(select(func.count()).select_from(PostingRow).where(PostingRow.reversal_of_id==UUID(pay["posting_id"])))==1
    assert pgfx.service.get_obligation(UUID(ob["id"]))["remaining_amount"]=="10000.00"


@pytest.mark.parametrize("sql",[
    "UPDATE finance.payments SET amount=1 WHERE id=:pay",
    "UPDATE finance.payments SET created_by=:actor, posted_at=now() WHERE id=:pay",
    "UPDATE finance.payments SET version=99 WHERE id=:pay",
    "DELETE FROM finance.payments WHERE id=:pay",
    "UPDATE finance.obligations SET original_amount=1 WHERE id=:ob",
    "DELETE FROM finance.obligations WHERE id=:ob",
    "UPDATE finance.payment_allocations SET amount=1 WHERE payment_id=:pay",
    "DELETE FROM finance.payment_allocations WHERE payment_id=:pay",
    "UPDATE finance.payment_allocations SET payment_id=:draft WHERE payment_id=:pay",
    "UPDATE finance.postings SET currency='USD' WHERE id=:posting",
    "DELETE FROM finance.postings WHERE id=:posting",
    "UPDATE finance.posting_entries SET amount=1 WHERE posting_id=:posting",
    "DELETE FROM finance.posting_entries WHERE posting_id=:posting",
    "INSERT INTO finance.posting_entries(posting_id,ledger_account_id,side,amount) VALUES (:posting,:offset,'debit',1),(:posting,:offset,'credit',1)",
    "UPDATE finance.advances SET original_amount=1 WHERE source_payment_id=:pay",
    "DELETE FROM finance.advances WHERE source_payment_id=:pay",
    "UPDATE finance.payments SET status='reversed',reversed_at=now() WHERE id=:pay",
    "UPDATE finance.ledger_accounts SET currency='USD' WHERE id=:offset",
    "UPDATE finance.money_accounts SET currency='USD' WHERE id=:account",
])
def test_postgres_immutable_history_sql(pgfx,sql):
    ob=obligation(pgfx);pay=payment(pgfx,[(ob["id"],"4000")],"5000")
    draft=payment(pgfx,(),"100",post=False)
    before=counts(pgfx)
    params=dict(pay=UUID(pay["id"]),ob=UUID(ob["id"]),posting=UUID(pay["posting_id"]),offset=pgfx.offset_account_id,account=pgfx.money_account_id,draft=UUID(draft["id"]),actor=pgfx.actor_id)
    with pgfx.session_factory() as s:
        with pytest.raises(DBAPIError):
            s.execute(text(sql),params);s.commit()
        s.rollback()
    assert counts(pgfx)==before


def test_postgres_reverse_audit_metadata_and_reversed_immutability(pgfx):
    pay=payment(pgfx,(),"100")
    result=reverse(pgfx,"payment",pay)
    with pgfx.session_factory() as s:
        event=s.scalar(select(AuditEventRow).where(AuditEventRow.entity_id==UUID(pay["id"]),AuditEventRow.action=="reverse"))
        assert event.actor_id==pgfx.actor_id and event.occurred_at and event.reason==result["reason"]
        with pytest.raises(DBAPIError):
            s.execute(text("UPDATE finance.payments SET reversal_reason='changed' WHERE id=:id"),{"id":UUID(pay["id"])});s.commit()
        s.rollback()


def seed_usd(fx):
    if hasattr(fx,"store"):
        for code in ("AP_CONTROL","AR_CONTROL","SUPPLIER_ADVANCE","CUSTOMER_ADVANCE"):
            fx.store.seed_ledger_account(entity_id=fx.entity_id,currency="USD",system_code=code)
        offset=fx.store.seed_ledger_account(entity_id=fx.entity_id,currency="USD")
        account,_=fx.store.seed_money_account(entity_id=fx.entity_id,currency="USD")
    else:
        offset,ledger,account=uuid4(),uuid4(),uuid4()
        with fx.session_factory.begin() as s:
            for code,klass,side in (("AP_CONTROL","payable","credit"),("AR_CONTROL","receivable","debit"),("SUPPLIER_ADVANCE","advance","debit"),("CUSTOMER_ADVANCE","advance","credit")):
                s.add(LedgerAccountRow(entity_id=fx.entity_id,code=code+"USD",name=code,account_class=klass,normal_side=side,currency="USD",system_code=code))
            s.add_all([LedgerAccountRow(id=offset,entity_id=fx.entity_id,code="USD_COST",name="USD Cost",account_class="expense",normal_side="debit",currency="USD"),LedgerAccountRow(id=ledger,entity_id=fx.entity_id,code="USD_BANK",name="USD Bank",account_class="cash",normal_side="debit",currency="USD")]);s.flush()
            s.add(MoneyAccountRow(id=account,entity_id=fx.entity_id,ledger_account_id=ledger,account_type="bank",name="USD Bank",currency="USD"))
    return offset,account


def test_postgres_currency_isolation_and_reversal(pgfx):
    offset,account=seed_usd(pgfx)
    tr=obligation(pgfx,"10000")
    usd=obligation(pgfx,"200",currency="USD",offset=offset)
    balances=pgfx.service.get_currency_balances(pgfx.entity_id)
    assert set(balances["balances"])=={"TRY","USD"} and "total" not in balances
    assert balances["balances"]["TRY"]["payable"]=="10000.00"
    assert balances["balances"]["USD"]["payable"]=="200.00"
    bad=payment(pgfx,[(usd["id"],"100")],"100",post=False)
    before=counts(pgfx)
    with pytest.raises(ValidationError,match="currency mismatch"):
        pgfx.service.post_payment(actor_id=pgfx.actor_id,idempotency_key="cross-currency",payment_id=UUID(bad["id"]),expected_version=1)
    assert counts(pgfx)==before
    good=payment(pgfx,[(usd["id"],"100")],"150",currency="USD",account=account)
    assert reverse(pgfx,"payment",good)["currency"]=="USD"
    balances=pgfx.service.get_currency_balances(pgfx.entity_id)["balances"]
    assert balances["USD"]["cash"]=="0.00" and balances["USD"]["payable"]=="200.00"
    assert balances["TRY"]["payable"]=="10000.00"
    # Independent SQL validation rejects cross-currency allocation at posting.
    with pgfx.session_factory() as s:
        with pytest.raises(DBAPIError):
            s.execute(text("UPDATE finance.payments SET status='posted',posting_id=:posting,posted_at=now(),version=2 WHERE id=:id"),{"id":UUID(bad["id"]),"posting":UUID(tr["posting_id"])});s.commit()
        s.rollback()
    # A balanced journal with an account in the wrong currency fails at commit.
    with pgfx.session_factory() as s:
        p=PostingRow(entity_id=pgfx.entity_id,currency="TRY",event_date=DAY,posting_type="test",source_type="test",source_id=uuid4(),created_by=pgfx.actor_id)
        s.add(p);s.flush()
        s.add_all([PostingEntryRow(posting_id=p.id,ledger_account_id=offset,side="debit",amount=1),PostingEntryRow(posting_id=p.id,ledger_account_id=pgfx.offset_account_id,side="credit",amount=1)])
        with pytest.raises(DBAPIError): s.commit()
        s.rollback()


def test_postgres_balanced_but_inexact_reversal_rolls_back(pgfx):
    pay=payment(pgfx,(),"100")
    before=counts(pgfx)
    with pgfx.session_factory() as s:
        original=s.get(PostingRow,UUID(pay["posting_id"]))
        lines=s.scalars(select(PostingEntryRow).where(PostingEntryRow.posting_id==original.id)).all()
        p=PostingRow(entity_id=pgfx.entity_id,currency="TRY",event_date=DAY,posting_type="payment_reversed",source_type="payment",source_id=UUID(pay["id"]),created_by=pgfx.actor_id,reversal_of_id=original.id,reason="wrong amount")
        s.add(p);s.flush()
        for line in lines: s.add(PostingEntryRow(posting_id=p.id,ledger_account_id=line.ledger_account_id,side="credit" if line.side=="debit" else "debit",amount=50))
        s.flush()
        s.execute(text("UPDATE finance.payments SET status='reversed',version=version+1,reversal_posting_id=:pid,reversed_by=:actor,reversed_at=now(),reversal_reason='wrong amount' WHERE id=:id"),{"pid":p.id,"actor":pgfx.actor_id,"id":UUID(pay["id"])})
        s.execute(text("UPDATE finance.advances SET status='reversed' WHERE source_payment_id=:id"),{"id":UUID(pay["id"])})
        with pytest.raises(DBAPIError): s.commit()
        s.rollback()
    assert counts(pgfx)==before
    assert pgfx.service.get_linkage("payment",UUID(pay["id"]))["status"]=="posted"
