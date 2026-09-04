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

    def test_bridging_hit_does_not_merge_separate_moments(self):
        hit_a = hit("scene", 1, 10.0, 12.0, "scene:a")
        hit_b = hit("speech", 1, 11.0, 25.0, "speech:b")
        hit_c = hit("scene", 2, 24.0, 26.0, "scene:c")

        scene = SearchResult(
            query_id="scene:q",
            query="car",
            modality="scene",
            hits=(hit_a, hit_c),
        )
        speech = SearchResult(
            query_id="speech:q",
            query="car",
            modality="speech",
            hits=(hit_b,),
        )

        result = fuse_search_results(
            query="car",
            requested_modalities=("scene", "speech"),
            results=(scene, speech),
        )

        self.assertEqual(len(result.moments), 2)
        moment_1, moment_2 = result.moments
        self.assertEqual(moment_1.start, 10.0)
        self.assertIn("scene:a", [h.source_id for h in moment_1.hits])
        self.assertEqual(moment_2.start, 24.0)
        self.assertEqual(moment_2.end, 26.0)
        self.assertIn("scene:c", [h.source_id for h in moment_2.hits])
        self.assertNotIn("scene:c", [h.source_id for h in moment_1.hits])

    def test_nearby_duplicate_hits_combine_into_one_moment(self):
        hit_1 = hit("scene", 1, 1.0, 3.0, "scene:1")
        hit_2 = hit("scene", 2, 2.0, 3.5, "scene:2")
        hit_3 = hit("speech", 1, 2.2, 2.8, "speech:1")

        scene = SearchResult(
            query_id="scene:q",
            query="dog",
            modality="scene",
            hits=(hit_1, hit_2),
        )
        speech = SearchResult(
            query_id="speech:q",
            query="dog",
            modality="speech",
            hits=(hit_3,),
        )

        result = fuse_search_results(
            query="dog",
            requested_modalities=("scene", "speech"),
            results=(scene, speech),
        )

        self.assertEqual(len(result.moments), 1)
        self.assertEqual(len(result.moments[0].hits), 3)
        self.assertEqual(result.moments[0].start, 1.0)
        self.assertEqual(result.moments[0].end, 3.5)


if __name__ == "__main__":
    unittest.main()
