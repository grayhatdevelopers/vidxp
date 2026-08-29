import { invoke } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';

export type TargetKind = 'existing_local' | 'managed';
export type LifecycleOwnership = 'external' | 'desktop';

const WINDOWS_EXTENDED_PATH_PREFIX = '\\\\?\\';
const WINDOWS_EXTENDED_UNC_PREFIX = 'UNC\\';

export function displayPath(path: string): string {
  if (!path.startsWith(WINDOWS_EXTENDED_PATH_PREFIX)) return path;
  const remainder = path.slice(WINDOWS_EXTENDED_PATH_PREFIX.length);
  if (/^[a-z]:\\/i.test(remainder)) return remainder;
  if (remainder.slice(0, WINDOWS_EXTENDED_UNC_PREFIX.length).toUpperCase() === WINDOWS_EXTENDED_UNC_PREFIX) {
    const [server, share] = remainder.slice(WINDOWS_EXTENDED_UNC_PREFIX.length).split('\\');
    if (server && share) return `\\\\${remainder.slice(WINDOWS_EXTENDED_UNC_PREFIX.length)}`;
  }
  return path;
}

export interface TargetError {
  code: string;
  message: string;
}

export interface FrontendCapability {
  available: boolean;
  launchable: boolean;
  optional: boolean;
  code: string;
  message: string;
  remediation: string;
}

interface RuntimeIdentity {
  python_executable: string;
  python_version: string;
  implementation: string;
  prefix: string;
  base_prefix: string;
}

interface WireTargetProfile {
  id: string;
  display_name: string;
  schema_version: number;
  kind: TargetKind;
  lifecycle_ownership: LifecycleOwnership;
  executable: string;
  data_root: string;
  repository_root: string;
  observed_vidxp_version: string;
  probe_schema_version: number;
  probe_protocol_version: number;
  launch_protocol_version: number;
  runtime: RuntimeIdentity | null;
  frontend: FrontendCapability;
  last_successful_validation_at: number | null;
  validation_error: TargetError | null;
  managed_runtime_profile?: string;
  capabilities: string[];
  surfaces: string[];
  model_directory?: string;
}

interface WireTargetState {
  profiles: WireTargetProfile[];
  selected_profile_id: string | null;
  issues: TargetError[];
}

export interface TargetProfile extends WireTargetProfile {
  display_executable: string;
  display_data_root: string;
  display_repository_root: string;
  display_model_directory?: string;
  last_validated_at: string | null;
}

export interface TargetSetupState {
  profiles: TargetProfile[];
  selected_profile_id: string | null;
  issues: TargetError[];
}

export interface LocalTargetCandidate {
  executable: string;
  display_path: string;
  source: string;
}

interface WireValidatedTarget {
  executable: string;
  product_version: string;
  probe_schema_version: number;
  probe_protocol_version: number;
  launch_protocol_version: number;
  runtime: RuntimeIdentity;
  data_root: string;
  repository_root: string;
  frontend: FrontendCapability;
  surfaces: string[];
  validated_at: number;
}

interface WireTargetInspection {
  state: 'ready_to_use' | 'update_required' | 'cannot_start';
  adoptable: boolean;
  executable: string;
  reported_version: string | null;
  probe_compatible: boolean;
  launch_compatible: boolean;
  validated: WireValidatedTarget | null;
  message: string;
  remediation: string;
  technical_details: string | null;
}

export interface LocalTargetValidation {
  canonical_executable: string;
  protocol_version: number;
  launch_protocol_version: number;
  python_version: string;
  display_data_root: string;
  can_launch_frontend: boolean;
  frontend: FrontendCapability;
  surfaces: string[];
}

export interface LocalTargetInspection extends Omit<WireTargetInspection, 'validated'> {
  validation: LocalTargetValidation | null;
}

export interface CapabilitySpec {
  extra: string;
  label: string;
  description: string;
  models: { cache_key: string; download_size_bytes: number }[];
}

export interface SurfaceSpec {
  extra: string;
  label: string;
  description: string;
  default: boolean;
}

export interface RuntimeManifest {
  package_version: string;
  managed_runtime_estimated_size_bytes: number;
  capabilities: Record<string, CapabilitySpec>;
  surfaces: Record<string, SurfaceSpec>;
}

export interface RuntimeStatus {
  state: 'never_configured' | 'ready' | 'broken';
  ready: boolean;
  runtime_profile: string | null;
  package_version: string;
  capabilities: string[];
  surfaces: string[];
  model_directory: string;
  detail: string;
}

export interface ModelDirectoryInventory {
  directory: string;
  exists: boolean;
  readable: boolean;
  total_bytes: number;
  file_count: number;
  recognized_models: { id: string; label: string }[];
  empty: boolean;
  verification_required: boolean;
  truncated: boolean;
  detail: string;
}

