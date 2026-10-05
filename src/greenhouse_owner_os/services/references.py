"""Atomic, non-posting setup using existing audit/idempotency infrastructure."""
from uuid import UUID, NAMESPACE_URL, uuid5
from sqlalchemy import select, text
from greenhouse_owner_os.domain.errors import ConflictError, NotFoundError, ValidationError
from greenhouse_owner_os.domain.references import SYSTEM_ACCOUNTS, SUPPORTED_CURRENCIES, canonical_key
from greenhouse_owner_os.infrastructure.models import (
    EntityRow, ActorRow, CounterpartyRow, LedgerAccountRow, MoneyAccountRow,
    ReferenceRecordRow, PostingRow, PostingEntryRow, AuditEventRow,
)
from .finance import FinanceService, _hash_request

RESOURCE_MODELS = {'entity': EntityRow, 'counterparty': CounterpartyRow,
                   'money_account': MoneyAccountRow, 'ledger_account': LedgerAccountRow}


def reference_id(scope, kind, key):
    return uuid5(NAMESPACE_URL, f'greenhouse-owner-os/reference/v1/{scope}/{kind}/{key}')


def serialize(row):
    return dict(id=str(row.id), entity_id=str(row.entity_id), kind=row.kind,
                classification=row.reference_class, original_name=row.original_name,
                source=row.source, created_by=str(row.created_by),
                created_at=row.created_at.isoformat(), **row.definition)


