"""Shared acceptance cases executed with memory and real PostgreSQL UoWs."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from greenhouse_owner_os.domain.commands import AllocationInput, CreateObligationCommand, CreatePaymentCommand, ReverseCommand
from greenhouse_owner_os.domain.errors import ConflictError, IdempotencyConflictError, ValidationError

DAY = date(2026, 10, 4)


def obligation(fx, amount="10000", direction="payable", replacement=None, currency="TRY", offset=None):
    created = fx.service.create_obligation(actor_id=fx.actor_id, idempotency_key=str(uuid4()),
        command=CreateObligationCommand(entity_id=fx.entity_id, counterparty_id=fx.counterparty_id,
            direction=direction, original_amount=Decimal(amount), currency=currency, event_date=DAY,
            offset_ledger_account_id=offset or fx.offset_account_id, replacement_of_id=replacement))
    return fx.service.post_obligation(actor_id=fx.actor_id, idempotency_key=str(uuid4()),
        obligation_id=UUID(created["id"]), expected_version=created["version"])


def payment(fx, allocations=(), amount="4000", direction="out", replacement=None, currency="TRY", account=None, post=True):
    created = fx.service.create_payment(actor_id=fx.actor_id, idempotency_key=str(uuid4()),
        command=CreatePaymentCommand(entity_id=fx.entity_id, counterparty_id=fx.counterparty_id,
            money_account_id=account or fx.money_account_id, direction=direction, amount=Decimal(amount),
            currency=currency, event_date=DAY, replacement_of_id=replacement,
            allocations=tuple(AllocationInput(obligation_id=UUID(oid), amount=Decimal(a)) for oid,a in allocations)))
    if not post:
        return created
    return fx.service.post_payment(actor_id=fx.actor_id, idempotency_key=str(uuid4()),
        payment_id=UUID(created["id"]), expected_version=created["version"])


def reverse(fx, kind, record, key=None, reason="Correct mistaken transaction", version=None):
    kwargs = {kind+"_id": UUID(record["id"])}
    return getattr(fx.service, "reverse_"+kind)(actor_id=fx.actor_id, idempotency_key=key or str(uuid4()),
        command=ReverseCommand(expected_version=version or record["version"], event_date=DAY, reason=reason), **kwargs)


def partial(fx):
    ob=obligation(fx); pay=payment(fx, [(ob["id"], "4000")])
    assert fx.service.get_obligation(UUID(ob["id"]))["remaining_amount"] == "6000.00"
    with fx.service.uow_factory() as uow:
        original=uow.finance.get_payment(UUID(pay["id"]))
        journal=uow.finance.get_posting(UUID(pay["posting_id"]))
    result=reverse(fx,"payment",pay)
    assert fx.service.get_obligation(UUID(ob["id"]))["remaining_amount"] == "10000.00"
    assert result["reversed_by"] == str(fx.actor_id)
    assert result["reversed_at"] and result["reason"] == "Correct mistaken transaction"
    assert result["original_posting_id"] == pay["posting_id"]
    with fx.service.uow_factory() as uow:
        current=uow.finance.get_payment(UUID(pay["id"]))
        unchanged=uow.finance.get_posting(UUID(pay["posting_id"]))
        inverse=uow.finance.get_posting(UUID(result["reversal_posting_id"]))
        assert unchanged == journal
        assert current.allocations == original.allocations
        assert current.amount == original.amount and current.posted_at == original.posted_at
        assert inverse.reversal_of_id == journal.id and inverse.currency == "TRY"
        assert sorted((str(l.ledger_account_id),l.side.value,l.amount) for l in inverse.lines) == sorted((str(l.ledger_account_id), "credit" if l.side.value=="debit" else "debit",l.amount) for l in journal.lines)
    assert fx.service.get_currency_balances(fx.entity_id)["balances"]["TRY"]["cash"] == "0.00"


def split(fx):
    a=obligation(fx,"3000");b=obligation(fx,"7000")
    pay=payment(fx,[(a["id"],"3000"),(b["id"],"7000")],"10000")
    result=reverse(fx,"payment",pay)
    assert result["obligation_balances"] == {a["id"]:"3000.00",b["id"]:"7000.00"}


def advance(fx):
    ob=obligation(fx);pay=payment(fx,[(ob["id"],"10000")],"12000")
    assert fx.service.get_currency_balances(fx.entity_id)["balances"]["TRY"]["advance_paid"] == "2000.00"
    reverse(fx,"payment",pay)
    with fx.service.uow_factory() as uow:
        a=uow.finance.get_advance_by_payment(UUID(pay["id"]))
        assert a.status=="reversed" and a.original_amount==Decimal("2000")
    balances=fx.service.get_currency_balances(fx.entity_id)["balances"]["TRY"]
    assert balances["cash"]==balances["advance_paid"]=="0.00"
    assert balances["payable"]=="10000.00"


def idempotency(fx):
    pay=payment(fx,(),"100")
    a=reverse(fx,"payment",pay,key="reversal-key")
    assert a==reverse(fx,"payment",pay,key="reversal-key")
    with pytest.raises(IdempotencyConflictError):
        reverse(fx,"payment",pay,key="reversal-key",reason="Another request")
    with pytest.raises(ConflictError,match="already reversed"):
        reverse(fx,"payment",pay,key="different-key")
    assert fx.service.get_linkage("payment",UUID(pay["id"]))["reversal_posting_id"]==a["reversal_posting_id"]


def obligation_reversal(fx):
    for direction in ("payable","receivable"):
        ob=obligation(fx,direction=direction)
        result=reverse(fx,"obligation",ob,key="reverse-ob-"+direction)
        assert result["currency"]=="TRY"
        assert fx.service.get_obligation(UUID(ob["id"]))["remaining_amount"]=="0.00"
        assert fx.service.get_currency_balances(fx.entity_id)["balances"].get("TRY",{}).get(direction,"0.00")=="0.00"
        assert result==reverse(fx,"obligation",ob,key="reverse-ob-"+direction)
        with pytest.raises(ConflictError): reverse(fx,"obligation",ob)


def obligation_allocated(fx):
    ob=obligation(fx);pay=payment(fx,[(ob["id"],"4000")])
    with pytest.raises(ValidationError,match="active allocated"):
        reverse(fx,"obligation",ob)
    assert fx.service.get_obligation(UUID(ob["id"]))["status"]=="posted"
    reverse(fx,"payment",pay)
    reverse(fx,"obligation",ob)
    assert fx.service.get_currency_balances(fx.entity_id)["balances"]["TRY"]["payable"]=="0.00"


def replacement(fx):
    ob=obligation(fx);pay=payment(fx,[(ob["id"],"4000")])
    with pytest.raises(ValidationError,match="reversed original"):
        payment(fx,[(ob["id"],"3000")],"3000",replacement=UUID(pay["id"]))
    reverse(fx,"payment",pay)
    corrected=payment(fx,[(ob["id"],"3000")],"3000",replacement=UUID(pay["id"]))
    assert corrected["replacement_of_id"]==pay["id"]
    assert fx.service.get_linkage("payment",UUID(pay["id"]))["replacement_id"]==corrected["id"]
    assert fx.service.get_obligation(UUID(ob["id"]))["remaining_amount"]=="7000.00"
    with pytest.raises(ConflictError): payment(fx,(),"100",replacement=UUID(pay["id"]))
    reverse(fx,"payment",corrected)
    reverse(fx,"obligation",ob)
    corrected_ob=obligation(fx,"9000",replacement=UUID(ob["id"]))
    assert fx.service.get_linkage("obligation",UUID(ob["id"]))["replacement_id"]==corrected_ob["id"]


def incoming(fx):
    ob=obligation(fx,direction="receivable")
    pay=payment(fx,[(ob["id"],"10000")],"12000",direction="in")
    balances=fx.service.get_currency_balances(fx.entity_id)["balances"]["TRY"]
    assert balances["cash"]=="12000.00" and balances["advance_received"]=="2000.00"
    reverse(fx,"payment",pay)
    balances=fx.service.get_currency_balances(fx.entity_id)["balances"]["TRY"]
    assert balances["cash"]==balances["advance_received"]=="0.00"
    assert balances["receivable"]=="10000.00"


def validation(fx):
    draft=payment(fx,(),"100",post=False)
    with pytest.raises(ValidationError,match="only posted"):
        reverse(fx,"payment",draft)
    pay=payment(fx,(),"100")
    with pytest.raises(ConflictError,match="version"):
        reverse(fx,"payment",pay,version=99)
    with pytest.raises(ValueError): ReverseCommand(expected_version=2,event_date=DAY,reason="  ")
    with pytest.raises(ValidationError,match="precede"):
        fx.service.reverse_payment(actor_id=fx.actor_id,idempotency_key=str(uuid4()),payment_id=UUID(pay["id"]),command=ReverseCommand(expected_version=2,event_date=date(2026,10,3),reason="wrong date"))


CASES=[partial,split,advance,idempotency,obligation_reversal,obligation_allocated,replacement,incoming,validation]
