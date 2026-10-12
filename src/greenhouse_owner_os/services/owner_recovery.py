"""Narrow source-linked manual completion of the existing DirectExpense.

No text extraction or business reclassification lives here. The retained source
and relational lineage are separate from owner-confirmed current expense facts.
"""
from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import text

from greenhouse_owner_os.domain.errors import ConflictError, NotFoundError, ValidationError
from greenhouse_owner_os.domain.owner_capture import MoneyProposal, TaskProposal
from greenhouse_owner_os.infrastructure.inbox_models import InboxRow
from .finance import _hash_request
from .inventory import rows


def expense_revision(previous, edits):
    """Keep server-held provenance for normalized unchanged values only."""
    from pydantic import ValidationError as InputError
    try:
        fields = MoneyProposal.model_validate({**previous['payload'], **edits}).model_dump(mode='json')
    except InputError as exc:
        raise ValidationError('Проверьте значения расхода') from exc
    provenance = {}
    for key, value in fields.items():
        old = previous['payload'].get(key)
        unchanged = old == value
        if key == 'amount' and old is not None and value is not None:
            unchanged = Decimal(old) == Decimal(value)
        provenance[key] = previous['provenance'].get(key, {'category': 'UNKNOWN' if value is None else 'INFERENCE'}) if unchanged else {'category': 'OWNER INPUT'}
    return fields, provenance


