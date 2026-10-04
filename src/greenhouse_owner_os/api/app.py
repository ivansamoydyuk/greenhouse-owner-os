from __future__ import annotations

from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException

from greenhouse_owner_os.api.schemas import ObligationCreateBody, PaymentCreateBody
from greenhouse_owner_os.domain.commands import AllocationInput, CreateObligationCommand, CreatePaymentCommand
from greenhouse_owner_os.domain.errors import ConflictError, DomainError, NotFoundError, ValidationError
from greenhouse_owner_os.services.finance import FinanceService


def create_app(*, finance_service: FinanceService, actor_provider) -> FastAPI:
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

    return app


def _json_error(status: int, code: str, message: str):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})
