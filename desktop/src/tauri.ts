import { invoke } from '@tauri-apps/api/core';

export type TargetKind = 'existing_local' | 'managed';
export type LifecycleOwnership = 'external' | 'desktop';

const WINDOWS_EXTENDED_PATH_PREFIX = '\\\\?\\';
const WINDOWS_EXTENDED_UNC_PREFIX = 'UNC\\';

export function displayPath(path: string): string {
  if (!path.startsWith(WINDOWS_EXTENDED_PATH_PREFIX)) return path;

  const remainder = path.slice(WINDOWS_EXTENDED_PATH_PREFIX.length);
  if (/^[a-z]:\\/i.test(remainder)) return remainder;
  if (remainder.slice(0, WINDOWS_EXTENDED_UNC_PREFIX.length).toUpperCase() === WINDOWS_EXTENDED_UNC_PREFIX) {
    const uncPath = remainder.slice(WINDOWS_EXTENDED_UNC_PREFIX.length);
    const [server, share] = uncPath.split('\\');
    if (server && share) return `\\\\${uncPath}`;
  }
  return path;
}

export interface TargetError {
  code?: string;
  message?: string;
  detail?: string;
  action?: string;
}

export interface FrontendCapability {
  available: boolean;
  launchable: boolean;
  optional: boolean;
  code: string;
  message: string;
  remediation: string;
}

export interface TargetProfile {
  id: string;
  display_name: string;
  schema_version: number;
  kind: TargetKind;
  lifecycle_ownership: LifecycleOwnership;
  executable?: string | null;
  canonical_executable?: string | null;
  display_executable?: string | null;
  data_root?: string | null;
  display_data_root?: string | null;
  repository_root?: string | null;
  display_repository_root?: string | null;
  model_directory?: string | null;
  display_model_directory?: string | null;
  vidxp_version?: string | null;
  probe_version?: string | number | null;
  compatibility_version?: string | number | null;
  launch_protocol_version?: string | number | null;
  last_validated_at?: string | null;
  validation_error?: TargetError | null;
  can_launch_frontend?: boolean | null;
  frontend?: FrontendCapability | null;
}

export interface TargetSetupState {
  profiles: TargetProfile[];
  selected_profile_id?: string | null;
  selected_profile?: TargetProfile | null;
  selected_profile_error?: TargetError | null;
  notice?: string | null;
  issues?: TargetError[];
}

export interface LocalTargetCandidate {
  executable: string;
  canonical_executable?: string | null;
  display_path?: string | null;
  display_name?: string | null;
  source?: string | null;
}

export interface LocalTargetValidation {
  compatible?: boolean;
  status?: 'compatible' | 'incompatible';
  canonical_executable?: string | null;
  executable?: string | null;
  executable_identity?: string | null;
  display_executable?: string | null;
  vidxp_version?: string | null;
  protocol_version?: string | number | null;
  probe_version?: string | number | null;
  launch_protocol_version?: string | number | null;
  python_executable?: string | null;
  display_python_executable?: string | null;
  python_version?: string | null;
  data_root?: string | null;
  display_data_root?: string | null;
  can_launch_frontend?: boolean | null;
  frontend?: FrontendCapability | null;
  error?: TargetError | null;
  warnings?: string[];
}

export interface LocalTargetInspection {
  state: 'ready_to_use' | 'update_required' | 'cannot_start';
  adoptable: boolean;
  executable: string;
  reported_version?: string | null;
  probe_compatible: boolean;
  launch_compatible: boolean;
  validation?: LocalTargetValidation | null;
  message: string;
  remediation: string;
  technical_details?: string | null;
}

interface RustRuntimeIdentity {
  python_executable: string;
  python_version: string;
}

interface RustTargetProfile extends Omit<TargetProfile, 'vidxp_version' | 'probe_version' | 'last_validated_at'> {
  executable: string;
  data_root: string;
  repository_root: string;
  observed_vidxp_version: string;
  probe_schema_version: number;
  probe_protocol_version: number;
  launch_protocol_version: number;
  runtime?: RustRuntimeIdentity | null;
  frontend?: FrontendCapability | null;
  last_successful_validation_at?: number | null;
}

interface RustTargetState {
  profiles: RustTargetProfile[];
  selected_profile_id?: string | null;
  issues?: TargetError[];
}

