import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { QueryAnswer } from "../src/services/vidxp/types";
import { GroundedAnswer } from "../src/ui/App";

const evidence = {
  kind: "moment" as const,
  evidence_id: "evidence-1",
  media_id: "media-1",
  modality: "sound",
  start: 12,
  end: 15,
  display_text: "A door slams.",
};

describe("Premiere grounded answers", () => {
  it("renders generated claims with numbered supporting evidence", () => {
    const answer: QueryAnswer = {
      question: "What happens after the door slams?",
      mode: "generated",
      model: { provider: "ollama", model: "qwen3.5:4b-q4_K_M" },
      claims: [
        {
          text: "Someone says they need to leave.",
          evidence_ids: ["evidence-1"],
        },
      ],
      evidence: [evidence],
      moments: [],
    };

    const markup = renderToStaticMarkup(<GroundedAnswer answer={answer} />);

    expect(markup).toContain("Grounded answer");
    expect(markup).toContain("Someone says they need to leave.");
    expect(markup).toContain("[1]");
    expect(markup).toContain("A door slams.");
    expect(markup).toContain("qwen3.5:4b-q4_K_M");
  });

  it("labels deterministic fallback results as evidence rather than an answer", () => {
    const answer: QueryAnswer = {
      question: "What happens after the door slams?",
      mode: "evidence_only",
      claims: [],
      evidence: [evidence],
      moments: [],
      fallback_reason: "provider_unavailable",
    };

    const markup = renderToStaticMarkup(<GroundedAnswer answer={answer} />);

    expect(markup).toContain("Retrieved evidence");
    expect(markup).toContain("without generating an answer");
    expect(markup).not.toContain("Grounded answer");
  });
});
