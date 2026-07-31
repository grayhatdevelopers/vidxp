from __future__ import annotations

import hashlib
import json

from vidxp.application_models import (
    ActorEvidence,
    DraftAnswer,
    Evidence,
    FusedSearchResult,
    GroundedClaim,
    IndexSnapshotReference,
    MomentEvidence,
    QueryAnswer,
    QueryAnswerMode,
    QueryPlan,
    QueryPlanningRequest,
    QuerySynthesisRequest,
    QueryVideoCommand,
    SearchMomentsPlanStep,
    ActorOverviewPlanStep,
)
from vidxp.capabilities.actor.schemas import ActorClusterSummary
from vidxp.ports import QueryModelPort, QueryProviderError


def _evidence_id(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _default_plan(
    command: QueryVideoCommand,
    *,
    search_modalities: tuple[str, ...],
    actor_overview: bool,
) -> QueryPlan:
    steps = [
        SearchMomentsPlanStep(
            modality=modality,
            query=command.question,
        )
        for modality in search_modalities
    ]
    if actor_overview:
        steps.append(ActorOverviewPlanStep())
    return QueryPlan(steps=tuple(steps))


def _valid_plan(
    plan: QueryPlan,
    *,
    search_modalities: tuple[str, ...],
    actor_overview: bool,
) -> bool:
    searches = [
        step.modality
        for step in plan.steps
        if isinstance(step, SearchMomentsPlanStep)
    ]
    actor_steps = sum(
        isinstance(step, ActorOverviewPlanStep) for step in plan.steps
    )
    return (
        len(searches) == len(set(searches))
        and set(searches) == set(search_modalities)
        and actor_steps == int(actor_overview)
    )


class GroundedQueryService:
    """Apply bounded model assistance to application-owned retrieval evidence."""

    def __init__(self, model: QueryModelPort | None = None) -> None:
        self.model = model

    def plan(
        self,
        command: QueryVideoCommand,
        *,
        search_modalities: tuple[str, ...],
        actor_overview: bool,
    ) -> tuple[QueryPlan, str | None]:
        fallback = _default_plan(
            command,
            search_modalities=search_modalities,
            actor_overview=actor_overview,
        )
        if self.model is None:
            return fallback, "query_model_not_configured"
        try:
            proposed = self.model.plan(
                QueryPlanningRequest(
                    question=command.question,
                    allowed_modalities=search_modalities,
                    actor_overview_allowed=actor_overview,
                )
            )
        except QueryProviderError:
            return fallback, "query_model_unavailable"
        if not _valid_plan(
            proposed,
            search_modalities=search_modalities,
            actor_overview=actor_overview,
        ):
            return fallback, "query_plan_rejected"
        return proposed, None

    @staticmethod
    def evidence(
        *,
        snapshot: IndexSnapshotReference,
        fused: FusedSearchResult,
        actors: tuple[ActorClusterSummary, ...],
    ) -> tuple[Evidence, ...]:
        items: list[Evidence] = []
        evidence_ids: set[str] = set()
        for moment in fused.moments:
            for hit in moment.hits:
                display_text = hit.metadata.get("text")
                if not isinstance(display_text, str) or not display_text.strip():
                    display_text = None
                identity = {
                    "kind": "moment",
                    "snapshot_id": snapshot.snapshot_id,
                    "media_id": hit.media_id,
                    "generation_id": hit.generation_id,
                    "modality": hit.modality,
                    "source_id": hit.source_id,
                    "start": hit.start,
                    "end": hit.end,
                }
                evidence_id = _evidence_id(identity)
                if evidence_id in evidence_ids:
                    continue
                evidence_ids.add(evidence_id)
                items.append(
                    MomentEvidence(
                        evidence_id=evidence_id,
                        snapshot_id=snapshot.snapshot_id,
                        display_text=display_text,
                        hit=hit,
                        **{
                            key: value
                            for key, value in identity.items()
                            if key not in {"kind", "snapshot_id"}
                        },
                    )
                )
                if len(items) == 200:
                    return tuple(items)
        for actor in actors:
            identity = {
                "kind": "actor",
                "snapshot_id": snapshot.snapshot_id,
                "media_id": actor.media_id,
                "generation_id": actor.generation_id,
                "cluster_id": actor.cluster_id,
                "start": actor.first_timestamp,
                "end": actor.last_timestamp,
            }
            evidence_id = _evidence_id(identity)
            if evidence_id in evidence_ids:
                continue
            evidence_ids.add(evidence_id)
            items.append(
                ActorEvidence(
                    evidence_id=evidence_id,
                    snapshot_id=snapshot.snapshot_id,
                    media_id=actor.media_id,
                    generation_id=actor.generation_id,
                    cluster_id=actor.cluster_id,
                    start=actor.first_timestamp,
                    end=actor.last_timestamp,
                    detection_count=actor.detection_count,
                    display_text=(
                        f"Actor cluster {actor.cluster_id} appears "
                        f"{actor.detection_count} times from "
                        f"{actor.first_timestamp:.3f}s to "
                        f"{actor.last_timestamp:.3f}s."
                    ),
                )
            )
            if len(items) == 200:
                break
        return tuple(items)

    def answer(
        self,
        command: QueryVideoCommand,
        *,
        plan: QueryPlan,
        planning_fallback: str | None,
        evidence: tuple[Evidence, ...],
        fused: FusedSearchResult,
    ) -> QueryAnswer:
        common = {
            "question": command.question,
            "plan": plan,
            "model": self.model.identity if self.model is not None else None,
            "evidence": evidence,
            "moments": fused.moments,
            "fusion": fused.fusion,
        }
        if not evidence:
            return QueryAnswer(
                mode=QueryAnswerMode.no_evidence,
                fallback_reason="no_evidence",
                **common,
            )

        citable = tuple(
            item
            for item in evidence
            if isinstance(item, ActorEvidence)
            or (
                isinstance(item, MomentEvidence)
                and item.display_text is not None
            )
        )[:200]
        if self.model is None:
            return QueryAnswer(
                mode=QueryAnswerMode.evidence_only,
                fallback_reason=planning_fallback,
                **common,
            )
        if planning_fallback == "query_model_unavailable":
            return QueryAnswer(
                mode=QueryAnswerMode.evidence_only,
                fallback_reason=planning_fallback,
                **common,
            )
        if not citable:
            return QueryAnswer(
                mode=QueryAnswerMode.evidence_only,
                fallback_reason="no_textual_evidence",
                **common,
            )
        try:
            draft = self.model.synthesize(
                QuerySynthesisRequest(
                    question=command.question,
                    evidence=citable,
                )
            )
        except QueryProviderError:
            return QueryAnswer(
                mode=QueryAnswerMode.evidence_only,
                fallback_reason="query_model_unavailable",
                **common,
            )
        claims = self._validated_claims(draft, citable)
        if claims is None:
            return QueryAnswer(
                mode=QueryAnswerMode.evidence_only,
                fallback_reason="query_citations_rejected",
                **common,
            )
        return QueryAnswer(
            mode=QueryAnswerMode.generated,
            claims=claims,
            fallback_reason=planning_fallback,
            **common,
        )

    @staticmethod
    def _validated_claims(
        draft: DraftAnswer,
        evidence: tuple[Evidence, ...],
    ) -> tuple[GroundedClaim, ...] | None:
        allowed = {item.evidence_id for item in evidence}
        claims = tuple(
            GroundedClaim(
                text=claim.text,
                evidence_ids=claim.evidence_ids,
            )
            for claim in draft.claims
        )
        cited = {
            evidence_id
            for claim in claims
            for evidence_id in claim.evidence_ids
        }
        if not cited.issubset(allowed):
            return None
        return claims