interface RustValidatedTarget {
  executable: string;
  product_version: string;
  probe_schema_version: number;
  probe_protocol_version: number;
  launch_protocol_version: number;
  runtime: RustRuntimeIdentity;
  data_root: string;
  frontend: FrontendCapability;
  validated_at: number;
}

interface RustTargetInspection {
  state: LocalTargetInspection['state'];
  adoptable: boolean;
  executable: string;
  reported_version?: string | null;
  probe_compatible: boolean;
  launch_compatible: boolean;
  validated?: RustValidatedTarget | null;
  message: string;
  remediation: string;
  technical_details?: string | null;
}

export interface CapabilitySpec {
  extra: string;
  label: string;
  description?: string;
}

export interface SurfaceSpec {
  extra: string;
  label: string;
  description: string;
  default: boolean;
}

export interface RuntimeManifest {
  package_version: string;
  capabilities: Record<string, CapabilitySpec>;
  surfaces: Record<string, SurfaceSpec>;
}

export interface RuntimeStatus {
  state: 'never_configured' | 'ready' | 'broken';
  ready: boolean;
  package_version: string;
  capabilities: string[];
  surfaces: string[];
  model_directory: string;
  detail: string;
}

export interface CachedModelEntry {
  id: string;
  label: string;
}

export interface ModelDirectoryInventory {
  directory: string;
  exists: boolean;
  readable: boolean;
  total_bytes: number;
  file_count: number;
  recognized_models: CachedModelEntry[];
  empty: boolean;
  verification_required: boolean;
  truncated: boolean;
  detail: string;
}

export interface InstallRuntimeRequest {
  capabilities: string[];
  surfaces: string[];
  prepare_models: boolean;
  model_directory?: string;
}

export interface InstallRuntimeResult {
  capabilities: string[];
  surfaces: string[];
  model_directory: string;
  prepared: boolean;
}

function normalizeProfile(profile: RustTargetProfile): TargetProfile {
  return {
    ...profile,
    canonical_executable: profile.executable,
    display_executable: displayPath(profile.executable),
    display_data_root: displayPath(profile.data_root),
    display_repository_root: displayPath(profile.repository_root),
    display_model_directory: profile.model_directory ? displayPath(profile.model_directory) : null,
    vidxp_version: profile.observed_vidxp_version,
    probe_version: profile.probe_protocol_version,
    compatibility_version: profile.probe_schema_version,
    launch_protocol_version: profile.launch_protocol_version,
    last_validated_at: profile.last_successful_validation_at
      ? new Date(profile.last_successful_validation_at * 1000).toISOString()
      : null,
    can_launch_frontend: profile.frontend?.launchable ?? null,
  };
}

function normalizeState(state: RustTargetState): TargetSetupState {
  const profiles = state.profiles.map(normalizeProfile);
  const selected = profiles.find((profile) => profile.id === state.selected_profile_id) ?? null;
  return {
    profiles,
    selected_profile_id: state.selected_profile_id,
    selected_profile: selected,
    selected_profile_error: selected?.validation_error ?? null,
    issues: state.issues ?? [],
  };
}

export async function targetSetupState(): Promise<TargetSetupState> {
  return normalizeState(await invoke<RustTargetState>('refresh_target_state'));
}

export async function discoverLocalTargets(): Promise<LocalTargetCandidate[]> {
  const result = await invoke<LocalTargetCandidate[] | { candidates: LocalTargetCandidate[] }>(
    'discover_local_targets',
  );
  const candidates = Array.isArray(result) ? result : result.candidates;
  return candidates.map((candidate) => ({
    ...candidate,
    canonical_executable: candidate.canonical_executable || candidate.executable,
    display_path: candidate.display_path || displayPath(candidate.executable),
    source: candidate.source || 'PATH',
  }));
}

export async function chooseLocalExecutable(): Promise<LocalTargetCandidate | null> {
  const result = await invoke<string | LocalTargetCandidate | null>('choose_local_executable');
  if (!result) return null;
  const candidate = typeof result === 'string' ? { executable: result } : result;
  return {
    ...candidate,
    canonical_executable: candidate.canonical_executable || candidate.executable,
    display_path: candidate.display_path || displayPath(candidate.executable),
    source: candidate.source || 'Selected file',
  };
}

