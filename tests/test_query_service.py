import unittest

from vidxp.application_models import (
    DraftAnswer,
    DraftClaim,
    FusedSearchResult,
    FusionProvenance,
    IndexSnapshotReference,
    QueryAnswerMode,
    QueryModelIdentity,
    QueryPlan,
    QueryVideoCommand,
    SearchHit,
    SearchMomentsPlanStep,
    SearchResult,
)
from vidxp.ports import QueryProviderError
from vidxp.query_service import GroundedQueryService
from vidxp.search_fusion import fuse_search_results


MEDIA_ID = "123456781234423481234567890abcde"
GENERATION_ID = "223456781234423481234567890abcde"
SNAPSHOT_ID = "323456781234423481234567890abcde"
SNAPSHOT_SHA256 = "a" * 64


class FakeQueryModel:
    identity = QueryModelIdentity(provider="ollama", model="test")

    def __init__(self, plan, answer=None):
        self.proposed_plan = plan
        self.proposed_answer = answer

    def plan(self, _request):
        if isinstance(self.proposed_plan, Exception):
            raise self.proposed_plan
        return self.proposed_plan

    def synthesize(self, _request):
        if isinstance(self.proposed_answer, Exception):
            raise self.proposed_answer
        return self.proposed_answer


def result(*, text: str | None = None, modality: str = "scene"):
    metadata = {"text": text} if text is not None else {}
    return SearchResult(
        query_id=f"{modality}:q",
        query="taxi",
        modality=modality,
        hits=(
            SearchHit(
                rank=1,
                media_id=MEDIA_ID,
                video_id=MEDIA_ID,
                generation_id=GENERATION_ID,
                start=1,
                end=2,
                score=-0.1,
                raw_distance=0.1,
                modality=modality,
                source_id=f"{modality}:1",
                metadata=metadata,
            ),
        ),
    )


def fused(
    modality: str = "scene",
    atomic: SearchResult | None = None,
):
    if atomic is not None:
        return fuse_search_results(
            query=atomic.query,
            requested_modalities=(modality,),
            results=(atomic,),
        )
    return FusedSearchResult(
        query_id="fused:q",
        query="taxi",
        modalities=(modality,),
        fusion=FusionProvenance(
            requested_modalities=(modality,),
            searched_modalities=(modality,),
        ),
    )


