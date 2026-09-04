import unittest

from vidxp.application_models import SearchHit, SearchResult
from vidxp.search_fusion import (
    RRF_RANK_CONSTANT,
    fuse_search_results,
    resolve_candidate_limit,
)



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

    def test_resolve_candidate_limit_bounds(self):
        self.assertEqual(resolve_candidate_limit(3), 50)
        self.assertEqual(resolve_candidate_limit(10), 50)
        self.assertEqual(resolve_candidate_limit(20), 100)
        self.assertEqual(resolve_candidate_limit(200), 500)
        self.assertEqual(resolve_candidate_limit(0), 0)
        self.assertEqual(resolve_candidate_limit(-1), -1)

    def test_fusion_recovers_top_result_when_candidates_extend_beyond_final_top_k(self):
        # Top-k requested is 2.
        # Moment 1 is rank 1 in scene, rank 10 in speech.
        # Moment 2 is rank 1 in speech, rank 10 in scene.
        # Moment 3 is rank 3 in scene AND rank 3 in speech.
        # If each search only returned top_k=2 candidates, Moment 3 would be missed completely.
        # When candidate depth extends past rank 2, Moment 3 is included in both channels
        # and becomes the top fused result due to strong combined rank (1/63 + 1/63 > 1/61).
        scene = SearchResult(
            query_id="scene:q",
            query="test",
            modality="scene",
            hits=(
                hit("scene", 1, 10, 20, "s1"),
                hit("scene", 2, 30, 40, "s2"),
                hit("scene", 3, 50, 60, "s3"),
            ),
        )
        speech = SearchResult(
            query_id="speech:q",
            query="test",
            modality="speech",
            hits=(
                hit("speech", 1, 70, 80, "p1"),
                hit("speech", 2, 90, 100, "p2"),
                hit("speech", 3, 50, 60, "s3_speech"),
            ),
        )

        fused = fuse_search_results(
            query="test",
            requested_modalities=("scene", "speech"),
            results=(scene, speech),
            top_k=2,
        )

        self.assertEqual(len(fused.moments), 2)
        top_moment = fused.moments[0]
        self.assertEqual(top_moment.start, 50)
        self.assertEqual(top_moment.end, 60)
        self.assertEqual(top_moment.modalities, ("scene", "speech"))
        self.assertAlmostEqual(top_moment.score, 2 / (RRF_RANK_CONSTANT + 3))


if __name__ == "__main__":
    unittest.main()

