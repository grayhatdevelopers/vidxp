import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  chunkPaths,
  collectSelectedClips,
  countReadyClips,
  filterLibrary,
} from "../premiere/library";
import type {
  PremiereAdapter,
  PremiereLibrary,
  PremiereMediaNode,
} from "../premiere/types";
import {
  createIdempotencyKey,
  VidXPClient,
  type VidXPFetch,
} from "../services/vidxp/client";
import type {
  CapabilitySummary,
  FusedMoment,
  WorkspaceOverview,
} from "../services/vidxp/types";
import {
  SpectrumActionButton,
  SpectrumButton,
  SpectrumCheckbox,
  SpectrumTextArea,
  SpectrumTextField,
} from "./components/Spectrum";

type ConnectionState =
  | { status: "disconnected" }
  | { status: "connecting" }
  | { status: "ready" }
  | { status: "error"; message: string };

type OperationState =
  | { status: "idle" }
  | { status: "indexing" | "searching"; message: string }
  | { status: "error"; message: string };

interface Notice {
  tone: "success" | "warning";
  title: string;
  message: string;
}

const DEFAULT_API_ADDRESS = "http://127.0.0.1:32191";

interface AppProps {
  fetchImpl?: VidXPFetch;
  premiere: PremiereAdapter;
}