export interface ManagedSetupDraft {
  id: string;
  previous_profile_id: string | null;
}

export interface InstallRuntimeRequest {
  capabilities: string[];
  surfaces: string[];
  prepare_models: boolean;
  model_directory?: string;
  draft_id: string;
}

export interface ManagedSetupProgress {
  draft_id: string;
  current: number;
  total: number;
  stage: string;
  message: string;
  model_message?: string | null;
  model_current?: number | null;
  model_total?: number | null;
}

export interface InstallRuntimeResult {
  package_version: string;
  capabilities: string[];
  surfaces: string[];
  model_directory: string;
  prepared: boolean;
}

export interface InstallTransitionResult {
  install: InstallRuntimeResult;
  setup: TargetSetupState;
}

export interface DoctorCheck {
  capability: string;
  kind: 'distribution' | 'model' | string;
  name: string;
  installed_version?: string | null;
  download_size_bytes?: number | null;
  ok: boolean;
  error?: string | null;
}

export interface DoctorReport {
  ok: boolean;
  modalities: string[];
  checks: DoctorCheck[];
}

export interface LocalServerStatus {
  state: 'stopped' | 'starting' | 'ready';
  running: boolean;
  shared: boolean;
  port: number | null;
  origin: string | null;
  health_url: string | null;
  mcp_url: string | null;
  bearer_token: string | null;
  detail: string;
}

export interface BrowserServiceStatus {
  state: 'stopped' | 'ready';
  running: boolean;
  shared: boolean;
  port: number | null;
  local_url: string | null;
  network_url: string | null;
  detail: string;
}

export interface LocalWorkerStatus {
  running: boolean;
  detail: string;
}

export interface CodexPluginInstallResult {
  plugin_name: string;
  plugin_id: string | null;
  plugin_version: string;
  marketplace_name: string;
  marketplace_path: string;
  installed_path: string | null;
  detail: string;
}

export type PremiereHostKind = 'cep' | 'uxp' | 'unsupported';

export interface PremiereInstallation {
  display_name: string;
  version: string;
  executable: string;
  host_kind: PremiereHostKind;
  compatible: boolean;
}

export interface PremiereIntegrationState {
  installations: PremiereInstallation[];
  platform_supported: boolean;
  installer_available: boolean;
  cep_package_available: boolean;
  uxp_package_available: boolean;
  cep_installed: boolean;
  uxp_installed: boolean;
  detail: string;
}

export interface PremiereInstallResult {
  installed_hosts: PremiereHostKind[];
  opened_packages: string[];
  detail: string;
}

interface WireInstallTransitionResult {
  install: InstallRuntimeResult;
  setup: WireTargetState;
}

function normalizeProfile(profile: WireTargetProfile): TargetProfile {
  return {
    ...profile,
    display_executable: displayPath(profile.executable),
    display_data_root: displayPath(profile.data_root),
    display_repository_root: displayPath(profile.repository_root),
    display_model_directory: profile.model_directory ? displayPath(profile.model_directory) : undefined,
    last_validated_at: profile.last_successful_validation_at
      ? new Date(profile.last_successful_validation_at * 1000).toISOString()
      : null,
  };
}

function normalizeState(state: WireTargetState): TargetSetupState {
  return { ...state, profiles: state.profiles.map(normalizeProfile) };
}

export function targetSetupState(): Promise<TargetSetupState> {
  return invoke<WireTargetState>('target_state').then(normalizeState);
}

export function recheckTargetState(): Promise<TargetSetupState> {
  return invoke<WireTargetState>('refresh_target_state').then(normalizeState);
}

export async function discoverLocalTargets(): Promise<LocalTargetCandidate[]> {
  const candidates = await invoke<{ executable: string }[]>('discover_local_targets');
  return candidates.map(({ executable }) => ({
    executable,
    display_path: displayPath(executable),
    source: 'PATH',
  }));
}

export async function chooseLocalExecutable(): Promise<LocalTargetCandidate | null> {
  const executable = await invoke<string | null>('choose_local_executable');
  return executable
    ? { executable, display_path: displayPath(executable), source: 'Selected file' }
    : null;
}

export function inspectLocalTarget(executable: string): Promise<LocalTargetInspection> {
  return invoke<WireTargetInspection>('inspect_local_target', { executable }).then((result) => ({
    ...result,
    validation: result.validated
      ? {
          canonical_executable: result.validated.executable,
          protocol_version: result.validated.probe_protocol_version,
          launch_protocol_version: result.validated.launch_protocol_version,
          python_version: result.validated.runtime.python_version,
          display_data_root: displayPath(result.validated.data_root),
          can_launch_frontend: result.validated.frontend.launchable,
          frontend: result.validated.frontend,
          surfaces: result.validated.surfaces,
        }
      : null,
  }));
}

