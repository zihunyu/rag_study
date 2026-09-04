"""Select a runtime factory before any profile-specific component is created."""

from ragkb.config import EnvSettings
from ragkb.runtime_profiles.contracts import RuntimeProfileFactory
from ragkb.runtime_profiles.local import LocalRuntimeFactory
from ragkb.runtime_profiles.production import ProductionRuntimeFactory


def select_runtime_factory(settings: EnvSettings) -> RuntimeProfileFactory:
    if settings.rag_runtime_profile == "production":
        return ProductionRuntimeFactory()
    if settings.rag_runtime_profile == "local":
        return LocalRuntimeFactory()
    raise RuntimeError(f"UNSUPPORTED_RUNTIME_PROFILE:{settings.rag_runtime_profile}")
