"""Narrow source-linked manual completion of the existing DirectExpense.

No text extraction or business reclassification lives here. The retained source
and relational lineage are separate from owner-confirmed current expense facts.
"""
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import text

from greenhouse_owner_os.domain.errors import ConflictError, NotFoundError, ValidationError
from greenhouse_owner_os.domain.owner_capture import MoneyProposal
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
            raise ConflictError('Исходное предложение связано с ручным расходом; откройте связанное действие ' + link[0]['recovery_proposal_id'])

    def retry_preparation(self, e, a, source_id):
        with self.factory() as u:
            u.session.execute(text('SET TRANSACTION ISOLATION LEVEL READ COMMITTED'))
            self.owner(u, e, a)
            original = u.session.get(InboxRow, source_id)
            if not original or original.entity_id != e or original.source != 'owner:r2':
                raise NotFoundError('Источник не найден')
            self._source_lock(u, e, source_id)
            if self._source_reserved(u, source_id):
                raise ConflictError('Источник уже связан с ручным расходом; подготовка не возобновляется')
            return self._capture_view(u, original)

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
                return self._view(u, self._proposal(u, e, links[0]['recovery_proposal_id'], True))
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
                    dict(payload=old['payload'], preview_digest=digest, recovery_proposal_id=target['id'], reason='Confirmed source-linked DirectExpense recovery'))

    def _recovery_view(self, u, p, revision):
        links = rows(u, 'SELECT * FROM core.owner_recovery_links WHERE origin_proposal_id=:p OR recovery_proposal_id=:p', p=p['id'])
        effects = rows(u, '''SELECT d.* FROM finance.direct_expenses d JOIN core.confirmation_effects c ON c.direct_expense_id=d.id
            WHERE c.proposal_id=:p''', p=p['id']) if p['action_type'] == 'DIRECT_EXPENSE' else []
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
