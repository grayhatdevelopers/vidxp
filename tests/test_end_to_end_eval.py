import unittest

from vidxp.application_models import (
    ApplicationError,
    ErrorCategory,
    EvidenceDeliveryItem,
    EvidenceDeliveryPolicy,
    EvidenceDeliveryResult,
    EvidenceDeliveryState,
    EvidenceRangeResolution,
    FusedMoment,
    FusedSearchResult,
    FusionProvenance,
    SearchCommand,
    SearchHit,
)
from vidxp.benchmarks.end_to_end import (
    EvaluationCase,
    EvaluationDataset,
    GroundTruthInterval,
    application_search_fn,
    best_interval_iou,
    evaluate_end_to_end,
    ndcg_at_k,
    temporal_iou,
)


MEDIA_ID = "123456781234423481234567890abcde"
OTHER_MEDIA = "223456781234423481234567890abcde"
GENERATION_ID = "323456781234423481234567890abcde"
EVIDENCE_ID = "ab" * 32


def make_hit(modality: str, media_id: str, start: float, end: float, rank: int):
    return SearchHit(
        rank=rank,
        media_id=media_id,
        video_id=media_id,
        generation_id=GENERATION_ID,
        start=start,
        end=end,
        score=-float(rank),
        raw_distance=float(rank),
        modality=modality,
        source_id=f"{modality}:{rank}",
    )


def make_moment(
    rank: int,
    media_id: str,
    start: float,
    end: float,
    modalities: tuple[str, ...],
):
    hits = tuple(make_hit(m, media_id, start, end, rank) for m in modalities)
    return FusedMoment(
        rank=rank,
        score=1.0 / (60 + rank),
        media_id=media_id,
        start=start,
        end=end,
        modalities=tuple(sorted(set(modalities))),
        hits=hits,
    )


def make_result(moments, *, modalities=("scene",), evidence=None):
    return FusedSearchResult(
        query_id="fused:test",
        query="taxi",
        modalities=modalities,
        moments=tuple(moments),
        fusion=FusionProvenance(
            requested_modalities=modalities,
            searched_modalities=modalities,
        ),
        evidence_delivery=evidence,
    )


def make_evidence(start: float, end: float, *, state=EvidenceDeliveryState.ready):
    return EvidenceDeliveryResult(
        policy=EvidenceDeliveryPolicy(),
        items=(
            EvidenceDeliveryItem(
                evidence_id=EVIDENCE_ID,
                rank=1,
                media_id=MEDIA_ID,
                generation_id=GENERATION_ID,
                modalities=("scene",),
                state=state,
                range=EvidenceRangeResolution(
                    source_start_seconds=start,
                    source_end_seconds=end,
                    representative_timestamp_seconds=start,
                    clip_start_seconds=start,
                    clip_end_seconds=end,
                    requested_padding_before_seconds=0.0,
                    requested_padding_after_seconds=0.0,
                    applied_padding_before_seconds=0.0,
                    applied_padding_after_seconds=0.0,
                ),
            ),
        ),
    )


def gt_case(case_id: str, start: float, end: float) -> EvaluationCase:
    return EvaluationCase(
        case_id=case_id,
        query="taxi",
        media_id=MEDIA_ID,
        modalities=("scene",),
        relevant=(GroundTruthInterval(media_id=MEDIA_ID, start=start, end=end),),
    )


class MetricFunctionTests(unittest.TestCase):
    def test_temporal_iou_overlap_disjoint_and_identical(self):
        self.assertEqual(temporal_iou(0, 10, 0, 10), 1.0)
        self.assertEqual(temporal_iou(0, 10, 20, 30), 0.0)
        # [0,10] vs [5,15]: intersection 5, union 15 -> 1/3
        self.assertAlmostEqual(temporal_iou(0, 10, 5, 15), 1 / 3)

    def test_best_interval_iou_ignores_other_media(self):
        relevant = (
            GroundTruthInterval(media_id=MEDIA_ID, start=10, end=20),
            GroundTruthInterval(media_id=OTHER_MEDIA, start=0, end=100),
        )
        # A perfect overlap exists only on OTHER_MEDIA; scoped to MEDIA_ID it is 0.
        self.assertEqual(
            best_interval_iou(media_id=MEDIA_ID, start=0, end=5, relevant=relevant),
            0.0,
        )
        self.assertEqual(
            best_interval_iou(
                media_id=MEDIA_ID, start=10, end=20, relevant=relevant
            ),
            1.0,
        )

    def test_ndcg_rewards_relevant_first(self):
        self.assertEqual(ndcg_at_k([], 5), 0.0)
        # Perfect gain ordering normalizes to 1.0.
        self.assertAlmostEqual(ndcg_at_k([1.0, 0.5, 0.0], 3), 1.0)
        # A relevant item buried below an irrelevant one scores below ideal.
        self.assertLess(ndcg_at_k([0.0, 1.0], 2), 1.0)


