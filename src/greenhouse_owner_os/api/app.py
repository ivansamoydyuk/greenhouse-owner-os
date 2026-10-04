from __future__ import annotations

from greenhouse_owner_os.domain.references import EntitySetup, MoneyAccountSetup, CounterpartySetup, BootstrapCommand, SUPPORTED_CURRENCIES
from greenhouse_owner_os.domain.opening import OpeningPayableCommand
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException

from greenhouse_owner_os.domain.history import ImportBatchCommand, HistoricalCandidateCommand
from greenhouse_owner_os.domain.cash import TransferCommand, FundingCommand, SettlementCommand, SupplierAdvanceCommand, AdvanceApplicationCommand
from greenhouse_owner_os.api.schemas import ObligationCreateBody, PaymentCreateBody
from greenhouse_owner_os.domain.commands import AllocationInput, CreateObligationCommand, CreatePaymentCommand, ReverseCommand
from greenhouse_owner_os.domain.errors import ConflictError, DomainError, NotFoundError, ValidationError
from greenhouse_owner_os.services.finance import FinanceService


def create_app(*, finance_service: FinanceService, actor_provider, reference_service=None) -> FastAPI:
    app = FastAPI(title="Greenhouse Owner OS Owner Core", version="0.1.0")

    @app.exception_handler(NotFoundError)
    async def not_found_handler(_, exc: NotFoundError):
        return _json_error(404, "not_found", str(exc))

    @app.exception_handler(ConflictError)
    async def conflict_handler(_, exc: ConflictError):
        return _json_error(409, "conflict", str(exc))

    @app.exception_handler(ValidationError)
    async def validation_handler(_, exc: ValidationError):
        return _json_error(422, "business_validation", str(exc))

    if reference_service is not None:

        @app.get('/v1/reference-currencies')
        def reference_currencies(actor_id: UUID = Depends(actor_provider)):
            return {'currencies': SUPPORTED_CURRENCIES, 'fx_supported': False}

        @app.post('/v1/entities', status_code=201)
        def setup_entity(body: EntitySetup, idempotency_key: str = Header(alias='Idempotency-Key'), actor_id: UUID = Depends(actor_provider)):
            return reference_service.setup(kind='entity',command=body,actor_id=actor_id,idempotency_key=idempotency_key)

        @app.post('/v1/entities/{entity_id}/money-accounts', status_code=201)
        def setup_money_account(entity_id: UUID, body: MoneyAccountSetup, idempotency_key: str = Header(alias='Idempotency-Key'), actor_id: UUID = Depends(actor_provider)):
            return reference_service.setup(kind='money_account',entity_id=entity_id,command=body,actor_id=actor_id,idempotency_key=idempotency_key)

        @app.post('/v1/entities/{entity_id}/counterparties', status_code=201)
        def setup_counterparty(entity_id: UUID, body: CounterpartySetup, idempotency_key: str = Header(alias='Idempotency-Key'), actor_id: UUID = Depends(actor_provider)):
            return reference_service.setup(kind='counterparty',entity_id=entity_id,command=body,actor_id=actor_id,idempotency_key=idempotency_key)

        @app.post('/v1/entities/{entity_id}/reference-bootstrap')
        def reference_bootstrap(entity_id: UUID, body: BootstrapCommand, idempotency_key: str = Header(alias='Idempotency-Key'), actor_id: UUID = Depends(actor_provider)):
            return reference_service.bootstrap(entity_id=entity_id,currency=body.currency,actor_id=actor_id,idempotency_key=idempotency_key)

        @app.get('/v1/entities/{entity_id}/references')
        def references(entity_id: UUID, actor_id: UUID = Depends(actor_provider)):
            return reference_service.read(entity_id)

        @app.get('/v1/entities/{entity_id}/readiness')
        def readiness(entity_id: UUID, actor_id: UUID = Depends(actor_provider)):
            return reference_service.readiness(entity_id)

        @app.get('/v1/entities/{entity_id}/resources/{resource_id}/audit')
        def audit(entity_id: UUID, resource_id: UUID, actor_id: UUID = Depends(actor_provider)):
            return reference_service.audit(entity_id,resource_id)

    @app.get('/v1/payments/{payment_id}')
    def get_payment(payment_id: UUID, actor_id: UUID = Depends(actor_provider)):
        from greenhouse_owner_os.services.finance import _serialize_payment
        with finance_service.uow_factory() as uow:
            payment = uow.finance.get_payment(payment_id)
            if not payment:
                raise NotFoundError('payment not found')
            return _serialize_payment(payment,uow.finance.get_advance_by_payment(payment_id))

    @app.get('/v1/finance/reconciliation')
    def reconciliation(entity_id: UUID | None = None, actor_id: UUID = Depends(actor_provider)):
        return finance_service.reconcile(include_classification=True, entity_id=entity_id)

    @app.get('/v1/entities/{entity_id}/cutover')
    def cutover(entity_id: UUID, actor_id: UUID = Depends(actor_provider)):
        return finance_service.get_cutover_status(entity_id)

    @app.post('/v1/entities/{entity_id}/opening-payables', status_code=201)
    def recognize_opening_payable(entity_id: UUID, body: OpeningPayableCommand, idempotency_key: str = Header(alias='Idempotency-Key'), actor_id: UUID = Depends(actor_provider)):
        return finance_service.recognize_opening_payable(entity_id=entity_id,actor_id=actor_id,
            idempotency_key=idempotency_key,command=body)

    @app.post('/v1/import-batches', status_code=201)
    def import_batch(body: ImportBatchCommand, idempotency_key: str = Header(alias='Idempotency-Key'), actor_id: UUID = Depends(actor_provider)):
        return finance_service.create_import_batch(actor_id=actor_id, idempotency_key=idempotency_key, command=body)

    @app.get('/v1/import-batches/{batch_id}')
    def batch_provenance(batch_id: UUID, actor_id: UUID = Depends(actor_provider)):
        return finance_service.get_import_batch(batch_id)

    @app.post('/v1/import-batches/{batch_id}/candidates', status_code=201)
    def candidate(batch_id: UUID, body: HistoricalCandidateCommand, idempotency_key: str = Header(alias='Idempotency-Key'), actor_id: UUID = Depends(actor_provider)):
        return finance_service.import_historical_candidate(actor_id=actor_id, idempotency_key=idempotency_key, batch_id=batch_id, command=body)

    @app.get('/v1/historical-candidates/{candidate_id}')
    def candidate_provenance(candidate_id: UUID, actor_id: UUID = Depends(actor_provider)):
        return finance_service.get_historical_candidate(candidate_id)

    @app.post("/v1/obligations", status_code=201)
    def create_obligation(body: ObligationCreateBody, idempotency_key: str = Header(alias="Idempotency-Key"), actor_id: UUID = Depends(actor_provider)):
        return finance_service.create_obligation(
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            command=CreateObligationCommand(**body.model_dump()),
        )

    @app.post("/v1/obligations/{obligation_id}/post")
    def post_obligation(obligation_id: UUID, expected_version: int, idempotency_key: str = Header(alias="Idempotency-Key"), actor_id: UUID = Depends(actor_provider)):
        return finance_service.post_obligation(
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            obligation_id=obligation_id,
            expected_version=expected_version,
        )

    @app.get("/v1/obligations/{obligation_id}")
    def get_obligation(obligation_id: UUID, actor_id: UUID = Depends(actor_provider)):
        return finance_service.get_obligation(obligation_id)

    @app.post("/v1/payments", status_code=201)
    def create_payment(body: PaymentCreateBody, idempotency_key: str = Header(alias="Idempotency-Key"), actor_id: UUID = Depends(actor_provider)):
        command = CreatePaymentCommand(
            **body.model_dump(exclude={"allocations"}),
            allocations=tuple(AllocationInput(**item.model_dump()) for item in body.allocations),
        )
        return finance_service.create_payment(actor_id=actor_id, idempotency_key=idempotency_key, command=command)

    @app.post("/v1/payments/{payment_id}/post")
    def post_payment(payment_id: UUID, expected_version: int, idempotency_key: str = Header(alias="Idempotency-Key"), actor_id: UUID = Depends(actor_provider)):
        return finance_service.post_payment(
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            payment_id=payment_id,
            expected_version=expected_version,
        )

    @app.post("/v1/payments/{payment_id}/reverse")
    def reverse_payment(payment_id: UUID, body: ReverseCommand, idempotency_key: str = Header(alias="Idempotency-Key"), actor_id: UUID = Depends(actor_provider)):
        return finance_service.reverse_payment(actor_id=actor_id, idempotency_key=idempotency_key, payment_id=payment_id, command=body)

    @app.post("/v1/obligations/{obligation_id}/reverse")
    def reverse_obligation(obligation_id: UUID, body: ReverseCommand, idempotency_key: str = Header(alias="Idempotency-Key"), actor_id: UUID = Depends(actor_provider)):
        return finance_service.reverse_obligation(actor_id=actor_id, idempotency_key=idempotency_key, obligation_id=obligation_id, command=body)

    @app.get("/v1/payments/{payment_id}/linkage")
    def payment_linkage(payment_id: UUID, actor_id: UUID = Depends(actor_provider)):
        return finance_service.get_linkage("payment", payment_id)

    @app.get("/v1/obligations/{obligation_id}/linkage")
    def obligation_linkage(obligation_id: UUID, actor_id: UUID = Depends(actor_provider)):
        return finance_service.get_linkage("obligation", obligation_id)

    @app.get("/v1/entities/{entity_id}/balances")
    def balances(entity_id: UUID, actor_id: UUID = Depends(actor_provider)):
        return finance_service.get_currency_balances(entity_id)


    @app.post('/v1/transfers', status_code=201)
    def create_transfer(body: TransferCommand, idempotency_key: str = Header(alias='Idempotency-Key'), actor_id: UUID = Depends(actor_provider)):
        return finance_service.create_cash(kind='transfer', actor_id=actor_id, idempotency_key=idempotency_key, command=body)

    @app.post('/v1/transfers/{resource_id}/post')
    def post_transfer(resource_id: UUID, expected_version: int, idempotency_key: str = Header(alias='Idempotency-Key'), actor_id: UUID = Depends(actor_provider)):
        return finance_service.post_cash(kind='transfer', resource_id=resource_id, actor_id=actor_id, idempotency_key=idempotency_key, expected_version=expected_version)

    @app.post('/v1/transfers/{resource_id}/reverse')
    def reverse_transfer(resource_id: UUID, body: ReverseCommand, idempotency_key: str = Header(alias='Idempotency-Key'), actor_id: UUID = Depends(actor_provider)):
        return finance_service.reverse_cash(kind='transfer', resource_id=resource_id, actor_id=actor_id, idempotency_key=idempotency_key, command=body)

    @app.get('/v1/transfers/{resource_id}/linkage')
    def linkage_transfer(resource_id: UUID, actor_id: UUID = Depends(actor_provider)):
        return finance_service.get_linkage('transfer', resource_id)

    @app.post('/v1/owner-fundings', status_code=201)
    def create_owner_funding(body: FundingCommand, idempotency_key: str = Header(alias='Idempotency-Key'), actor_id: UUID = Depends(actor_provider)):
        return finance_service.create_cash(kind='owner_funding', actor_id=actor_id, idempotency_key=idempotency_key, command=body)

    @app.post('/v1/owner-fundings/{resource_id}/post')
    def post_owner_funding(resource_id: UUID, expected_version: int, idempotency_key: str = Header(alias='Idempotency-Key'), actor_id: UUID = Depends(actor_provider)):
        return finance_service.post_cash(kind='owner_funding', resource_id=resource_id, actor_id=actor_id, idempotency_key=idempotency_key, expected_version=expected_version)

    @app.post('/v1/owner-fundings/{resource_id}/reverse')
    def reverse_owner_funding(resource_id: UUID, body: ReverseCommand, idempotency_key: str = Header(alias='Idempotency-Key'), actor_id: UUID = Depends(actor_provider)):
        return finance_service.reverse_cash(kind='owner_funding', resource_id=resource_id, actor_id=actor_id, idempotency_key=idempotency_key, command=body)

    @app.get('/v1/owner-fundings/{resource_id}/linkage')
    def linkage_owner_funding(resource_id: UUID, actor_id: UUID = Depends(actor_provider)):
        return finance_service.get_linkage('owner_funding', resource_id)

    @app.post('/v1/direct-settlements', status_code=201)
    def create_direct_settlement(body: SettlementCommand, idempotency_key: str = Header(alias='Idempotency-Key'), actor_id: UUID = Depends(actor_provider)):
        return finance_service.create_cash(kind='direct_settlement', actor_id=actor_id, idempotency_key=idempotency_key, command=body)

    @app.post('/v1/direct-settlements/{resource_id}/post')
    def post_direct_settlement(resource_id: UUID, expected_version: int, idempotency_key: str = Header(alias='Idempotency-Key'), actor_id: UUID = Depends(actor_provider)):
        return finance_service.post_cash(kind='direct_settlement', resource_id=resource_id, actor_id=actor_id, idempotency_key=idempotency_key, expected_version=expected_version)

    @app.post('/v1/direct-settlements/{resource_id}/reverse')
    def reverse_direct_settlement(resource_id: UUID, body: ReverseCommand, idempotency_key: str = Header(alias='Idempotency-Key'), actor_id: UUID = Depends(actor_provider)):
        return finance_service.reverse_cash(kind='direct_settlement', resource_id=resource_id, actor_id=actor_id, idempotency_key=idempotency_key, command=body)

    @app.get('/v1/direct-settlements/{resource_id}/linkage')
    def linkage_direct_settlement(resource_id: UUID, actor_id: UUID = Depends(actor_provider)):
        return finance_service.get_linkage('direct_settlement', resource_id)

    @app.post('/v1/supplier-advances', status_code=201)
    def create_supplier_advance(body: SupplierAdvanceCommand, idempotency_key: str = Header(alias='Idempotency-Key'), actor_id: UUID = Depends(actor_provider)):
        return finance_service.create_supplier_advance(actor_id=actor_id, idempotency_key=idempotency_key, command=body)

    @app.post('/v1/supplier-advances/{payment_id}/post')
    def post_supplier_advance(payment_id: UUID, expected_version: int, idempotency_key: str = Header(alias='Idempotency-Key'), actor_id: UUID = Depends(actor_provider)):
        return finance_service.post_supplier_advance(payment_id=payment_id, actor_id=actor_id, idempotency_key=idempotency_key, expected_version=expected_version)

    @app.post('/v1/supplier-advances/{payment_id}/reverse')
    def reverse_supplier_advance(payment_id: UUID, body: ReverseCommand, idempotency_key: str = Header(alias='Idempotency-Key'), actor_id: UUID = Depends(actor_provider)):
        return finance_service.reverse_payment(payment_id=payment_id, actor_id=actor_id, idempotency_key=idempotency_key, command=body)

    @app.post('/v1/supplier-advance-applications', status_code=201)
    def apply_supplier(body: AdvanceApplicationCommand, idempotency_key: str = Header(alias='Idempotency-Key'), actor_id: UUID = Depends(actor_provider)):
        if body.direction != 'paid': raise ValidationError('supplier application requires paid advance')
        return finance_service.apply_advance(actor_id=actor_id,idempotency_key=idempotency_key,command=body)

    @app.post('/v1/customer-advance-applications', status_code=201)
    def apply_customer(body: AdvanceApplicationCom…25405 tokens truncated…al(row['amount'])
                result['opening_balances']['payable'] = {currency:f'{amount:.2f}' for currency,amount in sorted(by_currency.items())}
                result['opening_postings_generated'] = True
                result['opening_payables'] = openings
                result['opening_balance_policy'] = 'ZERO_OTHER_POSITIONS_WITH_EXPLICIT_VERIFIED_PAYABLES'
            return result

    def create_import_batch(self, *, actor_id, idempotency_key, command):
        return self._import_evidence('import_batch', actor_id, idempotency_key, command)

    def import_historical_candidate(self, *, actor_id, idempotency_key, batch_id, command):
        return self._import_evidence('historical_candidate', actor_id, idempotency_key, command, batch_id)

    def _import_evidence(self, kind, actor_id, key, command, batch_id=None):
        from .finance import _hash_request
        operation = 'import_' + kind
        payload = command.model_dump(mode='json')
        if kind == 'historical_candidate' and command.confidence is not None:
            payload['confidence'] = str(command.confidence.normalize())
        if batch_id is not None: payload['import_batch_id'] = str(batch_id)
        request_hash = _hash_request(payload)
        with self.uow_factory() as uow:
            uow.idempotency.lock(actor_id, operation, key)
            replay = self._replay_if_exists(uow, actor_id, operation, key, request_hash)
            if replay: return replay
            values = command.model_dump(mode='json' if kind == 'historical_candidate' else 'python')
            if kind == 'import_batch':
                uow.finance.get_cutover(command.entity_id)
                response, created = uow.finance.ensure_import_batch({**values, 'created_by':actor_id})
            else:
                values = command.model_dump()
                # Locator identity uses both original components; never amount/date
                # or import timestamp. Same source locator with changed content conflicts.
                source_key = _hash_request({'source_row':command.source_row, 'source_document':command.source_document})
                evidence = command.model_dump(mode='json')
                # Decimal representation must not create false retry/content collisions.
                if command.confidence is not None: evidence['confidence'] = str(command.confidence.normalize())
                response, created = uow.finance.ensure_historical_candidate({**values, 'import_batch_id':batch_id,
                    'source_key':source_key, 'content_hash':_hash_request(evidence), 'created_by':actor_id})
            if created:
                uow.audit.append(actor_id=actor_id, action='import', entity_type=kind,
                    entity_id=UUID(response['id']), before=None, after=response)
            uow.idempotency.put(actor_id=actor_id, operation=operation, key=key, request_hash=request_hash,
                resource_type=kind, resource_id=UUID(response['id']), response=response)
            uow.commit()
            return response

    def get_import_batch(self, batch_id):
        with self.uow_factory() as uow:
            return {**uow.finance.get_import_batch(batch_id), 'posting_policy':'NON_POSTING',
                    'candidates':uow.finance.historical_candidates(batch_id)}

    def get_historical_candidate(self, candidate_id):
        with self.uow_factory() as uow:
            return uow.finance.get_historical_candidate(candidate_id)
