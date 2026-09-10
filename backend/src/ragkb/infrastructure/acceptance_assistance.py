"""Source authorization, candidate contracts and independent semantic review."""

from __future__ import annotations

from typing import Any

from ragkb.adapters.acceptance_model import AcceptanceModel
from ragkb.api.support import document_search_context, ensure_document_readable
from ragkb.application.reading_scope import ReadingOptions
from ragkb.domain.acceptance import AcceptanceCase, fingerprint
from ragkb.domain.acceptance_points import Criterion, criteria_for
from ragkb.domain.auth import RequestPrincipal
from ragkb.domain.errors import InvalidProviderResponse
from ragkb.domain.uploads import ResourceNotFoundError
from ragkb.runtime_components import RuntimeComponents


class AcceptanceAssistance:
    def __init__(self, runtime: RuntimeComponents) -> None:
        self.runtime = runtime
        self.model = AcceptanceModel(runtime.settings, runtime.model_transport)

    def source_page(
        self, subject: RequestPrincipal, space: str, document: str, offset: int = 0
    ) -> dict[str, Any]:
        if self.runtime.repository.get_document_space(document) != space:
            raise ResourceNotFoundError(document)
        self.runtime.lifecycle_store.reload()
        record = self.runtime.lifecycle_store.documents[document]
        version = record.active_version_id
        if not version:
            raise ValueError("SOURCE_HAS_NO_PUBLISHED_VERSION")
        ensure_document_readable(self.runtime, document, subject, version)
        context = document_search_context(self.runtime, subject, space)
        page = self.runtime.repository.list_chunks_page(
            version, limit=100, offset=offset, context=context, preview=False
        )
        allowed = self.runtime.search_service.control_plane.authorize_chunks(
            tuple(str(row["chunk_id"]) for row in page.items), context
        )
        return {
            "document_id": document,
            "version_id": version,
            "next_offset": offset + 100 if page.next_key else None,
            "items": [
                {
                    "chunk_id": chunk.chunk_id,
                    "document_id": document,
                    "version_id": version,
                    "text": chunk.display_text,
                    "locator": chunk.locator,
                }
                for row in page.items
                if (chunk := allowed.get(row["chunk_id"])) is not None
                and chunk.document_id == document
                and chunk.document_version_id == version
            ],
        }

    def bindings(
        self, subject: RequestPrincipal, sources: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        result = []
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for source in sources:
            grouped.setdefault((source["document_id"], source["version_id"]), []).append(source)
        for (document, version), rows in grouped.items():
            ensure_document_readable(self.runtime, document, subject, version)
            space = self.runtime.repository.get_document_space(document)
            context = document_search_context(self.runtime, subject, space)
            allowed = self.runtime.search_service.control_plane.authorize_chunks(
                tuple(r["chunk_id"] for r in rows), context
            )
            for row in rows:
                chunk = allowed.get(row["chunk_id"])
                if (
                    chunk is None
                    or chunk.document_id != document
                    or chunk.document_version_id != version
                ):
                    raise ResourceNotFoundError(row["chunk_id"])
                quote = row.get("quote", chunk.display_text)
                if not quote.strip() or quote not in chunk.display_text:
                    raise ValueError("CASE_SOURCE_QUOTE_NOT_IN_ORIGINAL")
                result.append({**row, "quote": quote, "locator": chunk.locator})
        return result

    def generate(
        self, sources: list[dict[str, Any]], count: int, identity: str
    ) -> list[dict[str, Any]]:
        pool = {f"S{i}": source for i, source in enumerate(sources, 1)}
        output = self.model.call("generate", {"requested_count": count, "sources": pool})
        cases = output.get("cases")
        if not isinstance(cases, list) or not 1 <= len(cases) <= count:
            raise InvalidProviderResponse("CANDIDATE_COUNT_INVALID")
        result = []
        for i, candidate in enumerate(cases, 1):
            points = []
            for j, point in enumerate(candidate.get("criteria", []), 1):
                bindings = []
                for source in point.get("sources", []):
                    original = pool.get(source.get("source_id"))
                    quote = source.get("quote")
                    if (
                        not original
                        or not isinstance(quote, str)
                        or not quote.strip()
                        or quote not in original["quote"]
                    ):
                        raise InvalidProviderResponse("CANDIDATE_SOURCE_WITNESS_INVALID")
                    bindings.append(
                        {k: original[k] for k in ("document_id", "version_id", "chunk_id")}
                        | {"quote": quote}
                    )
                if not bindings:
                    raise InvalidProviderResponse("CANDIDATE_SOURCE_REQUIRED")
                points.append(
                    {"id": f"P{j}", "text": point["text"], "kind": "required", "sources": bindings}
                )
            if not 1 <= len(points) <= 12:
                raise InvalidProviderResponse("CANDIDATE_POINTS_INVALID")
            docs = sorted({s["document_id"] for p in points for s in p["sources"]})
            result.append(
                AcceptanceCase(
                    key=f"AI-{identity[-12:]}-{i}",
                    question=candidate["question"],
                    criteria=[Criterion.model_validate(p) for p in points],
                    reading=ReadingOptions(mode="fact", document_ids=tuple(docs)),
                    required_source_documents=docs,
                    source_notes="由选定原文生成的候选题，请逐项核对后确认。",
                ).model_dump(mode="json")
            )
        if len({fingerprint(c["question"].strip()) for c in result}) != len(result):
            raise InvalidProviderResponse("CANDIDATE_QUESTIONS_DUPLICATED")
        return result

    def review_batch(
        self, question: str, answer: str, points: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        prepared = []
        for point in points:
            prepared.append(
                {
                    **point,
                    "sources": {f"S{i}": source for i, source in enumerate(point["sources"], 1)},
                }
            )
        value = self.model.call(
            "review", {"question": question, "answer": answer, "criteria": prepared}
        )
        rows = value.get("points")
        if not isinstance(rows, list) or len(rows) != len(points):
            raise InvalidProviderResponse("POINT_REVIEW_COUNT_INVALID")
        result = []
        for expected, row in zip(prepared, rows, strict=True):
            status, quote = row.get("status"), row.get("answer_quote", "")
            source = expected["sources"].get(row.get("source_id"))
            witness = row.get("source_quote", "")
            if (
                row.get("point_id") != expected["id"]
                or status not in {"covered", "missing", "incorrect", "pending_review"}
                or not isinstance(quote, str)
                or (quote and quote not in answer)
                or not isinstance(row.get("note"), str)
                or not row["note"].strip()
            ):
                raise InvalidProviderResponse("POINT_REVIEW_WITNESS_INVALID")
            relevance = expected["kind"] == "relevance"
            requires_answer = status == "incorrect" or (
                status == "covered" and expected["kind"] not in {"forbidden", "relevance"}
            )
            if status != "pending_review" and (
                (requires_answer and not quote.strip())
                or (
                    expected["sources"]
                    and not relevance
                    and (
                        not source
                        or not isinstance(witness, str)
                        or not witness.strip()
                        or witness not in source["quote"]
                    )
                )
                or (not expected["sources"] and expected["kind"] == "required")
                or (status == "missing" and quote)
                or (relevance and status == "missing")
            ):
                raise InvalidProviderResponse("POINT_REVIEW_WITNESS_INVALID")
            result.append(
                {
                    "point_id": row["point_id"],
                    "status": status,
                    "answer_quote": quote,
                    "source_quote": witness,
                    "source_id": row.get("source_id", ""),
                    "note": row["note"][:2000],
                    "origin": "model",
                    "revision": self.model.revision,
                }
            )
        return result

    def review_points(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        return criteria_for(spec)