class EvaluateEndToEndTests(unittest.TestCase):
    def _run(self, cases, results_by_id, **kwargs):
        dataset = EvaluationDataset(name="unit", cases=tuple(cases))

        def search_fn(case):
            outcome = results_by_id[case.case_id]
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        return evaluate_end_to_end(dataset, search_fn, **kwargs)

    def test_perfect_hit_scores_top_metrics(self):
        case = gt_case("a", 10, 20)
        result = make_result([make_moment(1, MEDIA_ID, 10, 20, ("scene",))])
        report = self._run([case], {"a": result})

        agg = report.aggregate
        self.assertEqual(agg.evaluated_cases, 1)
        self.assertEqual(agg.recall_at_k[1], 1.0)
        self.assertEqual(agg.mean_top1_iou, 1.0)
        self.assertEqual(agg.mean_reciprocal_rank, 1.0)
        self.assertEqual(agg.ndcg_at_k[1], 1.0)
        self.assertEqual(agg.modality_contribution, {"scene": 1})

    def test_relevant_below_rank_one_lowers_recall_and_mrr(self):
        case = gt_case("b", 10, 20)
        result = make_result(
            [
                make_moment(1, MEDIA_ID, 100, 110, ("scene",)),  # miss
                make_moment(2, MEDIA_ID, 11, 19, ("scene",)),  # relevant
            ]
        )
        report = self._run([case], {"b": result}, k_values=(1, 5))

        agg = report.aggregate
        self.assertEqual(agg.recall_at_k[1], 0.0)
        self.assertEqual(agg.recall_at_k[5], 1.0)
        self.assertEqual(agg.mean_reciprocal_rank, 0.5)
        self.assertEqual(report.cases[0].first_relevant_rank, 2)

    def test_failed_case_is_isolated_from_quality_metrics(self):
        cases = [gt_case("ok", 10, 20), gt_case("bad", 10, 20)]
        results = {
            "ok": make_result([make_moment(1, MEDIA_ID, 10, 20, ("scene",))]),
            "bad": ApplicationError(
                "boom", ErrorCategory.internal, "search exploded"
            ),
        }
        report = self._run(cases, results)

        agg = report.aggregate
        self.assertEqual(agg.failed_cases, 1)
        self.assertEqual(agg.evaluated_cases, 1)
        self.assertEqual(agg.recall_at_k[1], 1.0)  # denominator excludes failure
        failed = next(c for c in report.cases if c.case_id == "bad")
        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.error_code, "boom")

    def test_unexpected_exception_is_recorded_not_raised(self):
        # search() only wraps known errors; an unexpected type must not abort
        # the whole run. It is recorded as failed with its class name.
        cases = [gt_case("ok", 10, 20), gt_case("boom", 10, 20)]
        results = {
            "ok": make_result([make_moment(1, MEDIA_ID, 10, 20, ("scene",))]),
            "boom": RuntimeError("unexpected"),
        }
        report = self._run(cases, results)

        self.assertEqual(report.aggregate.failed_cases, 1)
        self.assertEqual(report.aggregate.evaluated_cases, 1)
        failed = next(c for c in report.cases if c.case_id == "boom")
        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.error_code, "RuntimeError")

    def test_empty_result_counts_as_no_result(self):
        case = gt_case("empty", 10, 20)
        report = self._run([case], {"empty": make_result([])})

        self.assertEqual(report.aggregate.no_result_cases, 1)
        self.assertEqual(report.aggregate.mean_top1_iou, 0.0)
        self.assertEqual(report.cases[0].status, "no_result")

    def test_evidence_support_and_degradation(self):
        supported = gt_case("sup", 10, 20)
        unsupported = gt_case("uns", 10, 20)
        results = {
            "sup": make_result(
                [make_moment(1, MEDIA_ID, 10, 20, ("scene",))],
                evidence=make_evidence(10, 20),
            ),
            "uns": make_result(
                [make_moment(1, MEDIA_ID, 10, 20, ("scene",))],
                evidence=make_evidence(
                    500, 510, state=EvidenceDeliveryState.partial
                ),
            ),
        }
        report = self._run([supported, unsupported], results)

        self.assertEqual(report.aggregate.evidence_support_rate, 0.5)
        degraded = next(c for c in report.cases if c.case_id == "uns")
        self.assertTrue(degraded.evidence_degraded)
        self.assertFalse(degraded.evidence_supported)

    def test_evidence_rate_is_none_when_no_evidence_delivered(self):
        case = gt_case("a", 10, 20)
        result = make_result([make_moment(1, MEDIA_ID, 10, 20, ("scene",))])
        report = self._run([case], {"a": result})

        self.assertIsNone(report.aggregate.evidence_support_rate)

    def test_invalid_parameters_are_rejected(self):
        dataset = EvaluationDataset(name="unit", cases=(gt_case("a", 10, 20),))
        with self.assertRaises(ValueError):
            evaluate_end_to_end(dataset, lambda case: make_result([]), relevance_iou=0)
        with self.assertRaises(ValueError):
            evaluate_end_to_end(dataset, lambda case: make_result([]), k_values=())


class ApplicationSearchFnTests(unittest.TestCase):
    def test_builds_public_search_command(self):
        captured = {}
        result = make_result([make_moment(1, MEDIA_ID, 10, 20, ("scene",))])

        def fake_search(command, **kwargs):
            captured["command"] = command
            captured["kwargs"] = kwargs
            return result

        run = application_search_fn(fake_search, top_k=7)
        case = EvaluationCase(
            case_id="a",
            query="a taxi at night",
            media_id=MEDIA_ID,
            modalities=("scene", "speech"),
            relevant=(GroundTruthInterval(media_id=MEDIA_ID, start=1, end=2),),
        )
        returned = run(case)

        self.assertIs(returned, result)
        command = captured["command"]
        self.assertIsInstance(command, SearchCommand)
        self.assertEqual(command.query, "a taxi at night")
        self.assertEqual(command.modalities, ("scene", "speech"))
        self.assertEqual(command.media_id, MEDIA_ID)
        self.assertEqual(command.top_k, 7)
        self.assertEqual(captured["kwargs"], {})  # no snapshot passed

    def test_passes_snapshot_when_supplied(self):
        captured = {}

        def fake_search(command, **kwargs):
            captured["kwargs"] = kwargs
            return make_result([])

        run = application_search_fn(fake_search, snapshot="snap-ref")
        run(gt_case("a", 1, 2))

        self.assertEqual(captured["kwargs"], {"snapshot": "snap-ref"})


if __name__ == "__main__":
    unittest.main()