export function validateLocalTarget(executable: string): Promise<LocalTargetValidation> {
  return invoke<RustValidatedTarget>('validate_local_target', { executable }).then((result) => ({
    compatible: true,
    executable: result.executable,
    canonical_executable: result.executable,
    display_executable: displayPath(result.executable),
    vidxp_version: result.product_version,
    protocol_version: result.probe_protocol_version,
    probe_version: result.probe_schema_version,
    launch_protocol_version: result.launch_protocol_version,
    python_executable: result.runtime.python_executable,
    display_python_executable: displayPath(result.runtime.python_executable),
    python_version: result.runtime.python_version,
    data_root: result.data_root,
    display_data_root: displayPath(result.data_root),
    can_launch_frontend: result.frontend.launchable,
    frontend: result.frontend,
    warnings:
      result.frontend.optional && !result.frontend.available ? [result.frontend.message] : undefined,
  }));
}

function normalizeValidatedTarget(result: RustValidatedTarget): LocalTargetValidation {
  return {
    compatible: true,
    executable: result.executable,
    canonical_executable: result.executable,
    display_executable: displayPath(result.executable),
    vidxp_version: result.product_version,
    protocol_version: result.probe_protocol_version,
    probe_version: result.probe_schema_version,
    launch_protocol_version: result.launch_protocol_version,
    python_executable: result.runtime.python_executable,
    display_python_executable: displayPath(result.runtime.python_executable),
    python_version: result.runtime.python_version,
    data_root: result.data_root,
    display_data_root: displayPath(result.data_root),
    can_launch_frontend: result.frontend.launchable,
    frontend: result.frontend,
    warnings:
      result.frontend.optional && !result.frontend.available ? [result.frontend.message] : undefined,
  };
}

export function inspectLocalTarget(executable: string): Promise<LocalTargetInspection> {
  return invoke<RustTargetInspection>('inspect_local_target', { executable }).then((result) => ({
    ...result,
    validation: result.validated ? normalizeValidatedTarget(result.validated) : null,
  }));
}

export function activateLocalTarget(request: {
  executable: string;
  displayName?: string;
  dataRoot?: string;
}): Promise<TargetProfile | TargetSetupState> {
  return invoke<RustTargetProfile>('adopt_local_target', {
    executable: request.executable,
    displayName: request.displayName || null,
  }).then(normalizeProfile);
}

export function chooseManagedTarget(): Promise<TargetProfile | TargetSetupState> {
  return invoke<RustTargetState>('begin_managed_setup').then(normalizeState);
}

export function runtimeManifest(): Promise<RuntimeManifest> {
  return invoke('runtime_manifest');
}

export function runtimeStatus(): Promise<RuntimeStatus> {
  return invoke('runtime_status');
}

export function modelDirectoryInventory(directory?: string): Promise<ModelDirectoryInventory> {
  return invoke('model_directory_inventory', { directory: directory || null });
}

export function chooseModelDirectory(): Promise<string | null> {
  return invoke('choose_model_directory');
}

export function installMediaRuntime(): Promise<unknown> {
  return invoke('install_media_runtime');
}

export function installRuntime(request: InstallRuntimeRequest): Promise<InstallRuntimeResult> {
  return invoke('install_runtime', { request });
}

export function launchUi(): Promise<void> {
  return invoke('launch_ui');
}

export function hideToTray(): Promise<void> {
  return invoke('hide_to_tray');
}

export function selectedProfile(state: TargetSetupState): TargetProfile | null {
  if (state.selected_profile) {
    return state.selected_profile;
  }
  return state.profiles.find((profile) => profile.id === state.selected_profile_id) ?? null;
}

export function isCompatible(validation: LocalTargetValidation): boolean {
  return validation.compatible === true || validation.status === 'compatible';
}

export function errorMessage(error: unknown, fallback = 'Something went wrong.'): string {
  if (typeof error === 'string') {
    return error;
  }
  if (error && typeof error === 'object') {
    const targetError = error as TargetError;
    const message = targetError.message ?? targetError.detail ?? fallback;
    return targetError.code ? `${targetError.code} · ${message}` : message;
  }
  return fallback;
}