class GroundedQueryServiceTests(unittest.TestCase):
    def setUp(self):
        self.command = QueryVideoCommand(question="Where is the taxi?")
        self.snapshot = IndexSnapshotReference(
            snapshot_id=SNAPSHOT_ID,
            snapshot_sha256=SNAPSHOT_SHA256,
        )

    def test_invalid_model_plan_falls_back_to_complete_closed_plan(self):
        model = FakeQueryModel(
            QueryPlan(
                steps=(
                    SearchMomentsPlanStep(
                        modality="scene",
                        query="taxi",
                    ),
                )
            )
        )
        service = GroundedQueryService(model)

        plan, reason = service.plan(
            self.command,
            search_modalities=("scene", "dialogue"),
            actor_overview=False,
        )

        self.assertEqual(reason, "query_plan_rejected")
        self.assertEqual(
            [step.modality for step in plan.steps],
            ["scene", "dialogue"],
        )

    def test_provider_failure_uses_deterministic_retrieval_plan(self):
        service = GroundedQueryService(
            FakeQueryModel(QueryProviderError("offline"))
        )

        plan, reason = service.plan(
            self.command,
            search_modalities=("scene",),
            actor_overview=False,
        )

        self.assertEqual(reason, "query_model_unavailable")
        self.assertEqual(plan.steps[0].query, self.command.question)

    def test_scene_only_evidence_never_becomes_a_generated_claim(self):
        service = GroundedQueryService(
            FakeQueryModel(
                QueryPlan(
                    steps=(
                        SearchMomentsPlanStep(
                            modality="scene",
                            query="taxi",
                        ),
                    )
                ),
                DraftAnswer(
                    claims=(
                        DraftClaim(
                            text="A taxi is visible.",
                            evidence_ids=("b" * 64,),
                        ),
                    )
                ),
            )
        )
        atomic = result()
        fused_result = fused(atomic=atomic)
        evidence = service.evidence(
            snapshot=self.snapshot,
            fused=fused_result,
            actors=(),
        )

        answer = service.answer(
            self.command,
            plan=service.plan(
                self.command,
                search_modalities=("scene",),
                actor_overview=False,
            )[0],
            planning_fallback=None,
            evidence=evidence,
            fused=fused_result,
        )

        self.assertEqual(answer.mode, QueryAnswerMode.evidence_only)
        self.assertEqual(answer.fallback_reason, "no_textual_evidence")
        self.assertFalse(answer.claims)

    def test_unknown_citation_is_rejected(self):
        plan = QueryPlan(
            steps=(
                SearchMomentsPlanStep(
                    modality="dialogue",
                    query="taxi",
                ),
            )
        )
        service = GroundedQueryService(
            FakeQueryModel(
                plan,
                DraftAnswer(
                    claims=(
                        DraftClaim(
                            text="Someone mentions a taxi.",
                            evidence_ids=("b" * 64,),
                        ),
                    )
                ),
            )
        )
        atomic = result(text="the taxi arrived", modality="dialogue")
        fused_result = fused("dialogue", atomic)
        evidence = service.evidence(
            snapshot=self.snapshot,
            fused=fused_result,
            actors=(),
        )

        answer = service.answer(
            self.command,
            plan=plan,
            planning_fallback=None,
            evidence=evidence,
            fused=fused_result,
        )

        self.assertEqual(answer.mode, QueryAnswerMode.evidence_only)
        self.assertEqual(answer.fallback_reason, "query_citations_rejected")

    def test_valid_textual_citation_is_reconstructed_by_the_application(self):
        plan = QueryPlan(
            steps=(
                SearchMomentsPlanStep(
                    modality="dialogue",
                    query="taxi",
                ),
            )
        )
        atomic = result(text="the taxi arrived", modality="dialogue")
        service = GroundedQueryService()
        fused_result = fused("dialogue", atomic)
        evidence = service.evidence(
            snapshot=self.snapshot,
            fused=fused_result,
            actors=(),
        )
        service.model = FakeQueryModel(
            plan,
            DraftAnswer(
                claims=(
                    DraftClaim(
                        text="The taxi arrived.",
                        evidence_ids=(evidence[0].evidence_id,),
                    ),
                )
            ),
        )

        answer = service.answer(
            self.command,
            plan=plan,
            planning_fallback=None,
            evidence=evidence,
            fused=fused_result,
        )

        self.assertEqual(answer.mode, QueryAnswerMode.generated)
        self.assertEqual(
            answer.claims[0].evidence_ids,
            (evidence[0].evidence_id,),
        )

    def test_generated_answer_preserves_rejected_plan_provenance(self):
        atomic = result(text="the taxi arrived", modality="dialogue")
        fused_result = fused("dialogue", atomic)
        service = GroundedQueryService(
            FakeQueryModel(
                QueryPlan(
                    steps=(
                        SearchMomentsPlanStep(
                            modality="dialogue",
                            query="taxi",
                        ),
                    )
                )
            )
        )
        evidence = service.evidence(
            snapshot=self.snapshot,
            fused=fused_result,
            actors=(),
        )
        service.model.proposed_answer = DraftAnswer(
            claims=(
                DraftClaim(
                    text="The taxi arrived.",
                    evidence_ids=(evidence[0].evidence_id,),
                ),
            )
        )

        answer = service.answer(
            self.command,
            plan=service.model.proposed_plan,
            planning_fallback="query_plan_rejected",
            evidence=evidence,
            fused=fused_result,
        )

        self.assertEqual(answer.mode, QueryAnswerMode.generated)
        self.assertEqual(answer.fallback_reason, "query_plan_rejected")
        self.assertEqual(answer.model, service.model.identity)

    def test_configured_model_identity_is_retained_without_evidence(self):
        plan = QueryPlan(
            steps=(
                SearchMomentsPlanStep(
                    modality="scene",
                    query="taxi",
                ),
            )
        )
        service = GroundedQueryService(FakeQueryModel(plan))

        answer = service.answer(
            self.command,
            plan=plan,
            planning_fallback=None,
            evidence=(),
            fused=fused(),
        )

        self.assertEqual(answer.mode, QueryAnswerMode.no_evidence)
        self.assertEqual(answer.model, service.model.identity)

    def test_evidence_is_bounded_to_retained_fused_moments(self):
        atomic = SearchResult(
            query_id="dialogue:many",
            query="taxi",
            modality="dialogue",
            hits=tuple(
                SearchHit(
                    rank=index + 1,
                    media_id=MEDIA_ID,
                    video_id=MEDIA_ID,
                    generation_id=GENERATION_ID,
                    start=float(index * 2),
                    end=float(index * 2 + 1),
                    score=-float(index + 1),
                    raw_distance=float(index + 1),
                    modality="dialogue",
                    source_id=f"dialogue:{index}",
                    metadata={"text": f"line {index}"},
                )
                for index in range(201)
            ),
        )
        fused_result = fuse_search_results(
            query="taxi",
            requested_modalities=("dialogue",),
            results=(atomic,),
            top_k=201,
        )

        evidence = GroundedQueryService.evidence(
            snapshot=self.snapshot,
            fused=fused_result,
            actors=(),
        )

        self.assertEqual(len(evidence), 200)
        retained_sources = {
            hit.source_id
            for moment in fused_result.moments
            for hit in moment.hits
        }
        self.assertTrue(
            all(item.source_id in retained_sources for item in evidence)
        )


if __name__ == "__main__":
    unittest.main()
