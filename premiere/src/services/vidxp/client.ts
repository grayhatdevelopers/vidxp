import type {
  CapabilitySummary,
  MediaIngestionStatus,
  VidXPJob,
  WorkspaceOverview,
} from "./types";

export type VidXPFetch = typeof fetch;
type Sleep = (milliseconds: number) => Promise<void>;

export interface VidXPClientOptions {
  baseUrl: string;
  bearerToken?: string;
  fetchImpl?: VidXPFetch;
  sleep?: Sleep;
}

export interface SearchRequest {
  query: string;
  modalities: string[];
  mediaId?: string;
  topK?: number;
}

export class VidXPApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
  ) {
    super(message);
    this.name = "VidXPApiError";
  }
}

export class VidXPClient {
  private readonly baseUrl: string;
  private readonly bearerToken?: string;
  private readonly fetchImpl: VidXPFetch;
  private readonly sleep: Sleep;

  constructor(options: VidXPClientOptions) {
    this.baseUrl = normalizeBaseUrl(options.baseUrl);
    this.bearerToken = options.bearerToken?.trim() || undefined;
    this.fetchImpl = options.fetchImpl ?? fetch;
    this.sleep = options.sleep ?? delay;
  }

  async health(): Promise<void> {
    await this.request<{ status: string }>("/health");
  }

  async listCapabilities(): Promise<CapabilitySummary[]> {
    const response = await this.request<{ items: CapabilitySummary[] }>(
      "/api/v1/capabilities",
    );
    return response.items;
  }

  workspace(): Promise<WorkspaceOverview> {
    return this.request<WorkspaceOverview>("/api/v1/workspace?page_size=100");
  }

  createLocalIngestion(
    paths: string[],
    modalities: string[],
    idempotencyKey: string,
  ): Promise<MediaIngestionStatus> {
    return this.request<MediaIngestionStatus>(
      "/api/v1/media/local-ingestions",
      {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey },
        body: JSON.stringify({
          paths,
          modalities,
          index_after_import: true,
        }),
      },
    );
  }

  getLocalIngestion(ingestionId: string): Promise<MediaIngestionStatus> {
    return this.request<MediaIngestionStatus>(
      `/api/v1/media/local-ingestions/${encodeURIComponent(ingestionId)}`,
    );
  }

  async waitForLocalIngestion(
    initial: MediaIngestionStatus,
    onProgress: (status: MediaIngestionStatus) => void,
    signal?: AbortSignal,
  ): Promise<MediaIngestionStatus> {
    let status = initial;
    while (!status.terminal) {
      onProgress(status);
      await this.wait(status.poll_after_seconds, signal);
      status = await this.getLocalIngestion(status.session_id);
    }
    onProgress(status);
    return status;
  }

  submitSearch(request: SearchRequest, idempotencyKey: string): Promise<VidXPJob> {
    const query = request.query.trim();
    if (!query) throw new Error("Enter something to search for.");
    return this.request<VidXPJob>("/api/v1/jobs/search", {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify({
        query,
        modalities: request.modalities,
        media_id: request.mediaId || null,
        top_k: request.topK ?? 20,
      }),
    });
  }

  getJob(jobId: string): Promise<VidXPJob> {
    return this.request<VidXPJob>(`/api/v1/jobs/${encodeURIComponent(jobId)}`);
  }

  async waitForJob(
    initial: VidXPJob,
    onProgress: (job: VidXPJob) => void,
    signal?: AbortSignal,
  ): Promise<VidXPJob> {
    let job = initial;
    while (!job.terminal) {
      onProgress(job);
      await this.wait(job.poll_after_seconds, signal);
      job = await this.getJob(job.job_id);
    }
    onProgress(job);
    if (job.state !== "succeeded") {
      throw new VidXPApiError(
        job.error?.message ?? "The VidXP job did not complete.",
        409,
        job.error?.code,
      );
    }
    return job;
  }

  private async wait(seconds: number, signal?: AbortSignal): Promise<void> {
    if (signal?.aborted) throw abortError();
    await this.sleep(Math.max(250, Math.min(10_000, seconds * 1000)));
    if (signal?.aborted) throw abortError();
  }

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    if (init.body !== undefined) headers.set("Content-Type", "application/json");
    if (this.bearerToken) {
      headers.set("Authorization", `Bearer ${this.bearerToken}`);
    }
    const response = await this.fetchImpl(`${this.baseUrl}${path}`, {
      ...init,
      headers,
    });
    if (!response.ok) throw await apiError(response);
    return (await response.json()) as T;
  }
}

export function createIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  const timestamp = Date.now().toString(16).padStart(16, "0");
  const random = Math.floor(Math.random() * Number.MAX_SAFE_INTEGER)
    .toString(16)
    .padStart(16, "0");
  return `${timestamp}${random}`;
}

function normalizeBaseUrl(value: string): string {
  const candidate = value.trim();
  if (!candidate) throw new Error("Enter the VidXP API address shown by Desktop.");
  let parsed: URL;
  try {
    parsed = new URL(candidate);
  } catch {
    throw new Error("Enter a complete VidXP API address, including http:// or https://.");
  }
  if (!['http:', 'https:'].includes(parsed.protocol)) {
    throw new Error("The VidXP API address must use HTTP or HTTPS.");
  }
  parsed.pathname = "";
  parsed.search = "";
  parsed.hash = "";
  return parsed.toString().replace(/\/$/, "");
}

async function apiError(response: Response): Promise<VidXPApiError> {
  let message = `VidXP returned HTTP ${response.status}.`;
  let code: string | undefined;
  try {
    const payload = (await response.json()) as {
      error?: {
        code?: string;
        message?: string;
        details?: { remediation?: string };
      };
    };
    if (payload.error?.message) message = payload.error.message;
    if (payload.error?.details?.remediation) {
      message += ` ${payload.error.details.remediation}`;
    }
    code = payload.error?.code;
  } catch {
    // Keep the bounded status-only fallback; response bodies may contain internals.
  }
  return new VidXPApiError(message, response.status, code);
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function abortError(): Error {
  const error = new Error("Operation cancelled");
  error.name = "AbortError";
  return error;
}
