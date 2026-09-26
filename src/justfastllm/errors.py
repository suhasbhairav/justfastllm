from __future__ import annotations


class GatewayError(Exception):
    status_code = 500
    code = "gateway_error"

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code


class BadRequestError(GatewayError):
    status_code = 400
    code = "bad_request"


class ProviderNotFoundError(GatewayError):
    status_code = 404
    code = "provider_not_found"


class ProviderAuthError(GatewayError):
    status_code = 401
    code = "provider_auth_missing"


class UnauthorizedError(GatewayError):
    status_code = 401
    code = "unauthorized"


class ForbiddenError(GatewayError):
    status_code = 403
    code = "forbidden"


class ProviderConfigurationError(GatewayError):
    status_code = 500
    code = "provider_misconfigured"


class UpstreamTransportError(GatewayError):
    status_code = 502
    code = "upstream_transport_error"


class GuardrailViolationError(GatewayError):
    status_code = 422
    code = "guardrail_violation"


class PolicyViolationError(GatewayError):
    status_code = 403
    code = "policy_violation"


class PayloadTooLargeError(GatewayError):
    status_code = 413
    code = "payload_too_large"


class RateLimitError(GatewayError):
    status_code = 429
    code = "rate_limit_exceeded"


class BudgetExceededError(GatewayError):
    status_code = 429
    code = "budget_exceeded"
