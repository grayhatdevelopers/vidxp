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

    def test_continuous_fine_hits_set_boundary_without_losing_coarse_support(self):
        action = SearchResult(
            query_id="action:q",
            query="rain followed by an engine",
            modality="action",
            hits=(hit("action", 1, 0, 8, "action:1"),),
        )
        scene = SearchResult(
            query_id="scene:q",
            query="rain followed by an engine",
            modality="scene",
            hits=tuple(
                hit("scene", rank, start, start + 1, f"scene:{rank}")
                for rank, start in enumerate(range(7), start=1)
            ),
        )
        sound = SearchResult(
            query_id="sound:q",
            query="rain followed by an engine",
            modality="sound",
            hits=(
                hit("sound", 1, 1.75, 2.0, "sound:1"),
                hit("sound", 2, 2.0, 2.25, "sound:2"),
            ),
        )

        result = fuse_search_results(
            query="rain followed by an engine",
            requested_modalities=("scene", "action", "sound"),
            results=(scene, action, sound),
            top_k=3,
        )

        self.assertEqual(
            (result.moments[0].start, result.moments[0].end),
            (0, 7),
        )
        self.assertEqual(
            set(result.moments[0].modalities),
            {"action", "scene", "sound"},
        )
        self.assertEqual(result.fusion.profile, "temporal_anchor_rrf_v1")
        self.assertEqual(result.fusion.overlap_rule, "anchored_intervals")

    def test_rewritten_atomic_query_identity_changes_fused_identity(self):
        original = SearchResult(
            query_id="scene:original",
            query="taxi",
            modality="scene",
            hits=(hit("scene", 1, 1, 2, "scene:1"),),
        )
        rewritten = original.model_copy(
            update={"query_id": "scene:rewritten"}
        )
        arguments = {
            "query": "Where is the taxi?",
            "requested_modalities": ("scene",),
        }

        first = fuse_search_results(results=(original,), **arguments)
        second = fuse_search_results(results=(rewritten,), **arguments)

        self.assertNotEqual(first.query_id, second.query_id)


if __name__ == "__main__":
    unittest.main()
