"""Plan crop dependencies in the normal text evidence selection request."""

from typing import Any

from ragkb.adapters.evidence_selection import ModelEvidenceSelector
from ragkb.adapters.model_http import JsonTransport
from ragkb.config import EnvSettings
from ragkb.contracts.rag import EvidenceSelection
from ragkb.domain.errors import InvalidProviderResponse
from ragkb.domain.rag import Evidence


class VisualRelevancePlanner:
    revision = "visual-dependency-plan-v2-shared-text-selection"

    def __init__(self, settings: EnvSettings, transport: JsonTransport | None) -> None:
        self.selector = ModelEvidenceSelector(settings, transport)

    def plan(
        self, question: str, assets: list[dict[str, Any]], text_sources: tuple[Evidence, ...]
    ) -> tuple[set[str], EvidenceSelection]:
        selection, checks = self.selector.select_with_plan(question, text_sources, assets)
        try:
            if len(checks) != len(assets):
                raise ValueError()
            text_ids = {s.evidence_id for s in text_sources}
            excluded = set()
            for asset, check in zip(assets, checks, strict=True):
                if (
                    check["id"] != asset["id"]
                    or check["decision"] not in {"required", "uncertain", "unrelated_scope"}
                    or not isinstance(check["reason"], str)
                    or not check["reason"].strip()
                ):
                    raise ValueError()
                if check["decision"] == "unrelated_scope":
                    if check.get("text_source_id") not in text_ids:
                        raise ValueError()
                    excluded.add(asset["id"])
            return excluded, selection
        except (ValueError, KeyError, TypeError) as error:
            raise InvalidProviderResponse("VISUAL_DEPENDENCY_PLAN_INVALID") from error
