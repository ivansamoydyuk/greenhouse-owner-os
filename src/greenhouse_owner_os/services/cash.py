from datetime import datetime, timezone
from uuid import uuid4

from greenhouse_owner_os.domain.cash import CASH_TYPES, serialize_cash
from greenhouse_owner_os.domain.commands import CreatePaymentCommand
from greenhouse_owner_os.domain.finance import FinancialPosting, PostingLine, PostingSide, RecordStatus, PaymentDirection
from greenhouse_owner_os.domain.errors import ValidationError, ConflictError, NotFoundError


class CashServiceMixin:
    def create_cash(self, *, kind, actor_id, idempotency_key, command):
        from .finance import _hash_request
        if kind not in CASH_TYPES: raise ValidationError('unknown cash operation')
        operation = 'create_' + kind
        request_hash = _hash_request(command.model_dump(mode='json'))
        with self.uow_factory() as uow:
            uow.idempotency.lock(actor_id, operation, idempotency_key)
            replay = self._replay_if_exists(uow, actor_id, operation, idempotency_key, request_hash)
            if replay: return replay
            record = CASH_TYPES[kind](id=uuid4(), **command.model_dump())
            self._cash_lines(uow, kind, record, posting=False)
            uow.finance.add_cash_record(kind, record, actor_id)
            response = serialize_cash(record)
            uow.audit.append(actor_id=actor_id, action='create', entity_type=kind, entity_id=record.id, before=None, after=response)
            uow.idempotency.put(actor_id=actor_id, operation=operation, key=idempotency_key, request_hash=request_hash, resource_type=kind, resource_id=record.id, response=response)
            uow.commit()
            return response

    def post_cash(self, *, kind, resource_id, actor_id, idempotency_key, expected_version):
        from .finance import _hash_request
        if kind not in CASH_TYPES: raise ValidationError('unknown cash operation')
        operation = 'post_' + kind
        request_hash = _hash_request({'id': str(resource_id), 'expected_version': expected_version})
        with self.uow_factory() as uow:
            uow.idempotency.lock(actor_id, operation, idempotency_key)
            replay = self._replay_if_exists(uow, actor_id, operation, idempotency_key, request_hash)
            if replay: return replay
            record = uow.finance.get_cash_record(kind, resource_id, for_update=True)
            if not record: raise NotFoundError(kind + ' not found')
            if record.version != expected_version: raise ConflictError('version conflict')
            if record.status != RecordStatus.DRAFT: raise ValidationError('only draft can be posted')
            lines = self._cash_lines(uow, kind, record, posting=True)
            posting = FinancialPosting(id=uuid4(), entity_id=record.entity_id, currency=record.currency,
                event_date=record.event_date, posting_type=kind+'_posted', source_type=kind,
                source_id=record.id, created_by=actor_id, lines=lines)
            before = serialize_cash(record)
            uow.finance.add_posting(posting)
            record.status = RecordStatus.POSTED
            record.posting_id = posting.id
            record.posted_at = datetime.now(timezone.utc)
            record.version += 1
            uow.finance.save_cash_record(kind, record)
            response = serialize_cash(record)
            uow.audit.append(actor_id=actor_id, action='post', entity_type=kind, entity_id=record.id, before=before, after=response)
            uow.idempotency.put(actor_id=actor_id, operation=operation, key=idempotency_key, request_hash=request_hash, resource_type=kind, resource_id=record.id, response=response)
            uow.commit()
            return response

    @staticmethod
    def _cash_lines(uow, kind, r, *, posting):
        repo = uow.finance
        def system(code): return repo.get_system_ledger_account(r.entity_id, r.currency, code)
        def cash(aid): return repo.get_money_account_ledger(aid, r.entity_id, r.currency)
        debit, credit = PostingSide.DEBIT, PostingSide.CREDIT
        if kind == 'transfer':
            if r.source_account_id == r.destination_account_id: raise ValidationError('accounts must differ')
            if posting: repo.lock_money_accounts([r.source_account_id, r.destination_account_id])
            source, destination = cash(r.source_account_id), cash(r.destination_account_id)
            if source == destination: raise ValidationError('accounts must have distinct cash ledgers')
            lines = [PostingLine(source, credit, r.amount), PostingLine(destination, debit, r.amount)]
            if r.fee:
                repo.assert_account_class(r.fee_account_id, r.entity_id, r.currency, 'expense')
                # Explicit fee lines; transfer amount remains exactly r.amount.
                lines += [PostingLine(source, credit, r.fee), PostingLine(r.fee_account_id, debit, r.fee)]
            return lines
        if kind == 'owner_funding':
            if posting: repo.lock_money_accounts([r.money_account_id])
            funding = system('OWNER_FUNDING')
            repo.assert_account_class(funding, r.entity_id, r.currency, 'owner_funding')
            return [PostingLine(cash(r.money_account_id), debit, r.amount), PostingLine(funding, credit, r.amount)]
        obs = {}
        for oid in sorted({r.receivable_id, r.payable_id}, key=str):
            obs[oid] = repo.get_obligation(oid, for_update=posting)
            if not obs[oid]: raise NotFoundError('settlement obligation not found')
        for oid, direction in [(r.receivable_id, 'receivable'), (r.payable_id, 'payable')]:
            ob = obs[oid]
            if ob.entity_id != r.entity_id or ob.currency != r.currency or ob.direction.value != direction:
                raise ValidationError('settlement entity/currency/direction mismatch')
            repo.assert_counterparty_scope(ob.counterparty_id, r.entity_id)
            if ob.status != RecordStatus.POSTED: raise ValidationError('settlement requires posted obligations')
            if r.event_date < ob.event_date: raise ValidationError('settlement date precedes obligation')
            if posting and r.amount > repo.obligation_remaining(oid): raise ValidationError('settlement exceeds remaining')
        return [PostingLine(system('AP_CONTROL'), debit, r.amount), PostingLine(system('AR_CONTROL'), credit, r.amount)]

    def reverse_cash(self, *, kind, resource_id, actor_id, idempotency_key, command):
        if kind not in CASH_TYPES: raise ValidationError('unknown cash operation')
        return self._reverse(kind, resource_id, actor_id, idempotency_key, command)

    def create_supplier_advance(self, *, actor_id, idempotency_key, command):
        # Same persisted payment/advance model and same idempotency scope as payments.
        with self.uow_factory() as uow:
            uow.finance.assert_counterparty_scope(command.counterparty_id, command.entity_id)
        return self.create_payment(actor_id=actor_id, idempotency_key=idempotency_key,
            command=CreatePaymentCommand(**command.model_dump(), direction=PaymentDirection.OUT, allocations=()))

    def post_supplier_advance(self, *, actor_id, idempotency_key, payment_id, expected_version):
        with self.uow_factory() as uow:
            r = uow.finance.get_payment(payment_id)
            if not r: raise NotFoundError('supplier advance payment not found')
            if r.direction != PaymentDirection.OUT or r.allocations:
                raise ValidationError('standalone supplier advance requires unallocated outgoing payment')
        return self.post_payment(actor_id=actor_id, idempotency_key=idempotency_key, payment_id=payment_id, expected_version=expected_version)