class ReferenceService:
    def __init__(self, uow_factory, *, environment):
        if environment not in ('OWNER_VERIFIED', 'TEST_PREVIEW'):
            raise ValueError('explicit reference environment required')
        self.uow_factory = uow_factory
        self.environment = environment
        self.finance = FinanceService(uow_factory)

    def _check_target(self, session):
        if self.environment == 'TEST_PREVIEW':
            from greenhouse_owner_os.infrastructure.test_safety import require_disposable_database
            require_disposable_database(session.get_bind().url, 'test')

    def _entity(self, session, entity_id):
        self._check_target(session)
        entity = session.get(EntityRow, entity_id)
        if not entity:
            raise NotFoundError('entity not found')
        if entity.reference_class != self.environment:
            raise ValidationError('reference environment mismatch')
        return entity

    def _ensure(self, uow, *, entity_id, kind, key, name, source, definition, classification, actor_id, build):
        session = uow.session
        # Serialize semantic identity even across different request keys/actors.
        session.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key, 73))'),
                        {'key': f'{entity_id}/{kind}/{key}'})
        old = session.scalar(select(ReferenceRecordRow).where(
            ReferenceRecordRow.entity_id == entity_id, ReferenceRecordRow.kind == kind,
            ReferenceRecordRow.canonical_key == key))
        if old:
            if (old.original_name, old.definition, old.reference_class) != (name, definition, classification):
                raise ConflictError('reference identity already exists with different original name or definition')
            resource = session.get(RESOURCE_MODELS[kind], old.id)
            if not resource:
                raise ConflictError('orphan reference; report, never repair')
            return serialize(old)
        rid = entity_id if kind == 'entity' else reference_id(entity_id, kind, key)
        if session.get(RESOURCE_MODELS[kind], rid):
            raise ConflictError('unregistered resource collision; explicit verification required')
        build(session, rid)
        session.flush()
        row = ReferenceRecordRow(id=rid, entity_id=entity_id, kind=kind, canonical_key=key,
            original_name=name, source=source, definition=definition,
            reference_class=classification, created_by=actor_id)
        session.add(row)
        session.flush()
        response = serialize(row)
        uow.audit.append(actor_id=actor_id, action='reference_create', entity_type=kind,
                         entity_id=rid, before=None, after=response)
        return response

    def setup(self, *, kind, command, actor_id, idempotency_key, entity_id=None):
        if kind not in ('entity', 'money_account', 'counterparty'):
            raise ValidationError('unknown owner reference kind')
        operation = 'reference_setup_' + kind
        values = command.model_dump(mode='json')
        request_hash = _hash_request(dict(values, entity_id=str(entity_id)))
        with self.uow_factory() as uow:
            self._check_target(uow.session)
            actor = uow.session.get(ActorRow, actor_id)
            if not actor or not actor.active or actor.actor_type != 'owner':
                raise ValidationError('active authenticated owner actor required')
            uow.idempotency.lock(actor_id, operation, idempotency_key)
            replay = self.finance._replay_if_exists(uow, actor_id, operation, idempotency_key, request_hash)
            if replay:
                return replay
            key = canonical_key(command.name)
            if kind == 'entity':
                entity_id = reference_id(self.environment, kind, key)
            else:
                self._entity(uow.session, entity_id)
            definition = {k: v for k, v in values.items() if k not in ('name', 'source')}
            def build(session, rid):
                if kind == 'entity':
                    session.add(EntityRow(id=rid, name=command.name, default_currency=command.currency,
                                          reference_class=self.environment))
                elif kind == 'counterparty':
                    # Existing type field expresses the minimal Finance role; no ERP directory.
                    session.add(CounterpartyRow(id=rid, entity_id=entity_id, name=command.name,
                                                counterparty_type=command.role))
                else:
                    ledger_id = reference_id(entity_id, 'cash_ledger', key)
                    session.add(LedgerAccountRow(id=ledger_id, entity_id=entity_id, code='CASH:'+str(rid),
                        name=command.name, account_class='cash', normal_side='debit',
                        currency=command.currency, system_code=None))
                    session.flush()
                    session.add(MoneyAccountRow(id=rid, entity_id=entity_id, ledger_account_id=ledger_id,
                        name=command.name, account_type=command.account_type, currency=command.currency))
                    definition['ledger_account_id'] = str(ledger_id)
            # Money ledger identity is deterministic and part of the immutable definition.
            if kind == 'money_account':
                definition['ledger_account_id'] = str(reference_id(entity_id, 'cash_ledger', key))
            response = self._ensure(uow, entity_id=entity_id, kind=kind, key=key,
                name=command.name, source=command.source, definition=definition,
                classification=self.environment, actor_id=actor_id, build=build)
            uow.idempotency.put(actor_id=actor_id, operation=operation, key=idempotency_key,
                request_hash=request_hash, resource_type=kind, resource_id=UUID(response['id']), response=response)
            uow.commit()
            return response

    def bootstrap(self, *, entity_id, currency, actor_id, idempotency_key):
        if currency not in SUPPORTED_CURRENCIES:
            raise ValidationError('unsupported setup currency')
        operation = 'reference_bootstrap_v3'
        request_hash = _hash_request({'entity_id':str(entity_id),'currency':currency})
        with self.uow_factory() as uow:
            self._entity(uow.session, entity_id)
            self._check_target(uow.session)
            actor = uow.session.get(ActorRow, actor_id)
            if not actor or not actor.active or actor.actor_type != 'owner':
                raise ValidationError('active authenticated owner actor required')
            uow.idempotency.lock(actor_id, operation, idempotency_key)
            replay = self.finance._replay_if_exists(uow, actor_id, operation, idempotency_key, request_hash)
            if replay:
                return replay
            refs = []
            for code, (account_class, side) in SYSTEM_ACCOUNTS.items():
                definition = dict(currency=currency, system_code=code, account_class=account_class, normal_side=side)
                def build(session, rid, code=code, account_class=account_class, side=side):
                    if session.scalar(select(LedgerAccountRow.id).where(LedgerAccountRow.entity_id==entity_id,
                        LedgerAccountRow.currency==currency, LedgerAccountRow.system_code==code)):
                        raise ConflictError('existing unregistered system mapping; explicit verification required')
                    session.add(LedgerAccountRow(id=rid, entity_id=entity_id, code=code,
                        name=code, currency=currency, account_class=account_class,
                        normal_side=side, system_code=code))
                refs.append(self._ensure(uow, entity_id=entity_id, kind='ledger_account',
                    key=currency+'/'+code, name=code, source='system:finance-r1-bootstrap:v2',
                    definition=definition, classification='SYSTEM', actor_id=actor_id, build=build))
            response = {'entity_id':str(entity_id),'currency':currency,'references':refs,'financial_effect':'NONE'}
            uow.idempotency.put(actor_id=actor_id, operation=operation, key=idempotency_key,
                request_hash=request_hash, resource_type='entity', resource_id=entity_id, response=response)
            uow.commit()
            return response

    def read(self, entity_id):
        with self.uow_factory() as uow:
            self._entity(uow.session, entity_id)
            rows = uow.session.scalars(select(ReferenceRecordRow).where(
                ReferenceRecordRow.entity_id==entity_id).order_by(ReferenceRecordRow.kind, ReferenceRecordRow.canonical_key))
            return {'entity_id':str(entity_id),'environment':self.environment,'references':[serialize(r) for r in rows]}

    def readiness(self, entity_id):
        findings = []
        def fail(code, rid):
            findings.append({'code':code,'id':str(rid)})
        with self.uow_factory() as uow:
            s = uow.session
            s.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
            entity = self._entity(s, entity_id)
            refs = list(s.scalars(select(ReferenceRecordRow).where(ReferenceRecordRow.entity_id==entity_id)))
            ledgers = {r.id:r for r in s.scalars(select(LedgerAccountRow).where(LedgerAccountRow.entity_id==entity_id))}
            accounts = list(s.scalars(select(MoneyAccountRow).where(MoneyAccountRow.entity_id==entity_id)))
            cps = list(s.scalars(select(CounterpartyRow).where(CounterpartyRow.entity_id==entity_id)))
            if str(entity.finance_cutover_date) != '2026-10-04' or entity.finance_opening_policy not in ('ZERO','EXPLICIT_AP'):
                fail('cutover_configuration', entity_id)
            currencies = {entity.default_currency} | {a.currency for a in accounts if a.active}
            for currency in sorted(currencies):
                for code, expected in SYSTEM_ACCOUNTS.items():
                    found = [l for l in ledgers.values() if l.currency==currency and l.system_code==code]
                    if len(found)!=1 or not found[0].active or (found[0].account_class,found[0].normal_side)!=expected:
                        fail('system_mapping:'+currency+'/'+code,entity_id)
            ids = {r.id for r in refs}
            if entity_id not in ids:
                fail('unverified_entity',entity_id)
            for row in refs:
                resource = s.get(RESOURCE_MODELS[row.kind],row.id)
                if not resource:
                    fail('orphan_reference',row.id)
                if row.reference_class not in ('SYSTEM',self.environment):
                    fail('reference_environment',row.id)
                if row.kind=='ledger_account' and resource and row.definition != dict(currency=resource.currency,
                    system_code=resource.system_code, account_class=resource.account_class, normal_side=resource.normal_side):
                    fail('reference_definition_mismatch',row.id)
                if row.kind=='counterparty' and resource and resource.counterparty_type != row.definition['role']:
                    fail('reference_definition_mismatch',row.id)
                if row.kind=='entity' and resource and resource.default_currency != row.definition['currency']:
                    fail('reference_definition_mismatch',row.id)
                if row.kind=='money_account' and resource and (resource.currency,resource.account_type,str(resource.ledger_account_id)) != (row.definition['currency'],row.definition['account_type'],row.definition['ledger_account_id']):
                    fail('reference_definition_mismatch',row.id)
                if resource and getattr(resource,'name',None)!=row.original_name:
                    fail('reference_name_mismatch',row.id)
            for ledger in ledgers.values():
                if ledger.system_code and ledger.id not in ids:
                    fail('unverified_system_mapping',ledger.id)
            for account in accounts:
                ledger = ledgers.get(account.ledger_account_id)
                if not ledger or ledger.currency!=account.currency or ledger.account_class!='cash' or ledger.normal_side!='debit':
                    fail('money_account_ledger_mismatch',account.id)
                if account.id not in ids:
                    fail('unverified_money_account',account.id)
            for cp in cps:
                if cp.id not in ids:
                    fail('unverified_counterparty',cp.id)
            if not any(a.active for a in accounts):
                fail('money_account_required',entity_id)
            if not any(c.active for c in cps):
                fail('counterparty_required',entity_id)
            for posting in s.scalars(select(PostingRow).where(PostingRow.entity_id==entity_id)):
                if posting.event_date < entity.finance_cutover_date:
                    fail('precutover_posting',posting.id)
            from .reconciliation import reconcile
            # Snapshot already selected above: reconciliation accepts a caller-established snapshot.
            reconciliation = reconcile(s, snapshot_established=True, entity_id=entity_id)
            if not reconciliation['ok']:
                findings.extend(reconciliation['findings'])
            return dict(ok=not findings, findings=sorted(findings,key=lambda f:(f['code'],str(f))),
                entity_id=str(entity_id), environment=self.environment, opening_policy=entity.finance_opening_policy,
                opening_explanation=('No opening journal; current balances derive only from live operations.'
                    if entity.finance_opening_policy=='ZERO' else
                    'Zero cash/AR/advances; explicit owner-verified AP at cut-over uses opening offset, not expense/revenue. Original dates preserved.'),
                reconciliation=reconciliation)

    def audit(self, entity_id, resource_id):
        with self.uow_factory() as uow:
            self._entity(uow.session, entity_id)
            kinds = dict(RESOURCE_MODELS)
            from greenhouse_owner_os.infrastructure.models import ObligationRow, PaymentRow, TransferRow, FundingRow, SettlementRow, ApplicationRow
            kinds.update(obligation=ObligationRow,payment=PaymentRow,transfer=TransferRow,
                         owner_funding=FundingRow,direct_settlement=SettlementRow,advance_application=ApplicationRow)
            if not any((r:=uow.session.get(model,resource_id)) and
                       (r.id==entity_id if kind=='entity' else r.entity_id==entity_id) for kind,model in kinds.items()):
                raise NotFoundError('resource not in entity')
            rows = uow.session.scalars(select(AuditEventRow).where(AuditEventRow.entity_id==resource_id)
                                      .order_by(AuditEventRow.occurred_at,AuditEventRow.id))
            return {'resource_id':str(resource_id),'events':[dict(id=str(r.id),actor_id=str(r.actor_id),
                action=r.action,before=r.before_json,after=r.after_json,reason=r.reason,created_at=r.occurred_at.isoformat()) for r in rows]}


