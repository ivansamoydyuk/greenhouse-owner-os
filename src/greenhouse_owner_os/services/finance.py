from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable
from uuid import UUID, uuid4

from greenhouse_owner_os.domain.cash import CASH_TYPES, serialize_cash
from greenhouse_owner_os.domain.commands import CreateObligationCommand, CreatePaymentCommand, ReverseCommand
from greenhouse_owner_os.domain.errors import ConflictError, IdempotencyConflictError, NotFoundError, ValidationError
from greenhouse_owner_os.domain.finance import (
    Advance,
    AdvanceDirection,
    AllocationRequest,
    FinancialPosting,
    Obligation,
    ObligationDirection,
    Payment,
    PaymentDirection,
    PostingLine,
    PostingSide,
    RecordStatus,
    money,
)
from greenhouse_owner_os.repositories.protocols import UnitOfWork

UowFactory = Callable[[], UnitOfWork]


def _hash_request(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _serialize_obligation(obligation: Obligation, remaining: Decimal | None = None) -> dict[str, Any]:
    data = {
        "id": str(obligation.id),
        "entity_id": str(obligation.entity_id),
        "counterparty_id": str(obligation.counterparty_id),
        "direction": obligation.direction.value,
        "original_amount": f"{obligation.original_amount:.2f}",
        "currency": obligation.currency,
        "event_date": obligation.event_date.isoformat(),
        "due_date": obligation.due_date.isoformat() if obligation.due_date else None,
        "obligation_class": obligation.obligation_class, "economic_source": obligation.economic_source,
        "description": obligation.description,
        "attributions": [dict(work_area_id=str(a.work_area_id),amount=f"{a.amount:.2f}") if hasattr(a,"work_area_id") else a for a in obligation.attributions],
        "status": obligation.status.value,
        "version": obligation.version,
        "reversal_posting_id": str(obligation.reversal_posting_id) if obligation.reversal_posting_id else None,
        "replacement_of_id": str(obligation.replacement_of_id) if obligation.replacement_of_id else None,
        "posting_id": str(obligation.posting_id) if obligation.posting_id else None,
    }
    if remaining is not None:
        data["remaining_amount"] = f"{money(remaining):.2f}"
    return data


def _serialize_payment(payment: Payment, advance: Advance | None = None) -> dict[str, Any]:
    return {
        "id": str(payment.id),
        "entity_id": str(payment.entity_id),
        "money_account_id": str(payment.money_account_id),
        "counterparty_id": str(payment.counterparty_id),
        "direction": payment.direction.value,
        "amount": f"{payment.amount:.2f}",
        "currency": payment.currency,
        "event_date": payment.event_date.isoformat(),
        "status": payment.status.value,
        "version": payment.version,
        "posting_id": str(payment.posting_id) if payment.posting_id else None,
        "reversal_posting_id": str(payment.reversal_posting_id) if payment.reversal_posting_id else None,
        "replacement_of_id": str(payment.replacement_of_id) if payment.replacement_of_id else None,
        "source": payment.source,
        "reason": payment.reason,
        "allocation_total": f"{payment.allocation_total:.2f}",
        "unallocated_amount": f"{payment.unallocated_amount:.2f}",
        "advance_id": str(advance.id) if advance else None,
        "allocations": [
            {"id": str(row.id), "obligation_id": str(row.obligation_id), "amount": f"{row.amount:.2f}"}
            for row in payment.allocations
        ],
    }


from .cash import CashServiceMixin


from .history import HistoryServiceMixin
from .opening import OpeningServiceMixin


class FinanceService(CashServiceMixin, HistoryServiceMixin, OpeningServiceMixin):
    def __init__(self, uow_factory: UowFactory) -> None:
        self.uow_factory = uow_factory

    def create_obligation(self, *, actor_id: UUID, idempotency_key: str, command: CreateObligationCommand) -> dict[str, Any]:
        operation = "create_obligation"
        payload = command.model_dump(mode="json")
        request_hash = _hash_request(payload)
        with self.uow_factory() as uow:
            uow.idempotency.lock(actor_id, operation, idempotency_key)
            replay = self._replay_if_exists(uow, actor_id, operation, idempotency_key, request_hash)
            if replay:
                return replay
            obligation = Obligation.draft(
                entity_id=command.entity_id,
                counterparty_id=command.counterparty_id,
                direction=command.direction,
                original_amount=command.original_amount,
                currency=command.currency,
                event_date=command.event_date,
                due_date=command.due_date,
                offset_ledger_account_id=command.offset_ledger_account_id,
            )
            uow.finance.assert_ledger_account_scope(obligation.offset_ledger_account_id, obligation.entity_id, obligation.currency)
            obligation.obligation_class=command.obligation_class
            obligation.economic_source=command.economic_source
            obligation.description=command.description
            obligation.attributions=command.attributions
            if obligation.direction == ObligationDirection.RECEIVABLE:
                if obligation.description and any(w in obligation.description.casefold() for w in ('банан','banana')):
                    raise ValidationError('banana sale requires authoritative harvest/depot settlement')
                uow.finance.assert_manual_receivable_boundary(obligation.counterparty_id)
            if obligation.economic_source == "harvest_depot":
                raise ValidationError("banana sale requires authoritative harvest/depot settlement")
            if obligation.obligation_class == "STRATEGIC":
                uow.finance.assert_strategic_basis(obligation)
            uow.finance.assert_regular_offset(obligation.offset_ledger_account_id)
            self._validate_replacement(uow, "obligation", command.replacement_of_id, obligation)
            obligation.replacement_of_id = command.replacement_of_id
            uow.finance.add_obligation(obligation, actor_id)
            if obligation.attributions:
                uow.finance.add_attributions(obligation, actor_id)
            response = _serialize_obligation(obligation, obligation.original_amount)
            uow.audit.append(actor_id=actor_id, action="create", entity_type="obligation", entity_id=obligation.id, before=None, after=response)
            uow.idempotency.put(actor_id=actor_id, operation=operation, key=idempotency_key, request_hash=request_hash, resource_type="obligation", resource_id=obligation.id, response=response)
            uow.commit()
            return response

    def post_obligation(self, *, actor_id: UUID, idempotency_key: str, obligation_id: UUID, expected_version: int) -> dict[str, Any]:
        operation = "post_obligation"
        payload = {"obligation_id": str(obligation_id), "expected_version": expected_version}
        request_hash = _hash_request(payload)
        with self.uow_factory() as uow:
            uow.idempotency.lock(actor_id, operation, idempotency_key)
            replay = self._replay_if_exists(uow, actor_id, operation, idempotency_key, request_hash)
            if replay:
                return replay
            obligation = uow.finance.get_obligation(obligation_id, for_update=True)
            if not obligation:
                raise NotFoundError("obligation not found")
            if obligation.version != expected_version:
                raise ConflictError("obligation version conflict")
            if obligation.status not in {RecordStatus.DRAFT, RecordStatus.REVIEW}:
                raise ValidationError("only draft/review obligation can be posted")
            uow.finance.assert_live_date(obligation.entity_id, obligation.event_date)
            uow.finance.assert_ledger_account_scope(obligation.offset_ledger_account_id, obligation.entity_id, obligation.currency)
            if obligation.economic_source == "harvest_depot":
                raise ValidationError("banana sale requires authoritative harvest/depot settlement")
            if obligation.obligation_class == "STRATEGIC":
                uow.finance.assert_strategic_basis(obligation)
            control_code = "AP_CONTROL" if obligation.direction == ObligationDirection.PAYABLE else "AR_CONTROL"
            control_id = uow.finance.get_system_ledger_account(obligation.entity_id, obligation.currency, control_code)
            posting_id = uuid4()
            if obligation.direction == ObligationDirection.PAYABLE:
                lines = [
                    PostingLine(obligation.offset_ledger_account_id, PostingSide.DEBIT, obligation.original_amount),
                    PostingLine(control_id, PostingSide.CREDIT, obligation.original_amount),
                ]
            else:
                lines = [
                    PostingLine(control_id, PostingSide.DEBIT, obligation.original_amount),
                    PostingLine(obligation.offset_ledger_account_id, PostingSide.CREDIT, obligation.original_amount),
                ]
            posting = FinancialPosting(
                id=posting_id,
                entity_id=obligation.entity_id,
                currency=obligation.currency,
                event_date=obligation.event_date,
                posting_type="obligation_posted",
                source_type="obligation",
                source_id=obligation.id,
                created_by=actor_id,
                lines=lines,
            )
            posting.validate_balanced()
            before = _serialize_obligation(obligation, obligation.original_amount)
            uow.finance.add_posting(posting)
            obligation.post(posting_id)
            uow.finance.save_obligation(obligation)
            response = _serialize_obligation(obligation, obligation.original_amount)
            uow.audit.append(actor_id=actor_id, action="post", entity_type="obligation", entity_id=obligation.id, before=before, after=response)
            uow.idempotency.put(actor_id=actor_id, operation=operation, key=idempotency_key, request_hash=request_hash, resource_type="obligation", resource_id=obligation.id, response=response)
            uow.commit()
            return response

    def create_payment(self, *, actor_id: UUID, idempotency_key: str, command: CreatePaymentCommand) -> dict[str, Any]:
        operation = "create_payment"
        payload = command.model_dump(mode="json")
        request_hash = _hash_request(payload)
        with self.uow_factory() as uow:
            uow.idempotency.lock(actor_id, operation, idempotency_key)
            replay = self._replay_if_exists(uow, actor_id, operation, idempotency_key, request_hash)
            if replay:
                return replay
            payment = Payment.draft(
                entity_id=command.entity_id,
                money_account_id=command.money_account_id,
                counterparty_id=command.counterparty_id,
                direction=command.direction,
                amount=command.amount,
                currency=command.currency,
                event_date=command.event_date,
                allocations=[AllocationRequest(item.obligation_id, item.amount) for item in command.allocations],
            )
            uow.finance.get_money_account_ledger(payment.money_account_id, payment.entity_id, payment.currency)
            self._validate_replacement(uow, "payment", command.replacement_of_id, payment)
            payment.replacement_of_id = command.replacement_of_id
            payment.source = command.source
            payment.reason = command.reason
            uow.finance.add_payment(payment, actor_id)
            response = _serialize_payment(payment)
            uow.audit.append(actor_id=actor_id, action="create", entity_type="payment", entity_id=payment.id, before=None, after=response)
            uow.idempotency.put(actor_id=actor_id, operation=operation, key=idempotency_key, request_hash=request_hash, resource_type="payment", resource_id=payment.id, response=response)
            uow.commit()
            return response

    def post_payment(self, *, actor_id: UUID, idempotency_key: str, payment_id: UUID, expected_version: int) -> dict[str, Any]:
        operation = "post_payment"
        payload = {"payment_id": str(payment_id), "expected_version": expected_version}
        request_hash = _hash_request(payload)
        with self.uow_factory() as uow:
            uow.idempotency.lock(actor_id, operation, idempotency_key)
            replay = self._replay_if_exists(uow, actor_id, operation, idempotency_key, request_hash)
            if replay:
                return replay
            payment = uow.finance.get_payment(payment_id, for_update=True)
            if not payment:
                raise NotFoundError("payment not found")
            if payment.version != expected_version:
                raise ConflictError("payment version conflict")
            if payment.status not in {RecordStatus.DRAFT, RecordStatus.REVIEW}:
                raise ValidationError("only draft/review payment can be posted")
            uow.finance.assert_live_date(payment.entity_id, payment.event_date)
            if payment.allocation_total > payment.amount:
                raise ValidationError("allocation total cannot exceed payment amount")

            # Stable lock order is intentional: it prevents deadlock patterns when one payment spans several obligations.
            obligations: dict[UUID, Obligation] = {}
            for obligation_id in sorted({row.obligation_id for row in payment.allocations}, key=str):
                obligation = uow.finance.get_obligation(obligation_id, for_update=True)
                if not obligation:
                    raise NotFoundError(f"obligation {obligation_id} not found")
                obligations[obligation_id] = obligation

            expected_direction = ObligationDirection.PAYABLE if payment.direction == PaymentDirection.OUT else ObligationDirection.RECEIVABLE
            proposed_by_obligation: dict[UUID, Decimal] = {}
            for row in payment.allocations:
                obligation = obligations[row.obligation_id]
                if obligation.status != RecordStatus.POSTED:
                    raise ValidationError("payment can allocate only to posted obligations")
                if obligation.entity_id != payment.entity_id:
                    raise ValidationError("payment and obligation entity mismatch")
                if obligation.counterparty_id != payment.counterparty_id:
                    raise ValidationError("payment and obligation counterparty mismatch")
                if obligation.currency != payment.currency:
                    raise ValidationError("payment and obligation currency mismatch")
                if obligation.direction != expected_direction:
                    raise ValidationError("payment direction does not match obligation direction")
                proposed_by_obligation[row.obligation_id] = money(proposed_by_obligation.get(row.obligation_id, Decimal("0.00")) + row.amount)

            for obligation_id, proposed in proposed_by_obligation.items():
                remaining = uow.finance.obligation_remaining(obligation_id)
                if proposed > remaining:
                    raise ValidationError(f"allocation exceeds obligation remaining amount: {obligation_id}")

            uow.finance.lock_money_accounts([payment.money_account_id])
            uow.finance.assert_counterparty_scope(payment.counterparty_id, payment.entity_id)
            cash_ledger_id = uow.finance.get_money_account_ledger(payment.money_account_id, payment.entity_id, payment.currency)
            allocation_total = payment.allocation_total
            remainder = payment.unallocated_amount
            posting_lines: list[PostingLine] = []
            advance: Advance | None = None

            if payment.direction == PaymentDirection.OUT:
                ap_id = uow.finance.get_system_ledger_account(payment.entity_id, payment.currency, "AP_CONTROL")
                if allocation_total:
                    posting_lines.append(PostingLine(ap_id, PostingSide.DEBIT, allocation_total))
                if remainder:
                    advance_account = uow.finance.get_system_ledger_account(payment.entity_id, payment.currency, "SUPPLIER_ADVANCE")
                    posting_lines.append(PostingLine(advance_account, PostingSide.DEBIT, remainder))
                    advance = Advance(
                        id=uuid4(), entity_id=payment.entity_id, counterparty_id=payment.counterparty_id,
                        source_payment_id=payment.id, direction=AdvanceDirection.PAID,
                        original_amount=remainder, currency=payment.currency, event_date=payment.event_date,
                    )
                posting_lines.append(PostingLine(cash_ledger_id, PostingSide.CREDIT, payment.amount))
            else:
                ar_id = uow.finance.get_system_ledger_account(payment.entity_id, payment.currency, "AR_CONTROL")
                posting_lines.append(PostingLine(cash_ledger_id, PostingSide.DEBIT, payment.amount))
                if allocation_total:
                    posting_lines.append(PostingLine(ar_id, PostingSide.CREDIT, allocation_total))
                if remainder:
                    advance_account = uow.finance.get_system_ledger_account(payment.entity_id, payment.currency, "CUSTOMER_ADVANCE")
                    posting_lines.append(PostingLine(advance_account, PostingSide.CREDIT, remainder))
                    advance = Advance(
                        id=uuid4(), entity_id=payment.entity_id, counterparty_id=payment.counterparty_id,
                        source_payment_id=payment.id, direction=AdvanceDirection.RECEIVED,
                        original_amount=remainder, currency=payment.currency, event_date=payment.event_date,
                    )

            posting = FinancialPosting(
                id=uuid4(),
                entity_id=payment.entity_id,
                currency=payment.currency,
                event_date=payment.event_date,
                posting_type="payment_posted",
                source_type="payment",
                source_id=payment.id,
                created_by=actor_id,
                lines=posting_lines,
            )
            posting.validate_balanced()
            before = _serialize_payment(payment)
            uow.finance.add_posting(posting)
            if advance:
                uow.finance.add_advance(advance)
            payment.post(posting.id)
            uow.finance.save_payment(payment)
            response = _serialize_payment(payment, advance)
            response["obligation_balances"] = {
                str(obligation_id): f"{uow.finance.obligation_remaining(obligation_id):.2f}"
                for obligation_id in obligations
            }
            uow.audit.append(actor_id=actor_id, action="post", entity_type="payment", entity_id=payment.id, before=before, after=response)
            uow.idempotency.put(actor_id=actor_id, operation=operation, key=idempotency_key, request_hash=request_hash, resource_type="payment", resource_id=payment.id, response=response)
            uow.commit()
            return response

    def get_obligation(self, obligation_id: UUID) -> dict[str, Any]:
        with self.uow_factory() as uow:
            obligation = uow.finance.get_obligation(obligation_id)
            if not obligation:
                raise NotFoundError("obligation not found")
            response = _serialize_obligation(obligation, uow.finance.obligation_remaining(obligation.id))
            opening = uow.finance.get_opening_payable(obligation.id)
            if opening:
                response['opening_provenance'] = opening
            return response

    @staticmethod
    def _validate_replacement(uow, kind, original_id, replacement):
        if original_id is None:
            return
        getter = uow.finance.get_payment if kind == "payment" else uow.finance.get_obligation
        original = getter(original_id, for_update=True)
        if not original:
            raise NotFoundError("replacement original not found")
        if original.status != RecordStatus.REVERSED:
            raise ValidationError("replacement requires a reversed original")
        if (original.entity_id, original.counterparty_id, original.currency, original.direction) != (replacement.entity_id, replacement.counterparty_id, replacement.currency, replacement.direction):
            raise ValidationError("replacement scope/currency/direction must match original")
        if uow.finance.replacement_id(kind, original_id):
            raise ConflictError("original already has a replacement")

    def reverse_payment(self, *, actor_id: UUID, idempotency_key: str, payment_id: UUID, command: ReverseCommand):
        return self._reverse("payment", payment_id, actor_id, idempotency_key, command)

    def reverse_obligation(self, *, actor_id: UUID, idempotency_key: str, obligation_id: UUID, command: ReverseCommand):
        return self._reverse("obligation", obligation_id, actor_id, idempotency_key, command)

    def _reverse(self, kind, resource_id, actor_id, key, command):
        operation = "reverse_" + kind
        request_hash = _hash_request({"id": str(resource_id), **command.model_dump(mode="json")})
        with self.uow_factory() as uow:
            uow.idempotency.lock(actor_id, operation, key)
            replay = self._replay_if_exists(uow, actor_id, operation, key, request_hash)
            if replay:
                return replay
            getter = (lambda rid, **kw: uow.finance.get_cash_record(kind, rid, **kw)) if kind in CASH_TYPES else (uow.finance.get_payment if kind == "payment" else uow.finance.get_obligation)
            record = getter(resource_id, for_update=True)
            if not record:
                raise NotFoundError(kind + " not found")
            if record.status == RecordStatus.REVERSED:
                raise ConflictError(kind + " already reversed")
            if record.version != command.expected_version:
                raise ConflictError(kind + " version conflict")
            if record.status != RecordStatus.POSTED:
                raise ValidationError("only posted records can be reversed")
            if command.event_date < record.event_date:
                raise ValidationError("reversal date cannot precede original event date")
            uow.finance.assert_live_date(record.entity_id, command.event_date)
            if kind == "payment":
                for oid in sorted({a.obligation_id for a in record.allocations}, key=str):
                    uow.finance.get_obligation(oid, for_update=True)
                uow.finance.lock_money_accounts([record.money_account_id])
            elif kind == 'advance_application':
                self._application_sources(uow.finance, record, lock=True)
            elif kind == 'direct_settlement':
                for oid in sorted({record.receivable_id, record.payable_id}, key=str):
                    uow.finance.get_obligation(oid, for_update=True)
            elif kind == 'transfer':
                uow.finance.lock_money_accounts([record.source_account_id, record.destination_account_id])
            elif kind == 'owner_funding':
                uow.finance.lock_money_accounts([record.money_account_id])
            elif uow.finance.obligation_remaining(resource_id) != record.original_amount:
                raise ValidationError("reverse active allocated payments before reversing obligation")
            original = uow.finance.get_posting(record.posting_id)
            if not original or original.reversal_of_id:
                raise ValidationError("original posting missing or itself a reversal")
            reversal = FinancialPosting(id=uuid4(), entity_id=original.entity_id,
                currency=original.currency, event_date=command.event_date,
                posting_type=kind + "_reversed", source_type=kind, source_id=resource_id,
                created_by=actor_id, reversal_of_id=original.id, reason=command.reason,
                lines=[PostingLine(line.ledger_account_id,
                    PostingSide.CREDIT if line.side == PostingSide.DEBIT else PostingSide.DEBIT,
                    line.amount) for line in original.lines])
            before = serialize_cash(record) if kind in CASH_TYPES else (_serialize_payment(record) if kind == "payment" else _serialize_obligation(record))
            uow.finance.add_posting(reversal)
            record.status = RecordStatus.REVERSED
            record.version += 1
            record.reversal_posting_id = reversal.id
            record.reversed_by = actor_id
            record.reversed_at = datetime.now(timezone.utc)
            record.reversal_reason = command.reason
            if kind == "payment":
                uow.finance.reverse_advance(resource_id)
                uow.finance.save_payment(record)
            elif kind in CASH_TYPES:
                uow.finance.save_cash_record(kind, record)
            else:
                uow.finance.save_obligation(record)
            response = self._linkage(uow, kind, record)
            if kind == "payment":
                response["obligation_balances"] = {str(a.obligation_id): f"{uow.finance.obligation_remaining(a.obligation_id):.2f}" for a in record.allocations}
            uow.audit.append(actor_id=actor_id, action="reverse", entity_type=kind,
                entity_id=resource_id, before=before, after=response, reason=command.reason)
            uow.idempotency.put(actor_id=actor_id, operation=operation, key=key,
                request_hash=request_hash, resource_type=kind, resource_id=resource_id, response=response)
            uow.commit()
            return response

    @staticmethod
    def _linkage(uow, kind, record):
        replacement = uow.finance.replacement_id(kind, record.id)
        result = {"id": str(record.id), "type": kind, "status": record.status.value,
            "version": record.version, "currency": record.currency,
            "original_posting_id": str(record.posting_id) if record.posting_id else None,
            "reversal_posting_id": str(record.reversal_posting_id) if record.reversal_posting_id else None,
            "reversed_by": str(record.reversed_by) if record.reversed_by else None,
            "reversed_at": record.reversed_at.isoformat() if record.reversed_at else None,
            "reason": record.reversal_reason,
            "replacement_of_id": str(record.replacement_of_id) if record.replacement_of_id else None,
            "replacement_id": str(replacement) if replacement else None}
        if kind == 'advance_application':
            result.update(advance_id=str(record.advance_id),obligation_id=str(record.obligation_id),
                          counterparty_id=str(record.counterparty_id),entity_id=str(record.entity_id),
                          amount=f'{record.amount:.2f}',direction=record.direction,source=record.source,
                          event_date=record.event_date.isoformat())
        return result

    def get_linkage(self, kind: str, resource_id: UUID):
        if kind not in {"payment", "obligation", *CASH_TYPES}:
            raise ValidationError("unknown transaction type")
        with self.uow_factory() as uow:
            getter = (lambda rid, **kw: uow.finance.get_cash_record(kind, rid, **kw)) if kind in CASH_TYPES else (uow.finance.get_payment if kind == "payment" else uow.finance.get_obligation)
            record = getter(resource_id)
            if not record:
                raise NotFoundError(kind + " not found")
            response = self._linkage(uow, kind, record)
            if kind == 'obligation':
                opening = uow.finance.get_opening_payable(resource_id)
                if opening:
                    response['opening_provenance'] = opening
            return response

    def reconcile(self, *, include_classification=False, entity_id=None):
        from .reconciliation import reconcile
        with self.uow_factory() as uow:
            return reconcile(uow.session, include_classification=include_classification, entity_id=entity_id)

    def get_currency_balances(self, entity_id: UUID):
        with self.uow_factory() as uow:
            return {"entity_id": str(entity_id), "balances": {
                currency: {category: f"{amount:.2f}" for category, amount in values.items()}
                for currency, values in sorted(uow.finance.currency_balances(entity_id).items())}}

    @staticmethod
    def _replay_if_exists(uow: UnitOfWork, actor_id: UUID, operation: str, key: str, request_hash: str) -> dict[str, Any] | None:
        if not key or len(key) > 200:
            raise ValidationError("Idempotency-Key is required and must be <= 200 characters")
        existing = uow.idempotency.get(actor_id, operation, key)
        if not existing:
            return None
        if existing["request_hash"] != request_hash:
            raise IdempotencyConflictError("idempotency key was already used with a different request")
        return existing["response"]