export function App({ fetchImpl, premiere }: AppProps) {
  const abortController = useRef<AbortController | undefined>(undefined);
  const [apiAddress, setApiAddress] = useState(DEFAULT_API_ADDRESS);
  const [bearerToken, setBearerToken] = useState("");
  const [client, setClient] = useState<VidXPClient>();
  const [connection, setConnection] = useState<ConnectionState>({
    status: "disconnected",
  });
  const [library, setLibrary] = useState<PremiereLibrary>();
  const [libraryError, setLibraryError] = useState<string>();
  const [libraryFilter, setLibraryFilter] = useState("");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [capabilities, setCapabilities] = useState<CapabilitySummary[]>([]);
  const [indexModalities, setIndexModalities] = useState<string[]>([]);
  const [searchModalities, setSearchModalities] = useState<string[]>([]);
  const [workspace, setWorkspace] = useState<WorkspaceOverview>();
  const [operation, setOperation] = useState<OperationState>({ status: "idle" });
  const [notice, setNotice] = useState<Notice>();
  const [query, setQuery] = useState("");
  const [mediaScope, setMediaScope] = useState("");
  const [moments, setMoments] = useState<FusedMoment[]>([]);

  const loadLibrary = useCallback(async () => {
    setLibraryError(undefined);
    try {
      const nextLibrary = await premiere.getLibrary();
      setLibrary(nextLibrary);
      if (!nextLibrary) setSelectedIds([]);
    } catch (error) {
      setLibrary(undefined);
      setLibraryError(messageOf(error));
    }
  }, [premiere]);

  useEffect(() => {
    void loadLibrary();
    return () => abortController.current?.abort();
  }, [loadLibrary]);

  const selectedSet = useMemo(() => new Set(selectedIds), [selectedIds]);
  const selectedClips = useMemo(
    () => collectSelectedClips(library?.items ?? [], selectedSet),
    [library, selectedSet],
  );
  const visibleLibrary = useMemo(
    () => filterLibrary(library?.items ?? [], libraryFilter),
    [library, libraryFilter],
  );
  const indexableCapabilities = capabilities.filter(
    (capability) => capability.supports_indexing,
  );
  const searchableCapabilities = capabilities.filter((capability) =>
    capability.roles.includes("searchable"),
  );
  const busy = operation.status === "indexing" || operation.status === "searching";

  async function connect() {
    setConnection({ status: "connecting" });
    setNotice(undefined);
    try {
      const nextClient = new VidXPClient({
        baseUrl: apiAddress,
        bearerToken,
        fetchImpl,
      });
      const [, nextCapabilities, nextWorkspace] = await Promise.all([
        nextClient.health(),
        nextClient.listCapabilities(),
        nextClient.workspace(),
      ]);
      setClient(nextClient);
      setCapabilities(nextCapabilities);
      setIndexModalities(
        nextCapabilities
          .filter((capability) => capability.supports_indexing)
          .map((capability) => capability.name),
      );
      setSearchModalities(
        nextCapabilities
          .filter((capability) => capability.roles.includes("searchable"))
          .map((capability) => capability.name),
      );
      setWorkspace(nextWorkspace);
      setConnection({ status: "ready" });
    } catch (error) {
      setClient(undefined);
      setConnection({ status: "error", message: messageOf(error) });
    }
  }

  async function selectCurrentPremiereItems() {
    try {
      setSelectedIds(await premiere.getSelectedProjectItemIds());
    } catch (error) {
      setLibraryError(messageOf(error));
    }
  }

  async function indexSelection() {
    if (!client || selectedClips.length === 0 || indexModalities.length === 0) return;
    const controller = beginOperation();
    setNotice(undefined);
    const batches = chunkPaths(selectedClips.map((clip) => clip.nativePath!));
    let searchable = 0;
    const failures: string[] = [];

    try {
      for (const [batchIndex, paths] of batches.entries()) {
        const initial = await client.createLocalIngestion(
          paths,
          indexModalities,
          createIdempotencyKey(),
        );
        const final = await client.waitForLocalIngestion(
          initial,
          (status) => {
            setOperation({
              status: "indexing",
              message: `Batch ${batchIndex + 1} of ${batches.length} · ${status.status}`,
            });
          },
          controller.signal,
        );
        searchable += final.searchable_file_count;
        failures.push(
          ...final.items
            .filter((item) => item.error)
            .map(
              (item) =>
                `${item.original_filename}: ${item.error?.message ?? "Indexing failed."}`,
            ),
        );
      }
      setWorkspace(await client.workspace());
      setNotice({
        tone: failures.length > 0 ? "warning" : "success",
        title: failures.length > 0 ? "Indexing finished with issues" : "Indexing complete",
        message:
          failures.length > 0
            ? `${searchable} media item(s) are searchable. ${failures.join(" ")}`
            : `${searchable} media item(s) from Premiere are now searchable.`,
      });
      setOperation({ status: "idle" });
    } catch (error) {
      if (!controller.signal.aborted) {
        setOperation({ status: "error", message: messageOf(error) });
      }
    }
  }

  async function search() {
    if (!client || !query.trim()) return;
    const controller = beginOperation();
    setNotice(undefined);
    try {
      const initial = await client.submitSearch(
        {
          query,
          modalities: searchModalities,
          mediaId: mediaScope || undefined,
          topK: 20,
        },
        createIdempotencyKey(),
      );
      const completed = await client.waitForJob(
        initial,
        (job) => {
          setOperation({
            status: "searching",
            message: job.progress?.message ?? "VidXP is searching indexed media…",
          });
        },
        controller.signal,
      );
      const result = completed.result?.result;
      if (!result) throw new Error("VidXP completed the search without a result payload.");
      setMoments(result.moments);
      if (result.moments.length === 0) {
        setNotice({
          tone: "warning",
          title: "No matching moments",
          message: "Try a broader description, another search feature, or the complete indexed library.",
        });
      }
      setOperation({ status: "idle" });
    } catch (error) {
      if (!controller.signal.aborted) {
        setMoments([]);
        setOperation({ status: "error", message: messageOf(error) });
      }
    }
  }

  function beginOperation(): AbortController {
    abortController.current?.abort();
    const controller = new AbortController();
    abortController.current = controller;
    return controller;
  }

  function toggleSelected(id: string) {
    setSelectedIds((current) =>
      current.includes(id)
        ? current.filter((candidate) => candidate !== id)
        : [...current, id],
    );
  }

  return (
    <main className="panel">
      <header className="panel-header">
        <div>
          <span className="eyebrow">Premiere Pro · Preview</span>
          <h1>VidXP Search</h1>
          <p>Index project media and find the moment you remember.</p>
        </div>
      </header>

      <section className="section connection-section">
        <div className="section-heading">
          <div>
            <h2>VidXP connection</h2>
            <p>Use the API address shown by VidXP Desktop’s app integration service.</p>
          </div>
          <ConnectionBadge state={connection} />
        </div>
        <SpectrumTextField
          label="API address"
          value={apiAddress}
          placeholder={DEFAULT_API_ADDRESS}
          onValueChange={setApiAddress}
        />
        <details className="advanced">
          <summary>Shared-server authentication</summary>
          <label className="field">
            <span>Bearer token</span>
            {/* UXP's Spectrum password field is unreadable on macOS. */}
            <input
              className="native-secret"
              type="password"
              autoComplete="off"
              value={bearerToken}
              onChange={(event) => setBearerToken(event.currentTarget.value)}
            />
          </label>
          <p>The token stays in panel memory and is not written to the Premiere project.</p>
        </details>
        <SpectrumButton
          className="full-width-action"
          variant="primary"
          onPress={() => void connect()}
          disabled={busy}
        >
          {connection.status === "connecting" ? "Connecting…" : "Connect"}
        </SpectrumButton>
        {connection.status === "error" && (
          <p className="inline-error" role="alert">{connection.message}</p>
        )}
      </section>

      <section className="section">
        <div className="section-heading">
          <div>
            <h2>Premiere media</h2>
            <p>
              {library
                ? `${library.projectName}${library.sequenceName ? ` · ${library.sequenceName}` : ""}`
                : "Open a Premiere project to browse its bins."}
            </p>
          </div>
          <SpectrumActionButton
            className="inline-action"
            quiet
            onPress={() => void loadLibrary()}
          >
            Refresh
          </SpectrumActionButton>
        </div>
        {libraryError && <p className="inline-error" role="alert">{libraryError}</p>}
        {library && (
          <>
            <div className="toolbar">
              <SpectrumTextField
                ariaLabel="Filter Premiere media"
                type="search"
                placeholder="Filter clips and bins…"
                value={libraryFilter}
                onValueChange={setLibraryFilter}
              />
              <SpectrumActionButton
                className="inline-action"
                quiet
                onPress={() => void selectCurrentPremiereItems()}
              >
                Use current selection
              </SpectrumActionButton>
            </div>
            <div className="library" role="tree" aria-label="Premiere media library">
              {visibleLibrary.length > 0 ? (
                visibleLibrary.map((node) => (
                  <MediaTreeNode
                    key={node.id}
                    node={node}
                    selected={selectedSet}
                    inherited={false}
                    onToggle={toggleSelected}
                  />
                ))
              ) : (
                <p className="empty">No matching media.</p>
              )}
            </div>
            <p className="selection-summary">
              {selectedClips.length} selected · {countReadyClips(library.items)} file-backed media items available
            </p>
          </>
        )}
      </section>

      <section className="section">
        <div className="section-heading">
          <div>
            <h2>Index selected media</h2>
            <p>VidXP expands selected bins, deduplicates clips, and indexes in batches of ten.</p>
          </div>
        </div>
        <CapabilityChoices
          capabilities={indexableCapabilities}
          selected={indexModalities}
          onChange={(next) => setIndexModalities(next)}
          emptyMessage="Connect to VidXP to discover indexable features."
        />
        <SpectrumButton
          className="full-width-action"
          variant="cta"
          disabled={
            busy ||
            connection.status !== "ready" ||
            selectedClips.length === 0 ||
            indexModalities.length === 0
          }
          onPress={() => void indexSelection()}
        >
          {operation.status === "indexing"
            ? "Indexing…"
            : `Index ${selectedClips.length || "selected"} media item${selectedClips.length === 1 ? "" : "s"}`}
        </SpectrumButton>
      </section>

      <section className="section search-section">
        <div className="section-heading">
          <div>
            <h2>Search moments</h2>
            <p>Search one indexed video or the complete active VidXP library.</p>
          </div>
        </div>
        <SpectrumTextArea
          className="query-field"
          label="What happens in the moment?"
          value={query}
          placeholder="A door slams while someone says we need to leave…"
          onValueChange={setQuery}
        />
        <label className="field">
          <span>Scope</span>
          <select value={mediaScope} onChange={(event) => setMediaScope(event.currentTarget.value)}>
            <option value="">All indexed media</option>
            {workspace?.media
              .filter((media) => media.in_active_snapshot)
              .map((media) => (
                <option key={media.media_id} value={media.media_id}>
                  {media.original_filename}
                </option>
              ))}
          </select>
        </label>
        <CapabilityChoices
          capabilities={searchableCapabilities}
          selected={searchModalities}
          onChange={(next) => setSearchModalities(next)}
          emptyMessage="No searchable features are available yet."
        />
        <SpectrumButton
          className="full-width-action"
          variant="cta"
          disabled={
            busy ||
            connection.status !== "ready" ||
            !query.trim() ||
            searchModalities.length === 0
          }
          onPress={() => void search()}
        >
          {operation.status === "searching" ? "Searching…" : "Search indexed media"}
        </SpectrumButton>
      </section>

      {operation.status !== "idle" && (
        <section
          className={`status-card ${operation.status === "error" ? "error" : ""}`}
          role={operation.status === "error" ? "alert" : "status"}
          aria-live="polite"
        >
          <span className={busy ? "spinner" : "status-dot"} aria-hidden="true" />
          <div>
            <strong>{operation.status === "error" ? "Operation failed" : "VidXP is working"}</strong>
            <p>{operation.message}</p>
          </div>
        </section>
      )}

      {notice && (
        <section className={`notice ${notice.tone}`} role="status" aria-live="polite">
          <div>
            <strong>{notice.title}</strong>
            <p>{notice.message}</p>
          </div>
          <SpectrumActionButton
            className="dismiss-action"
            ariaLabel="Dismiss notification"
            quiet
            onPress={() => setNotice(undefined)}
          >
            ×
          </SpectrumActionButton>
        </section>
      )}

      <SearchResults moments={moments} workspace={workspace} />
    </main>
  );
}

