"""Aggregate governance routers without coupling their handlers."""

from fastapi import APIRouter

from ragkb.api.routers.acceptance import build_acceptance_router
from ragkb.api.routers.operations import build_operations_router
from ragkb.api.routers.pilots import build_pilots_router
from ragkb.runtime_components import RuntimeComponents


def build_governance_router(runtime: RuntimeComponents) -> APIRouter:
    router = APIRouter()
    router.include_router(build_operations_router(runtime))
    router.include_router(build_pilots_router(runtime))
    router.include_router(build_acceptance_router(runtime))
    return router
