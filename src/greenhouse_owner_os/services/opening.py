"""One narrowly approved opening recognition, atomic with ordinary obligation."""
from uuid import UUID
from greenhouse_owner_os.domain.errors import ConflictError, ValidationError
from greenhouse_owner_os.domain.finance import Obligation, ObligationDirection, FinancialPosting, PostingLine, PostingSide
from greenhouse_owner_os.infrastructure.models import ActorRow, CounterpartyRow, OpeningPayableRow, ReferenceRecordRow


class OpeningServiceMixin:
    def recognize_opening_payable(self, *, entity_id, actor_id, idempotency_key, command):
        from .finance import _hash_request, _serialize_obligation
        from .references import reference_id
        operation = 'recognize_opening_payable'
        request_hash = _hash_request({'entity_id':str(entity_id), **command.model_dump(mode='json')})
        with self.uow_factory() as uow:
            actor = uow.session.get(ActorRow, actor_id)
            if not actor or not actor.active or actor.actor_type != 'owner':
                raise ValidationError('explicit active owner approval required')
            uow.idempotency.lock(actor_id, operation, idempotency_key)
            replay = self._replay_if_exists(uow, actor_id, operation, idempotency_key, request_hash)
            if replay:
                return replay
            entity = uow.finance.opening_scope_lock(entity_id)
            if command.original_event_date >= entity.finance_cutover_date:
                raise ValidationError('opening must preserve a genuinely pre-cut-over original date')
            cp = uow.session.get(CounterpartyRow, command.counterparty_id)
            if not cp or cp.entity_id != entity_id or not cp.active or cp.counterparty_type not in ('supplier','both'):
                raise ValidationError('opening payable requires a verified supplier in entity')
            for resource_id, kind in ((entity_id,'entity'),(cp.id,'counterparty')):
                proof = uow.session.get(ReferenceRecordRow, resource_id)
                if not proof or proof.kind != kind or proof.reference_class != entity.reference_class:
                    raise ValidationError('owner references must be explicitly verified before opening recognition')
            old = uow.finance.opening_by_approval(entity_id, command.approval_reference, command.source)
            if old:
                if old.request_hash != request_hash:
                    raise ConflictError('opening approval already recognized with different facts')
                obligation = uow.finance.get_obligation(old.obligation_id)
            else:
                offset = uow.finance.get_system_ledger_account(entity_id, command.currency, 'OPENING_PAYABLE_OFFSET')
                uow.finance.assert_account_class(offset, entity_id, command.currency, 'opening_equity')
                control = uow.finance.get_system_ledger_account(entity_id, command.currency, 'AP_CONTROL')
                uow.finance.assert_account_class(control, entity_id, command.currency, 'payable')
                obligation = Obligation.draft(entity_id=entity_id, counterparty_id=cp.id,
                    direction=ObligationDirection.PAYABLE, original_amount=command.amount,
                    currency=command.currency, event_date=command.original_event_date,
                    due_date=None, offset_ledger_account_id=offset)
                obligation.id = reference_id(entity_id, 'opening_payable', command.approval_reference)
                uow.finance.add_obligation(obligation, actor_id)
                uow.session.flush()
                before = _serialize_obligation(obligation, obligation.original_amount)
                uow.audit.append(actor_id=actor_id,action='create',entity_type='obligation',
                    entity_id=obligation.id,before=None,after=before)
                uow.session.add(OpeningPayableRow(obligation_id=obligation.id,entity_id=entity_id,
                    counterparty_id=cp.id,amount=command.amount,currency=command.currency,
                    original_event_date=command.original_event_date,opening_date=entity.finance_cutover_date,
                    description=command.description,source=command.source,
                    approval_reference=command.approval_reference,request_hash=request_hash,verified_by=actor_id))
                uow.session.flush()
                entity.finance_opening_policy = 'EXPLICIT_AP'
                uow.session.flush()
                uow.audit.append(actor_id=actor_id,action='opening_approve',entity_type='obligation',
                    entity_id=obligation.id,before=None,after=uow.finance.get_opening_payable(obligation.id))
                posting = FinancialPosting(id=reference_id(obligation.id,'posting','opening'),
                    entity_id=entity_id,currency=command.currency,event_date=entity.finance_cutover_date,
                    posting_type='opening_payable',source_type='obligation',source_id=obligation.id,
                    created_by=actor_id,reason=command.approval_reference,
                    lines=[PostingLine(offset,PostingSide.DEBIT,command.amount),
                           PostingLine(control,PostingSide.CREDIT,command.amount)])
                uow.finance.add_posting(posting)
                obligation.post(posting.id)
                uow.finance.save_obligation(obligation)
                uow.audit.append(actor_id=actor_id,action='post',entity_type='obligation',entity_id=obligation.id,
                    before=before,after={**_serialize_obligation(obligation,obligation.original_amount),
                    'opening_provenance':uow.finance.get_opening_payable(obligation.id)})
            response = _serialize_obligation(obligation,uow.finance.obligation_remaining(obligation.id))
            response['opening_provenance'] = uow.finance.get_opening_payable(obligation.id)
            uow.idempotency.put(actor_id=actor_id,operation=operation,key=idempotency_key,
                request_hash=request_hash,resource_type='obligation',resource_id=obligation.id,response=response)
            uow.commit()
            return response