function ConnectionBadge({ state }: { state: ConnectionState }) {
  const label =
    state.status === "ready"
      ? "Connected"
      : state.status === "connecting"
        ? "Checking"
        : state.status === "error"
          ? "Unavailable"
          : "Not connected";
  return <span className={`badge ${state.status}`}>{label}</span>;
}

function MediaTreeNode({
  node,
  selected,
  inherited,
  onToggle,
}: {
  node: PremiereMediaNode;
  selected: ReadonlySet<string>;
  inherited: boolean;
  onToggle: (id: string) => void;
}) {
  const effectiveSelected = inherited || selected.has(node.id);
  if (node.kind === "clip") {
    const disabled = node.availability !== "ready";
    return (
      <div className={`tree-row clip ${disabled ? "disabled" : ""}`} role="treeitem" title={node.detail ?? node.nativePath}>
        <SpectrumCheckbox
          ariaLabel={`Select ${node.name}`}
          disabled={disabled || inherited}
          checked={effectiveSelected}
          onCheckedChange={() => onToggle(node.id)}
        ></SpectrumCheckbox>
        <span className="node-icon" aria-hidden="true">▰</span>
        <span className="node-name">{node.name}</span>
        {disabled && <span className="node-state">{node.availability}</span>}
      </div>
    );
  }
  return (
    <details className="tree-bin" open role="treeitem">
      <summary>
        <SpectrumCheckbox
          ariaLabel={`Select ${node.name} bin`}
          disabled={inherited}
          checked={effectiveSelected}
          onPress={(event) => event.stopPropagation()}
          onCheckedChange={() => onToggle(node.id)}
        ></SpectrumCheckbox>
        <span className="node-icon" aria-hidden="true">▸</span>
        <span className="node-name">{node.name}</span>
        <span className="node-state">{countReadyClips(node.children)}</span>
      </summary>
      <div className="tree-children" role="group">
        {node.children.map((child) => (
          <MediaTreeNode
            key={child.id}
            node={child}
            selected={selected}
            inherited={effectiveSelected}
            onToggle={onToggle}
          />
        ))}
      </div>
    </details>
  );
}

