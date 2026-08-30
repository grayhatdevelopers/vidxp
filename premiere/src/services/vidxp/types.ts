export interface CapabilitySummary {
  name: string;
  description: string;
  supports_indexing: boolean;
  prepares_models: boolean;
  roles: string[];
}

export interface WorkspaceMedia {
  media_id: string;
  original_filename: string;
  duration_seconds?: number;
  state: string;
  in_active_snapshot: boolean;
}

export interface WorkspaceOverview {
  capabilities: CapabilitySummary[];
  media: WorkspaceMedia[];
  media_total: number;
  next_cursor?: string;
  index: {
    state: string;
    stage: string;
    message: string;
    summary?: {
      media_count: number;
      modalities: string[];
    };
  };
  next_actions: string[];
}

export interface ErrorDetail {
  code: string;
  message: string;
  retryable?: boolean;
  details?: {
    remediation?: string;
  };
}

export interface MediaUploadStatus {
  intent_id: string;
  original_filename: string;
  phase: string;
  media_id?: string;
  searchable: boolean;
  terminal: boolean;
  status: string;
  error?: ErrorDetail;
}

export interface MediaIngestionStatus {
  session_id: string;
  aggregate_state: string;
  index_modalities: string[];
  file_count: number;
  searchable_file_count: number;
  failed_file_count: number;
  index_failed_file_count: number;
  items: MediaUploadStatus[];
  terminal: boolean;
  poll_after_seconds: number;
  status: string;
  next_action: string;
}

export interface JobProgress {
  stage: string;
  message: string;
  current?: number;
  total?: number;
}

export interface SearchHit {
  modality: string;
  score: number;
  metadata: Record<string, unknown>;
}

export interface FusedMoment {
  moment_id?: string;
  rank: number;
  score: number;
  media_id: string;
  start: number;
  end: number;
  modalities: string[];
  hits: SearchHit[];
}

export interface FusedSearchResult {
  query_id: string;
  query: string;
  modalities: string[];
  moments: FusedMoment[];
}

export interface GroundedClaim {
  text: string;
  evidence_ids: string[];
}

export interface QueryModelIdentity {
  provider: "ollama";
  model: string;
}

export interface MomentEvidence {
  kind: "moment";
  evidence_id: string;
  media_id: string;
  modality: string;
  start: number;
  end: number;
  display_text?: string;
}

export interface ActorEvidence {
  kind: "actor";
  evidence_id: string;
  media_id: string;
  modality: "actor";
  start: number;
  end: number;
  display_text: string;
}

export type QueryEvidence = MomentEvidence | ActorEvidence;

export interface QueryAnswer {
  question: string;
  mode: "generated" | "evidence_only" | "no_evidence";
  model?: QueryModelIdentity;
  claims: GroundedClaim[];
  evidence: QueryEvidence[];
  moments: FusedMoment[];
  fallback_reason?: string;
}

export interface VidXPJob<TResult> {
  job_id: string;
  kind: string;
  state: string;
  progress?: JobProgress;
  result?: {
    kind: string;
    result: TResult;
  };
  error?: ErrorDetail;
  terminal: boolean;
  poll_after_seconds: number;
}
