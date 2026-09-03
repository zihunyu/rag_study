"""Typed failures used to distinguish degradable provider faults from invariant bugs."""

from __future__ import annotations


class RAGError(RuntimeError):
    """Base class for expected RAG failures with a stable, non-secret code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class TransientProviderError(RAGError):
    """A timeout, rate limit, or temporary upstream outage that may be degraded."""


class ProviderTimeout(TransientProviderError):
    pass


class ProviderRateLimited(TransientProviderError):
    pass


class ProviderUnavailable(TransientProviderError):
    pass


class ProviderCircuitOpen(TransientProviderError):
    pass


class InvalidProviderResponse(RAGError):
    """The provider replied, but violated the configured response contract."""


class ConfigurationError(RAGError):
    pass


class SchemaMismatch(RAGError):
    pass


class RetrievalFailClosed(RAGError):
    """Retrieval or permission evaluation could not safely produce evidence."""


class GenerationUnavailable(TransientProviderError):
    pass