function CapabilityChoices({
  capabilities,
  selected,
  onChange,
  emptyMessage,
}: {
  capabilities: CapabilitySummary[];
  selected: string[];
  onChange: (next: string[]) => void;
  emptyMessage: string;
}) {
  if (capabilities.length === 0) return <p className="empty">{emptyMessage}</p>;
  return (
    <div className="capabilities">
      {capabilities.map((capability) => (
        <div
          className={`capability ${selected.includes(capability.name) ? "selected" : ""}`}
          key={capability.name}
          title={capability.description}
        >
          <SpectrumCheckbox
            ariaLabel={`Use ${capability.name}`}
            checked={selected.includes(capability.name)}
            onCheckedChange={() =>
              onChange(
                selected.includes(capability.name)
                  ? selected.filter((name) => name !== capability.name)
                  : [...selected, capability.name],
              )
            }
          >
            {capability.name}
          </SpectrumCheckbox>
          <span>
            <small>{capability.description}</small>
          </span>
        </div>
      ))}
    </div>
  );
}

function SearchResults({
  moments,
  workspace,
}: {
  moments: FusedMoment[];
  workspace?: WorkspaceOverview;
}) {
  if (moments.length === 0) return null;
  const names = new Map(
    workspace?.media.map((media) => [media.media_id, media.original_filename]) ?? [],
  );
  return (
    <section className="section results">
      <div className="section-heading">
        <div>
          <h2>Matching moments</h2>
          <p>{moments.length} ranked result{moments.length === 1 ? "" : "s"}</p>
        </div>
      </div>
      <ol>
        {moments.map((moment) => (
          <li key={moment.moment_id ?? `${moment.media_id}-${moment.rank}-${moment.start}`}>
            <div className="result-rank">{moment.rank}</div>
            <div className="result-copy">
              <strong>{names.get(moment.media_id) ?? moment.media_id}</strong>
              <span>{formatTime(moment.start)} – {formatTime(moment.end)}</span>
              <div className="result-tags">
                {moment.modalities.map((modality) => <span key={modality}>{modality}</span>)}
              </div>
            </div>
            <span className="score">{moment.score.toFixed(3)}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}

function formatTime(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const remainder = whole % 60;
  return hours > 0
    ? `${hours}:${minutes.toString().padStart(2, "0")}:${remainder.toString().padStart(2, "0")}`
    : `${minutes}:${remainder.toString().padStart(2, "0")}`;
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