def provision_authenticated_owner(session_factory, *, external_ref, display_name, environment):
    """Trusted adapter hook after authentication; never accepts client actor IDs.

    No HTTP endpoint impersonates an owner. Authentication itself belongs to the
    deployment task. This deterministic hook removes manual SQL provisioning.
    """
    if environment not in ('OWNER_VERIFIED','TEST_PREVIEW') or not external_ref.strip() or not display_name.strip():
        raise ValueError('verified identity and explicit environment required')
    rid = reference_id(environment,'actor',external_ref)
    with session_factory.begin() as session:
        if environment=='TEST_PREVIEW':
            from greenhouse_owner_os.infrastructure.test_safety import require_disposable_database
            require_disposable_database(session.get_bind().url,'test')
        session.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key, 73))'), {'key':str(rid)})
        old = session.get(ActorRow,rid)
        if old:
            if (old.actor_type,old.external_ref,old.display_name,old.active) != ('owner',external_ref,display_name,True):
                raise ConflictError('authenticated actor identity differs; explicit verification required')
        else:
            session.add(ActorRow(id=rid,actor_type='owner',display_name=display_name,external_ref=external_ref))
            session.flush()
            from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyAuditRepository
            SqlAlchemyAuditRepository(session).append(actor_id=rid,action='reference_create',entity_type='actor',
                entity_id=rid,before=None,after={'id':str(rid),'display_name':display_name,'source':'authenticated:'+external_ref,'classification':environment})
    return rid