class ExpenseRecoveryMixin:
    def _source_lock(self, u, e, source_id):
        # Called before any mutable row lock, including after every process commit.
        if u.session.scalar(text("SHOW transaction_isolation")) != 'read committed':
            raise ConflictError('Изменения источника требуют READ COMMITTED')
        u.session.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),
                          {'key': f'owner-review-source:{e}:{source_id}'})

    def _source_reserved(self, u, source_id):
        return bool(rows(u, 'SELECT recovery_proposal_id FROM core.owner_recovery_links WHERE inbox_id=:s LIMIT 1', s=source_id))

    def _recovery_origin_guard(self, u, proposal):
        link = rows(u, 'SELECT recovery_proposal_id FROM core.owner_recovery_links WHERE origin_proposal_id=:p', p=proposal['id'])
        if link:
            raise ConflictError('Исходное предложение связано с ручным действием; откройте связанное действие ' + link[0]['recovery_proposal_id'])

    def retry_preparation(self, e, a, source_id):
        with self.factory() as u:
            u.session.execute(text('SET TRANSACTION ISOLATION LEVEL READ COMMITTED'))
            self.owner(u, e, a)
            original = u.session.get(InboxRow, source_id)
            if not original or original.entity_id != e or original.source != 'owner:r2' or original.created_by != a:
                raise NotFoundError('Источник не найден')
            self._source_lock(u, e, source_id)
            if self._source_reserved(u, source_id):
                raise ConflictError('Источник уже связан с ручным действием; подготовка не возобновляется')
            self._materialize_exhausted_document(u, e, a, original)
            result = self._capture_view(u, original)
            u.commit()
            return result

    def _materialize_exhausted_document(self, u, e, a, original):
        """Explicit owner recovery after final crash; no fourth OCR attempt."""
        if original.file_bytes is None or original.metadata_json.get('capture_context') not in ('depot_receipt', 'supplier_invoice'):
            return
        if rows(u, 'SELECT id FROM core.proposed_actions WHERE inbox_id=:s LIMIT 1', s=original.id):
            return
        jobs = rows(u, 'SELECT *,lease_until<clock_timestamp() AS expired FROM core.processing_jobs WHERE inbox_id=:s ORDER BY id FOR UPDATE', s=original.id)
        if not jobs or any(j['attempts'] != 3 or not (j['state'] == 'FAILED' and j['lease_token'] is None and j['lease_until'] is None or j['state'] == 'RUNNING' and j['expired']) for j in jobs):
            return
        rows(u, "UPDATE core.processing_jobs SET state='FAILED',error_code='RETRY_EXHAUSTED',lease_token=NULL,lease_until=NULL WHERE inbox_id=:s RETURNING id", s=original.id)
        from .owner_preparation import prepare_context_document
        specs = prepare_context_document('', today=original.received_at.astimezone(ZoneInfo('Europe/Istanbul')).date(), refs={},
            context=original.metadata_json['capture_context'], invoice_intent=original.metadata_json.get('invoice_intent'),
            receipt_id=original.metadata_json.get('invoice_receipt_id'), binary=True, recognition_state='FAILED')
        targets = []
        for index, (kind, payload, provenance) in enumerate(specs):
            pid = uuid4()
            for fact in provenance.values():
                fact['recovery_basis'] = 'owner_requested_after_terminal_preparation_exhaustion'
            rows(u, """INSERT INTO core.proposed_actions(id,entity_id,inbox_id,slot,action_type,state,version,priority,created_by,first_unresolved_at)
                VALUES(:id,:e,:s,:slot,:kind,'PREPARING',1,:priority,:a,:saved) RETURNING id""",
                id=pid, e=e, s=original.id, slot=f'{kind}:{index}', kind=kind, priority=1 if kind=='DEPOT_RECEIPT' else 0, a=a, saved=original.created_at)
            self._revision(u, self._proposal(u, e, pid), payload, provenance, a, 'PREPARE',
                           warnings=['Подготовка исчерпана. Оригинал сохранён; вручную введите и проверьте данные.'])
            targets.append(str(pid))
        u.audit.append(actor_id=a, action='owner_document_manual_recovery', entity_type='owner_capture', entity_id=original.id,
                       before=None, after={'proposal_ids': targets, 'basis': 'terminal_exhaustion', 'ocr_attempt_added': False})

    def recover_expense(self, e, a, source_id, key, body):
        def run(u):
            original = u.session.get(InboxRow, source_id)
            if not original or original.entity_id != e or original.source != 'owner:r2' or original.created_by != a:
                raise NotFoundError('Источник не найден')
            if body.origin_proposal_id:
                # Immutable identity is safe to resolve without a lock. Reject a
                # forged source/origin pair BEFORE acquiring either source root;
                # never nest source A then source B through _proposal(lock=True).
                selected = self._proposal(u, e, body.origin_proposal_id)
                if selected['inbox_id'] != str(source_id) or selected['created_by'] != str(a):
                    raise NotFoundError('Предложение этого источника не найдено')
            self._source_lock(u, e, source_id)
            # Reuse before checking original version: a lost handoff response may
            # be retried after target confirmation retired the original version.
            links = rows(u, '''SELECT * FROM core.owner_recovery_links WHERE entity_id=:e AND inbox_id=:s
                AND origin_proposal_id IS NOT DISTINCT FROM CAST(:origin AS uuid)''',
                e=e, s=source_id, origin=body.origin_proposal_id)
            if links:
                if links[0]['origin_version'] != body.expected_version:
                    raise ConflictError('Исходная версия не совпадает с сохранённым переходом')
                target = self._proposal(u, e, links[0]['recovery_proposal_id'], True)
                if target['action_type'] != 'DIRECT_EXPENSE':
                    raise ConflictError('Для источника уже выбрано другое действие')
                return self._view(u, target)
            jobs = rows(u, 'SELECT * FROM core.processing_jobs WHERE entity_id=:e AND inbox_id=:s ORDER BY id FOR UPDATE', e=e, s=source_id)
            if not jobs or any(j['state'] in ('QUEUED', 'RUNNING') or j['state'] == 'FAILED' and j['attempts'] < 3
                               or j['lease_token'] is not None or j['lease_until'] is not None for j in jobs):
                raise ConflictError('Подготовка ещё активна или доступен повтор; оригинал сохранён')
            origin = None
            failed_job = None
            if body.origin_proposal_id:
                origin = self._proposal(u, e, body.origin_proposal_id, True)
                if origin['inbox_id'] != str(source_id) or origin['created_by'] != str(a):
                    raise NotFoundError('Предложение этого источника не найдено')
                if origin['version'] != body.expected_version:
                    raise ConflictError('Карточка изменилась; обновите её')
                if origin['action_type'] == 'DIRECT_EXPENSE':
                    # No new identity or relationship is needed for typed expense.
                    return self._view(u, origin)
                if origin['action_type'] != 'UNRESOLVED' or origin['state'] not in ('NEEDS_INPUT', 'READY', 'DEFERRED', 'STALE'):
                    raise ConflictError('Этот тип действия нельзя преобразовать в прямой расход')
                if rows(u, "SELECT version FROM core.proposal_revisions WHERE proposal_id=:p AND version=1 AND jsonb_typeof(payload->'task_candidates')='object'", p=origin['id']):
                    raise ConflictError('Распознанная задача требует выбора исполнителя, а не денежного действия')
                if rows(u, 'SELECT proposal_id FROM core.confirmation_effects WHERE proposal_id=:p', p=origin['id']):
                    raise ConflictError('Исходное действие уже подтверждено')
            else:
                if rows(u, 'SELECT id FROM core.proposed_actions WHERE inbox_id=:s LIMIT 1', s=source_id):
                    raise ConflictError('Выберите существующее предложение этого источника')
                failed_job = next((j['id'] for j in jobs if j['state'] == 'FAILED' and j['attempts'] == 3), None)
                if not failed_job:
                    raise ConflictError('Ручной переход доступен после исчерпания подготовки')
            pid = uuid4()
            slot = 'manual:direct-expense:' + str(body.origin_proposal_id or source_id)
            rows(u, """INSERT INTO core.proposed_actions(id,entity_id,inbox_id,slot,action_type,state,version,priority,created_by,first_unresolved_at)
                VALUES(:id,:e,:s,:slot,'DIRECT_EXPENSE','PREPARING',1,0,:a,:saved) RETURNING id""",
                id=pid, e=e, s=source_id, slot=slot, a=a, saved=original.created_at)
            p = self._proposal(u, e, pid)
            # UNRESOLVED.note / OCR text is context, never structured expense truth.
            payload = MoneyProposal(currency=None).model_dump(mode='json')
            provenance = {k: {'category': 'UNKNOWN' if v is None else 'INFERENCE', 'basis': 'manual_recovery_no_extracted_fact'} for k, v in payload.items()}
            self._revision(u, p, payload, provenance, a, 'PREPARE')
            rows(u, '''INSERT INTO core.owner_recovery_links(entity_id,inbox_id,origin_proposal_id,origin_version,recovery_proposal_id,failed_job_id,created_by)
                VALUES(:e,:s,:origin,:version,:target,:job,:a) RETURNING recovery_proposal_id''',
                e=e, s=source_id, origin=body.origin_proposal_id, version=body.expected_version, target=pid, job=failed_job, a=a)
            return self._view(u, self._proposal(u, e, pid))
        return self._write(e, a, key, 'owner_expense_recovery', dict(source_id=str(source_id), **body.model_dump(mode='json')), run)

    def recover_money(self, e, a, source_id, key, body):
        """Select one typed action for a bounded immutable nominal money note."""
        from sqlalchemy import select
        from greenhouse_owner_os.infrastructure.models import CounterpartyRow
        from .owner_preparation import nominal_money_candidate
        kinds = {'operating_payable': 'PAYABLE', 'paid_expense': 'DIRECT_EXPENSE', 'debt_payment': 'PAYMENT'}
        kind = kinds[body.intent]

        def run(u):
            original = u.session.get(InboxRow, source_id)
            if not original or original.entity_id != e or original.source != 'owner:r2' or original.created_by != a:
                raise NotFoundError('Источник не найден')
            selected = self._proposal(u, e, body.origin_proposal_id)
            if selected['inbox_id'] != str(source_id) or selected['created_by'] != str(a):
                raise NotFoundError('Предложение этого источника не найдено')
            self._source_lock(u, e, source_id)
            links = rows(u, 'SELECT * FROM core.owner_recovery_links WHERE origin_proposal_id=:p', p=body.origin_proposal_id)
            if links:
                link = links[0]
                target = self._proposal(u, e, link['recovery_proposal_id'], True)
                if link['origin_version'] != body.expected_version or target['action_type'] != kind:
                    raise ConflictError('Для исходной версии уже выбрано другое действие')
                return self._view(u, target)
            jobs = rows(u, 'SELECT * FROM core.processing_jobs WHERE entity_id=:e AND inbox_id=:s ORDER BY id FOR UPDATE', e=e, s=source_id)
            if not jobs or any(j['state'] in ('QUEUED', 'RUNNING') or j['state'] == 'FAILED' and j['attempts'] < 3
                               or j['lease_token'] is not None or j['lease_until'] is not None for j in jobs):
                raise ConflictError('Подготовка ещё активна или доступен повтор; оригинал сохранён')
            origin = self._proposal(u, e, body.origin_proposal_id, True)
            if origin['version'] != body.expected_version:
                raise ConflictError('Карточка изменилась; обновите её')
            if origin['action_type'] != 'UNRESOLVED' or origin['state'] not in ('NEEDS_INPUT', 'READY', 'DEFERRED', 'STALE'):
                raise ConflictError('Этот источник не поддерживает выбор денежного действия')
            # Candidate JSON, later note edits and sibling proposals do not grant
            # financial authority. Recompute only the immutable complete text.
            first = rows(u, 'SELECT payload FROM core.proposal_revisions WHERE proposal_id=:p AND version=1', p=origin['id'])
            siblings = rows(u, 'SELECT id FROM core.proposed_actions WHERE inbox_id=:s', s=source_id)
            if original.file_bytes is not None or original.metadata_json.get('capture_context') not in (None, 'quick_expense') or len(siblings) != 1 or not first or first[0]['payload'].get('note') != original.raw_text:
                raise ConflictError('Выбор доступен только для исходной короткой денежной записи')
            refs = {'counterparties': list(u.session.scalars(select(CounterpartyRow).where(CounterpartyRow.entity_id == e)))}
            candidate = nominal_money_candidate(original.raw_text or '', today=original.received_at.astimezone(ZoneInfo('Europe/Istanbul')).date(), refs=refs)
            if candidate is None:
                raise ConflictError('В исходной записи нет однозначных фактов для этого выбора')
            payload, provenance = candidate
            if kind in ('DIRECT_EXPENSE', 'PAYMENT'):
                payload['payment_purpose'] = 'expense' if kind == 'DIRECT_EXPENSE' else 'debt'
                provenance['payment_purpose'] = {'category': 'OWNER INPUT', 'basis': 'explicit_money_recovery_intent'}
            pid = uuid4()
            rows(u, """INSERT INTO core.proposed_actions(id,entity_id,inbox_id,slot,action_type,state,version,priority,created_by,first_unresolved_at)
                VALUES(:id,:e,:s,:slot,:kind,'PREPARING',1,0,:a,:saved) RETURNING id""",
                id=pid, e=e, s=source_id, slot='manual:money:' + str(body.origin_proposal_id), kind=kind, a=a, saved=original.created_at)
            self._revision(u, self._proposal(u, e, pid), payload, provenance, a, 'PREPARE', review_reason=body.reason or '')
            rows(u, """INSERT INTO core.owner_recovery_links(entity_id,inbox_id,origin_proposal_id,origin_version,recovery_proposal_id,created_by)
                VALUES(:e,:s,:origin,:version,:target,:a) RETURNING recovery_proposal_id""",
                e=e, s=source_id, origin=body.origin_proposal_id, version=body.expected_version, target=pid, a=a)
            return self._view(u, self._proposal(u, e, pid))
        return self._write(e, a, key, 'owner_money_recovery', dict(source_id=str(source_id), **body.model_dump(mode='json')), run)

    def recover_task(self, e, a, source_id, key, body):
        """Explicit audience choice prepares one linked task; it has no effect.

        Reuse immutable prepared facts/current owner edits, never reparse an old
        proposal in place or turn an already confirmed personal task into work.
        """
        kind = {'personal': 'PERSONAL_TASK', 'worker': 'WORKER_TASK'}[body.audience]

        def run(u):
            original = u.session.get(InboxRow, source_id)
            if not original or original.entity_id != e or original.source != 'owner:r2' or original.created_by != a:
                raise NotFoundError('Источник не найден')
            selected = self._proposal(u, e, body.origin_proposal_id)
            if selected['inbox_id'] != str(source_id) or selected['created_by'] != str(a):
                raise NotFoundError('Предложение этого источника не найдено')
            self._source_lock(u, e, source_id)
            links = rows(u, 'SELECT * FROM core.owner_recovery_links WHERE origin_proposal_id=:p', p=body.origin_proposal_id)
            if links:
                link = links[0]
                target = self._proposal(u, e, link['recovery_proposal_id'], True)
                if link['origin_version'] != body.expected_version or target['action_type'] != kind:
                    raise ConflictError('Для исходной версии уже выбрано другое действие')
                return self._view(u, target)
            jobs = rows(u, 'SELECT * FROM core.processing_jobs WHERE entity_id=:e AND inbox_id=:s ORDER BY id FOR UPDATE', e=e, s=source_id)
            if not jobs or any(j['state'] in ('QUEUED', 'RUNNING') or j['state'] == 'FAILED' and j['attempts'] < 3
                               or j['lease_token'] is not None or j['lease_until'] is not None for j in jobs):
                raise ConflictError('Подготовка ещё активна или доступен повтор; оригинал сохранён')
            origin = self._proposal(u, e, body.origin_proposal_id, True)
            self._recovery_origin_guard(u, origin)
            if origin['version'] != body.expected_version or origin['state'] not in ('NEEDS_INPUT', 'READY', 'DEFERRED', 'STALE'):
                raise ConflictError('Карточка изменилась или уже подтверждена; обновите её')
            if origin['action_type'] not in ('UNRESOLVED', 'PERSONAL_TASK', 'WORKER_TASK'):
                raise ConflictError('Это предложение не является задачей')
            if rows(u, 'SELECT proposal_id FROM core.confirmation_effects WHERE proposal_id=:p', p=origin['id']):
                raise ConflictError('Подтверждённую задачу нельзя преобразовать')
            first = rows(u, 'SELECT * FROM core.proposal_revisions WHERE proposal_id=:p AND version=1', p=origin['id'])[0]
            previous = self._view(u, origin)['revision']
            if origin['action_type'] == 'UNRESOLVED':
                if not first['payload'].get('task_candidates') or not previous['payload'].get('task_candidates'):
                    raise ConflictError('В исходном предложении нет распознанной задачи')
                payload = previous['payload']['task_candidates']
                evidence = previous['provenance'].get('task_candidates', {})
                provenance = evidence.get('fields') or {k: {'category': evidence.get('category', 'INFERENCE')} for k in payload}
            else:
                if origin['action_type'] == kind:
                    return self._view(u, origin)
                payload = previous['payload']
                provenance = dict(previous['provenance'])
            payload = TaskProposal.model_validate(payload).model_dump(mode='json')
            provenance = dict(provenance)
            if kind == 'PERSONAL_TASK':
                for field in ('worker_id', 'language', 'rendered_text'):
                    payload[field] = None
                    provenance[field] = {'category': 'OWNER INPUT', 'basis': 'explicit_personal_audience_choice'}
                payload.pop('execution_contract', None)
                provenance.pop('execution_contract', None)
            else:
                payload['execution_contract'] = 'R2_RECEIVED_REPORT'
                provenance['execution_contract'] = {'category': 'CONTEXT DEFAULT', 'basis': 'worker_execution_contract_r2_v1'}
            pid = uuid4()
            rows(u, """INSERT INTO core.proposed_actions(id,entity_id,inbox_id,slot,action_type,state,version,priority,created_by,first_unresolved_at)
                VALUES(:id,:e,:s,:slot,:kind,'PREPARING',1,2,:a,:saved) RETURNING id""",
                id=pid, e=e, s=source_id, slot='manual:task:' + str(body.origin_proposal_id), kind=kind, a=a, saved=original.created_at)
            self._revision(u, self._proposal(u, e, pid), payload, provenance, a, 'PREPARE',
                           run_id=previous.get('extraction_run_id'),
                           warnings=[message for message in previous['missing'] if message.startswith('Возможный повтор источника:')],
                           review_reason=body.reason or 'Явный выбор: ' + body.audience)
            rows(u, """INSERT INTO core.owner_recovery_links(entity_id,inbox_id,origin_proposal_id,origin_version,recovery_proposal_id,created_by)
                VALUES(:e,:s,:origin,:version,:target,:a) RETURNING recovery_proposal_id""",
                e=e, s=source_id, origin=body.origin_proposal_id, version=body.expected_version, target=pid, a=a)
            return self._view(u, self._proposal(u, e, pid))
        return self._write(e, a, key, 'owner_task_recovery', dict(source_id=str(source_id), **body.model_dump(mode='json')), run)

    def _retire_recovery_origin(self, u, target, actor):
        from .owner_capture import js
        links = rows(u, 'SELECT * FROM core.owner_recovery_links WHERE recovery_proposal_id=:p', p=target['id'])
        if not links or links[0]['origin_proposal_id'] is None:
            return
        link = links[0]
        origin = self._proposal(u, target['entity_id'], link['origin_proposal_id'], True)
        old = rows(u, 'SELECT * FROM core.proposal_revisions WHERE proposal_id=:p AND version=:v', p=origin['id'], v=link['origin_version'])[0]
        version = link['origin_version'] + 1
        digest = _hash_request(dict(id=origin['id'], version=version, type=origin['action_type'], payload=old['payload'], basis=old['basis'], missing=old['missing']))
        rows(u, '''INSERT INTO core.proposal_revisions(proposal_id,entity_id,version,schema_version,payload,provenance,missing,basis,preview_digest,actor_id,reason)
            VALUES(:p,:e,:v,1,CAST(:payload AS jsonb),CAST(:provenance AS jsonb),CAST(:missing AS jsonb),CAST(:basis AS jsonb),:digest,:a,'REJECT') RETURNING version''',
            p=origin['id'], e=origin['entity_id'], v=version, payload=js(old['payload']), provenance=js(old['provenance']),
            missing=js(old['missing']), basis=js(old['basis']), digest=digest, a=actor)
        rows(u, "UPDATE core.proposed_actions SET version=:v,state='REJECTED',updated_at=clock_timestamp() WHERE id=:p RETURNING id", p=origin['id'], v=version)
        self._audit(u, origin, actor, 'REJECT', version,
                    dict(payload=old['payload'], preview_digest=digest, recovery_proposal_id=target['id'], reason='Confirmed source-linked task audience recovery' if target['slot'].startswith('manual:task:') else 'Confirmed source-linked money recovery' if target['slot'].startswith('manual:money:') else 'Confirmed source-linked DirectExpense recovery'))

    def _recovery_view(self, u, p, revision):
        links = rows(u, 'SELECT * FROM core.owner_recovery_links WHERE origin_proposal_id=:p OR recovery_proposal_id=:p', p=p['id'])
        effects = rows(u, '''SELECT d.* FROM finance.direct_expenses d JOIN core.confirmation_effects c ON c.direct_expense_id=d.id
            WHERE c.proposal_id=:p''', p=p['id']) if p['action_type'] == 'DIRECT_EXPENSE' else []
        if links:
            links[0]['target_action_type'] = self._proposal(u, p['entity_id'], links[0]['recovery_proposal_id'])['action_type']
        result = dict(recovery=links[0] if links else None, current_effect=effects[0] if effects else None)
        if links and links[0]['origin_proposal_id']:
            link = links[0]
            selected = rows(u, '''SELECT payload,provenance FROM core.proposal_revisions
                WHERE entity_id=:e AND proposal_id=:p AND version=:v''',
                e=p['entity_id'], p=link['origin_proposal_id'], v=link['origin_version'])[0]
            # The selected origin may include an owner clarification distinct
            # from raw source text. Keep that immutable context visible even
            # after retirement; it is never an extracted expense field.
            result['recovery_origin_context'] = dict(proposal_id=link['origin_proposal_id'],
                version=link['origin_version'], note=selected['payload'].get('note'),
                provenance=selected['provenance'].get('note', {'category': 'UNKNOWN'}))
        if p['action_type'] == 'DIRECT_EXPENSE':
            result['expense_context'] = dict(
                description_kind='OWNER INPUT' if revision['provenance'].get('description', {}).get('category') == 'OWNER INPUT' else 'RETAINED CONTEXT' if revision['payload'].get('description') else 'UNKNOWN',
                source_is_not_extracted_fields=True,
                unknown_fields=[k for k in ('amount', 'currency', 'event_date', 'money_account_id', 'description') if revision['payload'].get(k) is None])
        return result
