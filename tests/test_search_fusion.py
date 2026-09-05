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
    def test_rrf_keeps_one_best_direct_match_per_modality(self):
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

        self.assertEqual(len(result.moments), 2)
        moment = result.moments[0]
        self.assertAlmostEqual(moment.score, 2 / (RRF_RANK_CONSTANT + 1))
        self.assertEqual(len(moment.hits), 2)
        self.assertEqual(moment.start, 1)
        self.assertEqual(moment.end, 3.5)

    def test_distant_matches_remain_separate_final_candidates(self):
        scene = SearchResult(
            query_id="scene:q",
            query="opening image and closing sound",
            modality="scene",
            hits=(hit("scene", 1, 0, 10, "scene:opening"),),
        )
        sound = SearchResult(
            query_id="sound:q",
            query="opening image and closing sound",
            modality="sound",
            hits=(hit("sound", 1, 290, 300, "sound:closing"),),
        )

        result = fuse_search_results(
            query="opening image and closing sound",
            requested_modalities=("scene", "sound"),
            results=(scene, sound),
        )

        self.assertEqual(
            [(moment.start, moment.end) for moment in result.moments],
            [(0, 10), (290, 300)],
        )

    def test_overlap_support_does_not_chain_through_another_hit(self):
        scene = SearchResult(
            query_id="scene:q",
            query="event",
            modality="scene",
            hits=(hit("scene", 1, 0, 10, "scene:1"),),
        )
        action = SearchResult(
            query_id="action:q",
            query="event",
            modality="action",
            hits=(hit("action", 1, 9, 20, "action:1"),),
        )
        sound = SearchResult(
            query_id="sound:q",
            query="event",
            modality="sound",
            hits=(hit("sound", 1, 19, 30, "sound:1"),),
        )

        result = fuse_search_results(
            query="event",
            requested_modalities=("scene", "action", "sound"),
            results=(scene, action, sound),
        )

        self.assertEqual(
            [(moment.start, moment.end) for moment in result.moments],
            [(0, 20), (19, 30)],
        )
        self.assertNotIn((0, 30), {
            (moment.start, moment.end) for moment in result.moments
        })

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


if __name__ == "__main__":
    unittest.main()
