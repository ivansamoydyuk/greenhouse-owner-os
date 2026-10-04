class DomainError(Exception):
    """Base class for deterministic business-rule failures."""


class NotFoundError(DomainError):
    pass


class ConflictError(DomainError):
    pass


class ValidationError(DomainError):
    pass


class IdempotencyConflictError(ConflictError):
    pass
