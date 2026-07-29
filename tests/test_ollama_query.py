import asyncio
import json
import unittest

import httpx

from vidxp.application_models import (
    DraftAnswer,
    MomentEvidence,
    QueryPlanningRequest,
    QuerySynthesisRequest,
    SearchHit,
)
from vidxp.infrastructure.ollama_query import OllamaQueryModel


MEDIA_ID = "123456781234423481234567890abcde"
GENERATION_ID = "223456781234423481234567890abcde"
SNAPSHOT_ID = "323456781234423481234567890abcde"
EVIDENCE_ID = "a" * 64


class OllamaQueryModelTests(unittest.TestCase):
    def test_structured_planning_and_synthesis_use_the_provider_contract(self):
        outputs = [
            {
                "steps": [
                    {
                        "kind": "search_moments",
                        "modality": "dialogue",
                        "query": "taxi arrival",
                    }
                ]
            },
            {
                "claims": [
                    {
                        "text": "The taxi arrived.",
                        "evidence_ids": [EVIDENCE_ID],
                    }
                ]
            },
        ]
        requests: list[dict[str, object]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            return httpx.Response(
                200,
                request=request,
                json={
                    "id": f"chatcmpl-{len(requests)}",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "contract-model",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": json.dumps(outputs.pop(0)),
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 10,
                        "total_tokens": 20,
                    },
                },
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        model = OllamaQueryModel(
            base_url="http://ollama.test/v1",
            model_name="contract-model",
            timeout_seconds=2,
            output_retries=0,
            http_client=client,
        )
        hit = SearchHit(
            rank=1,
            media_id=MEDIA_ID,
            video_id=MEDIA_ID,
            generation_id=GENERATION_ID,
            start=1,
            end=2,
            score=-0.1,
            raw_distance=0.1,
            modality="dialogue",
            source_id="dialogue:1",
            metadata={"text": "the taxi arrived"},
        )
        evidence = MomentEvidence(
            evidence_id=EVIDENCE_ID,
            snapshot_id=SNAPSHOT_ID,
            media_id=MEDIA_ID,
            generation_id=GENERATION_ID,
            modality="dialogue",
            source_id="dialogue:1",
            start=1,
            end=2,
            display_text="the taxi arrived",
            hit=hit,
        )
        try:
            plan = model.plan(
                QueryPlanningRequest(
                    question="When did the taxi arrive?",
                    allowed_modalities=("dialogue",),
                )
            )
            answer = model.synthesize(
                QuerySynthesisRequest(
                    question="When did the taxi arrive?",
                    evidence=(evidence,),
                )
            )
        finally:
            asyncio.run(client.aclose())

        self.assertEqual(plan.steps[0].query, "taxi arrival")
        self.assertIsInstance(answer, DraftAnswer)
        self.assertEqual(answer.claims[0].evidence_ids, (EVIDENCE_ID,))
        self.assertEqual(len(requests), 2)
        for request in requests:
            self.assertEqual(
                request["response_format"]["type"],
                "json_schema",
            )
            self.assertEqual(request["model"], "contract-model")


if __name__ == "__main__":
    unittest.main()
