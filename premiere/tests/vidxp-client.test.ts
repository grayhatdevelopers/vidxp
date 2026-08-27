import { describe, expect, it, vi } from "vitest";

import {
  VidXPApiError,
  VidXPClient,
  type VidXPFetch,
} from "../src/services/vidxp/client";
import type { MediaIngestionStatus, VidXPJob } from "../src/services/vidxp/types";

const ingestion: MediaIngestionStatus = {
  session_id: "ingestion-1",
  aggregate_state: "processing",
  index_modalities: ["scene"],
  file_count: 1,
  searchable_file_count: 0,
  failed_file_count: 0,
  index_failed_file_count: 0,
  items: [],
  terminal: false,
  poll_after_seconds: 1,
  status: "Importing",
  next_action: "Poll",
};

describe("VidXPClient", () => {
  it("submits Premiere paths to the local ingestion contract", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse(ingestion));
    const client = new VidXPClient({
      baseUrl: " http://127.0.0.1:32191/docs ",
      bearerToken: " local-token ",
      fetchImpl: fetchImpl as VidXPFetch,
    });

    await client.createLocalIngestion(
      ["C:/Media/a.mp4"],
      ["scene"],
      "request-key",
    );

    const [url, init] = fetchImpl.mock.calls[0] as [string, RequestInit];
    const headers = new Headers(init.headers);
    expect(url).toBe("http://127.0.0.1:32191/api/v1/media/local-ingestions");
    expect(init.method).toBe("POST");
    expect(headers.get("Authorization")).toBe("Bearer local-token");
    expect(headers.get("Idempotency-Key")).toBe("request-key");
    if (typeof init.body !== "string") throw new Error("Expected a JSON request body.");
    expect(JSON.parse(init.body)).toEqual({
      paths: ["C:/Media/a.mp4"],
      modalities: ["scene"],
      index_after_import: true,
    });
  });

  it("polls a search job and returns its typed result", async () => {
    const completed: VidXPJob = {
      job_id: "job-1",
      kind: "search",
      state: "succeeded",
      terminal: true,
      poll_after_seconds: 0,
      result: {
        kind: "search",
        result: {
          query_id: "query-1",
          query: "door opens",
          modalities: ["scene"],
          moments: [],
        },
      },
    };
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse(completed));
    const sleep = vi.fn().mockResolvedValue(undefined);
    const client = new VidXPClient({
      baseUrl: "http://localhost:32191",
      fetchImpl: fetchImpl as VidXPFetch,
      sleep,
    });
    const queued: VidXPJob = {
      job_id: "job-1",
      kind: "search",
      state: "queued",
      terminal: false,
      poll_after_seconds: 1,
    };

    const result = await client.waitForJob(queued, vi.fn());

    expect(sleep).toHaveBeenCalledWith(1000);
    expect(result.result?.result.query_id).toBe("query-1");
  });

  it("surfaces safe API remediation without exposing the bearer token", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      jsonResponse(
        {
          error: {
            code: "models_unavailable",
            message: "Models are not prepared.",
            details: { remediation: "Prepare the selected features in Desktop." },
          },
        },
        503,
      ),
    );
    const client = new VidXPClient({
      baseUrl: "http://localhost:32191",
      bearerToken: "secret-token",
      fetchImpl: fetchImpl as VidXPFetch,
    });

    const error = await client.listCapabilities().catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(VidXPApiError);
    expect(String(error)).toContain("Prepare the selected features in Desktop.");
    expect(String(error)).not.toContain("secret-token");
  });
});

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}
