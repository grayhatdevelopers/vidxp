import unittest

from vidxp.application_models import SearchHit, SearchResult
from vidxp.search_fusion import RRF_RANK_CONSTANT, fuse_search_results


MEDIA_ID = "123456781234423481234567890abcde"
GENERATION_ID = "223456781234423481234567890abcde"


def hit(
    modality: str,
    rank: int,
    start: float,
    end: float,
    source_id: str,
) -> SearchHit:
    return SearchHit(
        rank=rank,
        media_id=MEDIA_ID,
        video_id=MEDIA_ID,
        generation_id=GENERATION_ID,
        start=start,
        end=end,
        score=-float(rank),
        raw_distance=float(rank),
        modality=modality,
        source_id=source_id,
    )


class SearchFusionTests(unittest.TestCase):
    def test_rrf_counts_only_the_best_rank_per_modality_in_a_moment(self):
        scene = SearchResult(
            query_id="scene:q",
            query="taxi",
            modality="scene",
            hits=(
                hit("scene", 1, 1, 3, "scene:1"),
                hit("scene", 2, 2, 4, "scene:2"),
            ),
        )
        dialogue = SearchResult(
            query_id="dialogue:q",
            query="taxi",
            modality="speech",
            hits=(hit("speech", 1, 2.5, 3.5, "dialogue:1"),),
        )

        result = fuse_search_results(
            query="taxi",
            requested_modalities=("scene", "speech"),
            results=(scene, dialogue),
        )

        self.assertEqual(len(result.moments), 1)
        moment = result.moments[0]
        self.assertAlmostEqual(moment.score, 2 / (RRF_RANK_CONSTANT + 1))
        self.assertEqual(len(moment.hits), 3)
        self.assertEqual(moment.start, 1)
        self.assertEqual(moment.end, 4)

    def test_result_order_does_not_change_fusion_identity_or_output(self):
        scene = SearchResult(
            query_id="scene:q",
            query="taxi",
            modality="scene",
            hits=(hit("scene", 1, 1, 2, "scene:1"),),
        )
        dialogue = SearchResult(
            query_id="dialogue:q",
            query="taxi",
            modality="speech",
            hits=(hit("speech", 1, 1, 2, "dialogue:1"),),
        )
        arguments = {
            "query": "taxi",
            "requested_modalities": ("scene", "speech"),
        }

        forward = fuse_search_results(
            results=(scene, dialogue),
            **arguments,
        )
        reverse = fuse_search_results(
            results=(dialogue, scene),
            **arguments,
        )

        self.assertEqual(forward, reverse)

    def test_rewritten_atomic_query_identity_changes_fused_identity(self):
        original = SearchResult(
            query_id="scene:original",
            query="taxi",
            modality="scene",
            hits=(hit("scene", 1, 1, 2, "scene:1"),),
        )
        rewritten = original.model_copy(update={"query_id": "scene:rewritten"})
        arguments = {
            "query": "Where is the taxi?",
            "requested_modalities": ("scene",),
        }

        first = fuse_search_results(results=(original,), **arguments)
        second = fuse_search_results(results=(rewritten,), **arguments)

        self.assertNotEqual(first.query_id, second.query_id)

    def test_search_hit_and_fused_moment_ranking_score_semantics(self):
        scene = SearchResult(
            query_id="scene:q",
            query="taxi",
            modality="scene",
            hits=(hit("scene", 1, 1.0, 3.0, "scene:1"),),
        )
        result = fuse_search_results(
            query="taxi",
            requested_modalities=("scene",),
            results=(scene,),
        )

        self.assertEqual(len(result.moments), 1)
        moment = result.moments[0]

        # Combined rank vs channel rank
        self.assertEqual(moment.rank, 1)
        self.assertEqual(moment.combined_rank, 1)
        self.assertEqual(moment.score_kind, "ordering_only")
        self.assertEqual(moment.score_direction, "higher_is_better")
        self.assertEqual(moment.scoring_method, "reciprocal_rank_fusion")
        self.assertEqual(moment.contributing_channels, ("scene",))
        self.assertEqual(moment.channels_run, ("scene",))

        # Channel hit score and rank metadata
        first_hit = moment.hits[0]
        self.assertEqual(first_hit.rank, 1)
        self.assertEqual(first_hit.channel_rank, 1)
        self.assertEqual(first_hit.score_kind, "ordering_only")
        self.assertEqual(first_hit.score_direction, "higher_is_better")
        self.assertEqual(first_hit.score_conversion, "negated_distance")
        self.assertEqual(first_hit.distance_metric, "cosine")
        self.assertEqual(first_hit.distance_direction, "lower_is_better")

        # Fusion provenance semantics
        self.assertEqual(result.fusion.score_kind, "ordering_only")
        self.assertEqual(result.fusion.score_direction, "higher_is_better")
        self.assertEqual(result.fusion.scoring_method, "reciprocal_rank_fusion")
        self.assertEqual(result.fusion.searched_modalities, ("scene",))

    def test_multimodal_moment_distinguishes_channel_ranks_from_combined_rank(self):
        scene = SearchResult(
            query_id="scene:q",
            query="taxi",
            modality="scene",
            hits=(hit("scene", 3, 1.0, 3.0, "scene:3"),),
        )
        speech = SearchResult(
            query_id="speech:q",
            query="taxi",
            modality="speech",
            hits=(hit("speech", 1, 2.0, 4.0, "speech:1"),),
        )
        result = fuse_search_results(
            query="taxi",
            requested_modalities=("scene", "speech"),
            results=(scene, speech),
        )

        self.assertEqual(len(result.moments), 1)
        moment = result.moments[0]
        self.assertEqual(moment.combined_rank, 1)
        self.assertEqual(moment.contributing_channels, ("scene", "speech"))
        self.assertEqual(moment.channels_run, ("scene", "speech"))

        hits_by_modality = {h.modality: h for h in moment.hits}
        self.assertEqual(hits_by_modality["scene"].channel_rank, 3)
        self.assertEqual(hits_by_modality["speech"].channel_rank, 1)

    def test_score_serialization_clear_for_callers(self):
        scene = SearchResult(
            query_id="scene:q",
            query="taxi",
            modality="scene",
            hits=(hit("scene", 1, 1.0, 2.0, "scene:1"),),
        )
        result = fuse_search_results(
            query="taxi",
            requested_modalities=("scene",),
            results=(scene,),
        )
        dumped = result.model_dump(mode="json")
        moment_dump = dumped["moments"][0]

        self.assertEqual(moment_dump["combined_rank"], 1)
        self.assertEqual(moment_dump["score_kind"], "ordering_only")
        self.assertEqual(moment_dump["score_direction"], "higher_is_better")
        self.assertEqual(moment_dump["scoring_method"], "reciprocal_rank_fusion")
        self.assertEqual(moment_dump["contributing_channels"], ["scene"])
        self.assertEqual(moment_dump["channels_run"], ["scene"])

        hit_dump = moment_dump["hits"][0]
        self.assertEqual(hit_dump["channel_rank"], 1)
        self.assertEqual(hit_dump["score_kind"], "ordering_only")
        self.assertEqual(hit_dump["score_direction"], "higher_is_better")
        self.assertEqual(hit_dump["score_conversion"], "negated_distance")
        self.assertEqual(hit_dump["distance_metric"], "cosine")
        self.assertEqual(hit_dump["distance_direction"], "lower_is_better")


if __name__ == "__main__":
    unittest.main()