export function activateLocalTarget(executable: string, displayName?: string): Promise<TargetSetupState> {
  return invoke<WireTargetState>('adopt_local_target', {
    executable,
    displayName: displayName || null,
  }).then(normalizeState);
}

export function selectTargetProfile(profileId: string): Promise<TargetSetupState> {
  return invoke<WireTargetState>('select_target_profile', { profileId }).then(normalizeState);
}

export function deleteTargetProfile(profileId: string): Promise<TargetSetupState> {
  return invoke<WireTargetState>('delete_target_profile', { profileId }).then(normalizeState);
}

export function confirmForgetTarget(displayName: string): Promise<boolean> {
  return invoke('confirm_forget_target', { displayName });
}

export function beginManagedSetup(): Promise<ManagedSetupDraft> {
  return invoke('begin_managed_setup');
}

export function cancelManagedSetup(draftId: string): Promise<TargetSetupState> {
  return invoke<WireTargetState>('cancel_managed_setup', { draftId }).then(normalizeState);
}

export function cancelManagedSetupOperation(draftId: string): Promise<void> {
  return invoke('cancel_managed_setup_operation', { draftId });
}

export function runtimeManifest(): Promise<RuntimeManifest> { return invoke('runtime_manifest'); }
export function runtimeStatus(): Promise<RuntimeStatus> { return invoke('runtime_status'); }
export function modelDirectoryInventory(directory?: string): Promise<ModelDirectoryInventory> {
  return invoke('model_directory_inventory', { directory: directory || null });
}
export function chooseModelDirectory(): Promise<string | null> { return invoke('choose_model_directory'); }
export function installMediaRuntime(draftId: string, totalSteps: number): Promise<RuntimeStatus> {
  return invoke('install_media_runtime', { draftId, totalSteps });
}
export function installRuntime(request: InstallRuntimeRequest): Promise<InstallTransitionResult> {
  return invoke<WireInstallTransitionResult>('install_runtime', { request }).then((result) => ({
    install: result.install,
    setup: normalizeState(result.setup),
  }));
}
export function onManagedSetupProgress(
  handler: (progress: ManagedSetupProgress) => void,
): Promise<() => void> {
  return listen<ManagedSetupProgress>('managed-setup-progress', (event) => handler(event.payload));
}
export function prepareManagedModels(draftId: string): Promise<TargetSetupState> {
  return invoke<WireTargetState>('prepare_managed_models', { draftId }).then(normalizeState);
}
export function launchUi(): Promise<void> { return invoke('launch_ui'); }
export function targetDoctor(): Promise<DoctorReport> { return invoke('target_doctor'); }
export function configureExternalInstallation(capabilities: string[], surfaces: string[]): Promise<TargetSetupState> {
  return invoke<WireTargetState>('configure_external_installation', { capabilities, surfaces }).then(normalizeState);
}
export function mcpClientConfig(): Promise<string> { return invoke('mcp_client_config'); }
export function installCodexPlugin(): Promise<CodexPluginInstallResult> { return invoke('install_codex_plugin'); }
export function premiereIntegrationState(): Promise<PremiereIntegrationState> { return invoke('premiere_integration_state'); }
export function installPremiereExtensions(): Promise<PremiereInstallResult> { return invoke('install_premiere_extensions'); }
export function uninstallPremiereExtensions(): Promise<void> { return invoke('uninstall_premiere_extensions'); }
export function localWorkerStatus(): Promise<LocalWorkerStatus> { return invoke('local_worker_status'); }
export function startLocalWorker(): Promise<LocalWorkerStatus> { return invoke('start_local_worker'); }
export function stopLocalWorker(): Promise<LocalWorkerStatus> { return invoke('stop_local_worker'); }
export function browserServiceStatus(): Promise<BrowserServiceStatus> { return invoke('browser_service_status'); }
export function startSharedBrowser(): Promise<BrowserServiceStatus> { return invoke('start_shared_browser'); }
export function stopBrowserService(): Promise<BrowserServiceStatus> { return invoke('stop_browser_service'); }
export function localServerStatus(): Promise<LocalServerStatus> { return invoke('local_server_status'); }
export function startLocalServer(): Promise<LocalServerStatus> { return invoke('start_local_server'); }
export function startSharedServer(): Promise<LocalServerStatus> { return invoke('start_shared_server'); }
export function stopLocalServer(): Promise<LocalServerStatus> { return invoke('stop_local_server'); }

export function selectedProfile(state: TargetSetupState): TargetProfile | null {
  return state.profiles.find((profile) => profile.id === state.selected_profile_id) ?? null;
}

export function errorMessage(error: unknown, fallback = 'Something went wrong.'): string {
  if (typeof error === 'string') return error;
  if (error && typeof error === 'object') {
    const targetError = error as Partial<TargetError>;
    const message = targetError.message ?? fallback;
    return targetError.code ? `${targetError.code} · ${message}` : message;
  }
  return fallback;
}
