use std::{
    borrow::Cow,
    collections::{BTreeMap, BTreeSet},
    env, fs,
    io::{self, Read, Write},
    net::{SocketAddr, TcpListener, TcpStream},
    path::{Path, PathBuf},
    process::Command,
    sync::{
        Arc, Mutex, OnceLock,
        atomic::{AtomicBool, AtomicU64, Ordering},
    },
    thread,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

use atomic_write_file::AtomicWriteFile;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use tauri::{
    AppHandle, Emitter, Manager, RunEvent, WindowEvent,
    menu::{Menu, MenuItem, PredefinedMenuItem, Submenu},
    tray::TrayIconBuilder,
};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
use tauri_plugin_opener::OpenerExt;
use tauri_plugin_shell::ShellExt;

mod activation;
mod background_process;
mod browser_readiness;
mod lifecycle;
mod media_setup;
mod target_profiles;

use activation::{ActivationRecovery, ActivationStage, activation_recovery};
use lifecycle::{
    ActiveOperationGuard, ActiveOperations, DesktopAction, DesktopActivation, DesktopCloseAction,
    UiProcessAction, action_for_activation, close_action, ui_process_action,
};
use media_setup::{SystemInstallPlan, display_command, required_encoder_missing};

const RUNTIME_MANIFEST_BYTES: &[u8] =
    include_bytes!(concat!(env!("OUT_DIR"), "/runtime-manifest.json"));
const RUNTIME_CONSTRAINTS_BYTES: &[u8] =
    include_bytes!(concat!(env!("OUT_DIR"), "/runtime-constraints.txt"));
const RUNTIME_PACKAGE_WHEEL_BYTES: &[u8] =
    include_bytes!(concat!(env!("OUT_DIR"), "/runtime-package.whl"));
const RUNTIME_PACKAGE_WHEEL_NAME: &str =
    include_str!(concat!(env!("OUT_DIR"), "/runtime-package-name.txt"));
const RUNTIME_PACKAGE_WHEEL_SHA256: &str =
    include_str!(concat!(env!("OUT_DIR"), "/runtime-package-sha256.txt"));
const MODEL_CACHE_CATALOG_BYTES: &[u8] = include_bytes!("../../model-cache-catalog.json");
const PRODUCT_DATA_DIRECTORY_NAME: &str = "VidXP";
const RUNTIME_CONSTRAINTS_FILE_NAME: &str = "runtime-constraints.txt";
const MAX_SETUP_OUTPUT_BYTES: usize = 4 * 1024 * 1024;
static READINESS_SEQUENCE: AtomicU64 = AtomicU64::new(0);

#[derive(Clone, Deserialize, Serialize)]
struct CapabilitySpec {
    extra: String,
    modality: String,
    label: String,
}

#[derive(Clone, Deserialize, Serialize)]
struct SurfaceSpec {
    extra: String,
    label: String,
    description: String,
    default: bool,
}

#[derive(Clone, Deserialize, Serialize)]
struct MediaRuntimeSpec {
    strategy: String,
    executables: Vec<String>,
    reason: String,
}

#[derive(Clone, Deserialize, Serialize)]
struct RuntimeManifest {
    schema_version: u32,
    desktop_version: String,
    package_name: String,
    package_version: String,
    dependency_index: String,
    dependency_constraints_sha256: String,
    python_version: String,
    uv_version: String,
    surfaces: BTreeMap<String, SurfaceSpec>,
    capabilities: BTreeMap<String, CapabilitySpec>,
    media_runtime: MediaRuntimeSpec,
}

#[derive(Deserialize)]
struct InstallRequest {
    capabilities: Vec<String>,
    surfaces: Vec<String>,
    prepare_models: bool,
    model_directory: Option<String>,
    draft_id: String,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
struct ManagedSetupDraft {
    id: String,
    previous_profile_id: Option<String>,
}

#[derive(Serialize)]
struct InstallResult {
    package_version: String,
    capabilities: Vec<String>,
    surfaces: Vec<String>,
    model_directory: String,
    prepared: bool,
}

#[derive(Serialize)]
struct InstallTransitionResult {
    install: InstallResult,
    setup: target_profiles::TargetState,
}

#[derive(Clone, Serialize)]
struct ManagedSetupProgress {
    draft_id: String,
    current: u8,
    total: u8,
    stage: String,
    message: String,
    model_message: Option<String>,
    model_current: Option<u64>,
    model_total: Option<u64>,
}

fn emit_managed_setup_progress(
    app: &AppHandle,
    draft_id: &str,
    current: u8,
    total: u8,
    stage: &str,
    message: &str,
) {
    let _ = app.emit(
        "managed-setup-progress",
        ManagedSetupProgress {
            draft_id: draft_id.into(),
            current,
            total,
            stage: stage.into(),
            message: message.into(),
            model_message: None,
            model_current: None,
            model_total: None,
        },
    );
}

fn emit_managed_model_progress(
    app: &AppHandle,
    draft_id: &str,
    current: u8,
    total: u8,
    progress: &ManagedModelJobProgress,
) {
    let _ = app.emit(
        "managed-setup-progress",
        ManagedSetupProgress {
            draft_id: draft_id.into(),
            current,
            total,
            stage: "models".into(),
            message: "Verifying and downloading selected model files".into(),
            model_message: Some(progress.message.clone()),
            model_current: progress.current,
            model_total: progress.total,
        },
    );
}

#[derive(Deserialize)]
struct ManagedModelJobProgress {
    message: String,
    current: Option<u64>,
    total: Option<u64>,
}

#[derive(Serialize)]
struct RuntimeStatus {
    state: RuntimeState,
    ready: bool,
    runtime_profile: Option<String>,
    package_version: String,
    capabilities: Vec<String>,
    surfaces: Vec<String>,
    model_directory: String,
    detail: String,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
enum RuntimeState {
    NeverConfigured,
    Ready,
    Broken,
}

#[derive(Clone, Deserialize, Serialize)]
struct CachedModelEntry {
    id: String,
    label: String,
}

#[derive(Deserialize)]
struct ModelCacheCatalogEntry {
    id: String,
    label: String,
    relative_artifact: String,
}

#[derive(Serialize)]
struct ModelDirectoryInventory {
    directory: String,
    exists: bool,
    readable: bool,
    total_bytes: u64,
    file_count: u64,
    recognized_models: Vec<CachedModelEntry>,
    empty: bool,
    verification_required: bool,
    truncated: bool,
    detail: String,
}

#[derive(Clone, Serialize)]
struct MediaRuntimeStatus {
    ready: bool,
    ffmpeg_executable: Option<String>,
    ffprobe_executable: Option<String>,
    required_encoders: Vec<String>,
    errors: Vec<String>,
    package_manager: Option<String>,
    install_command: Option<String>,
    automatic_install: bool,
}

struct VerifiedMediaRuntime {
    ffmpeg: PathBuf,
    ffprobe: PathBuf,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
struct ActiveRuntime {
    schema_version: u32,
    manifest_sha256: String,
    profile: String,
    package_version: String,
    capabilities: Vec<String>,
    #[serde(default)]
    surfaces: Vec<String>,
    #[serde(default)]
    model_directory: PathBuf,
}

#[derive(Clone)]
struct DesktopPaths {
    private_data: PathBuf,
    data: PathBuf,
    cache: PathBuf,
    repository: PathBuf,
    runtimes: PathBuf,
    python: PathBuf,
    models: PathBuf,
    active_runtime: PathBuf,
    activation_journal: PathBuf,
}

type WorkerStopper = dyn Fn(&Path, &DesktopPaths, Instant) + Send + Sync;

struct ActiveWorkerOperation {
    id: u64,
    runtime: PathBuf,
    paths: DesktopPaths,
    stop_claimed: bool,
}

#[derive(Default)]
struct WorkerStopState {
    next_id: u64,
    active: Option<ActiveWorkerOperation>,
    last_stopped_runtime: Option<PathBuf>,
}

struct WorkerStopSupervisor {
    state: Mutex<WorkerStopState>,
    stopper: Arc<WorkerStopper>,
}

impl Default for WorkerStopSupervisor {
    fn default() -> Self {
        Self {
            state: Mutex::new(WorkerStopState::default()),
            stopper: Arc::new(stop_worker_before),
        }
    }
}

impl WorkerStopSupervisor {
    #[cfg(test)]
    fn with_stopper(stopper: Arc<WorkerStopper>) -> Self {
        Self {
            state: Mutex::new(WorkerStopState::default()),
            stopper,
        }
    }

    fn register(
        self: &Arc<Self>,
        runtime: PathBuf,
        paths: DesktopPaths,
    ) -> Result<WorkerOperationGuard, String> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| "The preparation worker supervisor is unavailable.".to_string())?;
        if state.active.is_some() {
            return Err("Another worker-backed managed operation is already active.".into());
        }
        state.next_id += 1;
        let id = state.next_id;
        state.last_stopped_runtime = None;
        state.active = Some(ActiveWorkerOperation {
            id,
            runtime,
            paths,
            stop_claimed: false,
        });
        Ok(WorkerOperationGuard {
            supervisor: self.clone(),
            id,
            settled: false,
        })
    }

    fn claim(&self, id: u64) -> Option<(PathBuf, DesktopPaths)> {
        let mut state = self.state.lock().ok()?;
        let active = state.active.as_mut()?;
        if active.id != id || active.stop_claimed {
            return None;
        }
        active.stop_claimed = true;
        Some((active.runtime.clone(), active.paths.clone()))
    }

    fn finish(&self, id: u64, runtime: PathBuf) {
        if let Ok(mut state) = self.state.lock()
            && state.active.as_ref().is_some_and(|active| active.id == id)
        {
            state.active = None;
            state.last_stopped_runtime = Some(runtime);
        }
    }

    fn stop_active_before(&self, deadline: Instant) -> Option<PathBuf> {
        let claim = {
            let mut state = self.state.lock().ok()?;
            if let Some(active) = state.active.as_mut() {
                if active.stop_claimed {
                    return Some(active.runtime.clone());
                }
                active.stop_claimed = true;
                Some((active.id, active.runtime.clone(), active.paths.clone()))
            } else {
                return state.last_stopped_runtime.clone();
            }
        };
        let (id, runtime, paths) = claim?;
        (self.stopper)(&runtime, &paths, deadline);
        self.finish(id, runtime.clone());
        Some(runtime)
    }
}

struct WorkerOperationGuard {
    supervisor: Arc<WorkerStopSupervisor>,
    id: u64,
    settled: bool,
}

impl WorkerOperationGuard {
    fn stop_before(&mut self, deadline: Instant) {
        if self.settled {
            return;
        }
        if let Some((runtime, paths)) = self.supervisor.claim(self.id) {
            (self.supervisor.stopper)(&runtime, &paths, deadline);
            self.supervisor.finish(self.id, runtime);
        }
        self.settled = true;
    }
}

impl Drop for WorkerOperationGuard {
    fn drop(&mut self) {
        self.stop_before(Instant::now() + Duration::from_secs(5));
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct ActivationJournal {
    schema_version: u32,
    stage: ActivationStage,
    previous_active_bytes: Option<Vec<u8>>,
    previous_targets: target_profiles::TargetState,
    candidate_active: ActiveRuntime,
    candidate_targets: target_profiles::TargetState,
}

#[derive(Debug, Default, Eq, PartialEq)]
struct RuntimeReconciliation {
    removed_directories: usize,
    reclaimed_bytes: u64,
    failures: Vec<String>,
}

struct ManagedUi {
    process: background_process::OwnedChild,
    port: u16,
    local_url: String,
    network_url: Option<String>,
    shared: bool,
    profile_id: String,
}

#[derive(Clone, Debug, Serialize)]
struct BrowserServiceStatus {
    state: &'static str,
    running: bool,
    shared: bool,
    port: Option<u16>,
    local_url: Option<String>,
    network_url: Option<String>,
    detail: String,
}

struct ManagedApiService {
    process: background_process::OwnedChild,
    port: u16,
    health_host: String,
    origin: String,
    health_url: String,
    mcp_url: String,
    bearer_token: Option<String>,
    shared: bool,
    profile_id: String,
}

#[derive(Clone, Debug, Serialize)]
struct LocalServerStatus {
    state: &'static str,
    running: bool,
    shared: bool,
    port: Option<u16>,
    origin: Option<String>,
    health_url: Option<String>,
    mcp_url: Option<String>,
    bearer_token: Option<String>,
    detail: String,
}

#[derive(Debug, Deserialize)]
struct ApiShareDetails {
    origin: String,
    host: String,
    port: u16,
    health_url: String,
    mcp_url: String,
    bearer_token: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct LocalWorkerStatus {
    running: bool,
    detail: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct CodexPluginInstallResult {
    plugin_name: String,
    plugin_id: Option<String>,
    plugin_version: String,
    marketplace_name: String,
    marketplace_path: String,
    installed_path: Option<String>,
    detail: String,
}

#[derive(Clone)]
struct TrayMenuItems {
    installation: MenuItem<tauri::Wry>,
    browser: Submenu<tauri::Wry>,
    open_browser: MenuItem<tauri::Wry>,
    share_browser: MenuItem<tauri::Wry>,
    stop_browser: MenuItem<tauri::Wry>,
    worker: Submenu<tauri::Wry>,
    start_worker: MenuItem<tauri::Wry>,
    stop_worker: MenuItem<tauri::Wry>,
    server: Submenu<tauri::Wry>,
    start_server: MenuItem<tauri::Wry>,
    share_server: MenuItem<tauri::Wry>,
    stop_server: MenuItem<tauri::Wry>,
}

struct DesktopState {
    ui_process: Mutex<Option<ManagedUi>>,
    api_process: Mutex<Option<ManagedApiService>>,
    worker_stop: Arc<WorkerStopSupervisor>,
    operation_cancellation: Arc<Mutex<Option<background_process::CancellationToken>>>,
    transition: Arc<Mutex<TransitionState>>,
    browser_open_active: AtomicBool,
    shutdown: background_process::CancellationToken,
    shutdown_started: AtomicBool,
    active_operations: Arc<ActiveOperations>,
    worker_status: Mutex<Option<(String, Result<LocalWorkerStatus, String>)>>,
    tray_menu: Mutex<Option<TrayMenuItems>>,
}

impl Default for DesktopState {
    fn default() -> Self {
        Self {
            ui_process: Mutex::new(None),
            api_process: Mutex::new(None),
            worker_stop: Arc::new(WorkerStopSupervisor::default()),
            operation_cancellation: Arc::new(Mutex::new(None)),
            transition: Arc::new(Mutex::new(TransitionState::default())),
            browser_open_active: AtomicBool::new(false),
            shutdown: background_process::CancellationToken::default(),
            shutdown_started: AtomicBool::new(false),
            active_operations: Arc::new(ActiveOperations::default()),
            worker_status: Mutex::new(None),
            tray_menu: Mutex::new(None),
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum DraftPhase {
    Draft,
    Applying,
    Committed,
    Cancelled,
}

#[derive(Clone, Debug)]
struct DraftRecord {
    draft: ManagedSetupDraft,
    phase: DraftPhase,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum TransitionKind {
    Revalidate,
    Adopt,
    Select,
    Delete,
    InstallMedia,
    InstallRuntime,
    ConfigureExternalInstallation,
    PrepareModels,
    RecoverActivation,
    OpenBrowser,
}

#[derive(Clone, Copy, Debug)]
struct ActiveTransition {
    id: u64,
    kind: TransitionKind,
}

#[derive(Default)]
struct TransitionState {
    next_id: u64,
    active: Option<ActiveTransition>,
    draft: Option<DraftRecord>,
}

fn transition_error(
    code: target_profiles::TargetErrorCode,
    message: impl Into<String>,
) -> target_profiles::TargetError {
    target_profiles::TargetError {
        code,
        message: message.into(),
    }
}

fn track_target_operation(
    state: &DesktopState,
) -> Result<ActiveOperationGuard, target_profiles::TargetError> {
    state.active_operations.register().map_err(|message| {
        transition_error(target_profiles::TargetErrorCode::OperationConflict, message)
    })
}

struct TransitionGuard {
    shared: Arc<Mutex<TransitionState>>,
    id: u64,
    applying_draft: Option<String>,
}

impl TransitionGuard {
    fn commit_draft(&mut self) {
        let Some(draft_id) = self.applying_draft.take() else {
            return;
        };
        if let Ok(mut transition) = self.shared.lock()
            && let Some(record) = transition.draft.as_mut()
            && record.draft.id == draft_id
            && record.phase == DraftPhase::Applying
        {
            record.phase = DraftPhase::Committed;
        }
    }
}

impl Drop for TransitionGuard {
    fn drop(&mut self) {
        let Ok(mut transition) = self.shared.lock() else {
            return;
        };
        if transition.active.is_some_and(|active| active.id == self.id) {
            transition.active = None;
        }
        if let Some(draft_id) = self.applying_draft.take()
            && let Some(record) = transition.draft.as_mut()
            && record.draft.id == draft_id
            && record.phase == DraftPhase::Applying
        {
            record.phase = DraftPhase::Draft;
        }
    }
}

struct TargetTransitionCoordinator;

impl TargetTransitionCoordinator {
    fn begin(
        state: &DesktopState,
        kind: TransitionKind,
    ) -> Result<TransitionGuard, target_profiles::TargetError> {
        let mut transition = state.transition.lock().map_err(|_| {
            transition_error(
                target_profiles::TargetErrorCode::StoreUnavailable,
                "The target transition coordinator is unavailable.",
            )
        })?;
        if let Some(active) = transition.active {
            return Err(transition_error(
                target_profiles::TargetErrorCode::OperationConflict,
                format!(
                    "Another target transition ({:?}) is already active.",
                    active.kind
                ),
            ));
        }
        transition.next_id = transition.next_id.wrapping_add(1).max(1);
        let id = transition.next_id;
        transition.active = Some(ActiveTransition { id, kind });
        Ok(TransitionGuard {
            shared: state.transition.clone(),
            id,
            applying_draft: None,
        })
    }

    fn begin_apply(
        state: &DesktopState,
        draft_id: &str,
        kind: TransitionKind,
    ) -> Result<TransitionGuard, target_profiles::TargetError> {
        let mut guard = Self::begin(state, kind)?;
        let mut transition = state.transition.lock().map_err(|_| {
            transition_error(
                target_profiles::TargetErrorCode::StoreUnavailable,
                "The managed setup draft is unavailable.",
            )
        })?;
        let record = transition.draft.as_mut().ok_or_else(|| {
            transition_error(
                target_profiles::TargetErrorCode::DraftMismatch,
                "This managed setup draft has expired.",
            )
        })?;
        if record.draft.id != draft_id {
            return Err(transition_error(
                target_profiles::TargetErrorCode::DraftMismatch,
                "A stale managed setup screen cannot modify the current draft.",
            ));
        }
        if record.phase != DraftPhase::Draft {
            return Err(transition_error(
                target_profiles::TargetErrorCode::DraftApplying,
                "This managed setup draft is already applying or has finished.",
            ));
        }
        record.phase = DraftPhase::Applying;
        guard.applying_draft = Some(draft_id.to_owned());
        Ok(guard)
    }

    fn begin_managed_draft(
        app: &AppHandle,
        state: &DesktopState,
    ) -> Result<ManagedSetupDraft, target_profiles::TargetError> {
        let mut transition = state.transition.lock().map_err(|_| {
            transition_error(
                target_profiles::TargetErrorCode::StoreUnavailable,
                "The managed setup draft could not be created.",
            )
        })?;
        if transition.active.is_some() {
            return Err(transition_error(
                target_profiles::TargetErrorCode::OperationConflict,
                "Another target transition is already active.",
            ));
        }
        if let Some(record) = &transition.draft
            && record.phase == DraftPhase::Draft
        {
            return Ok(record.draft.clone());
        }
        let current = target_profiles::current_state(app)?;
        let seed = format!(
            "{}:{}:{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map_err(|error| {
                    transition_error(
                        target_profiles::TargetErrorCode::ValidationRequired,
                        format!("The system clock is invalid: {error}"),
                    )
                })?
                .as_nanos(),
            current.selected_profile_id.as_deref().unwrap_or_default()
        );
        let draft = ManagedSetupDraft {
            id: hex::encode(Sha256::digest(seed.as_bytes())),
            previous_profile_id: current.selected_profile_id,
        };
        transition.draft = Some(DraftRecord {
            draft: draft.clone(),
            phase: DraftPhase::Draft,
        });
        Ok(draft)
    }

    fn cancel_managed_draft(
        app: &AppHandle,
        state: &DesktopState,
        draft_id: &str,
    ) -> Result<target_profiles::TargetState, target_profiles::TargetError> {
        Self::cancel_draft(state, draft_id)?;
        target_profiles::current_state(app)
    }

    fn cancel_draft(
        state: &DesktopState,
        draft_id: &str,
    ) -> Result<(), target_profiles::TargetError> {
        let mut transition = state.transition.lock().map_err(|_| {
            transition_error(
                target_profiles::TargetErrorCode::StoreUnavailable,
                "The managed setup draft could not be cancelled.",
            )
        })?;
        if transition.active.is_some() {
            return Err(transition_error(
                target_profiles::TargetErrorCode::DraftApplying,
                "Managed setup is applying and cannot be cancelled until it settles.",
            ));
        }
        let record = transition.draft.as_mut().ok_or_else(|| {
            transition_error(
                target_profiles::TargetErrorCode::DraftMismatch,
                "This managed setup draft has expired.",
            )
        })?;
        if record.draft.id != draft_id {
            return Err(transition_error(
                target_profiles::TargetErrorCode::DraftMismatch,
                "A stale managed setup screen cannot cancel the current draft.",
            ));
        }
        match record.phase {
            DraftPhase::Draft => {}
            DraftPhase::Applying => {
                return Err(transition_error(
                    target_profiles::TargetErrorCode::DraftApplying,
                    "Managed setup is applying and cannot be cancelled until it settles.",
                ));
            }
            DraftPhase::Committed | DraftPhase::Cancelled => {
                return Err(transition_error(
                    target_profiles::TargetErrorCode::DraftMismatch,
                    "This managed setup draft has already finished.",
                ));
            }
        }
        record.phase = DraftPhase::Cancelled;
        Ok(())
    }
}

struct OperationCancellationGuard {
    slot: Arc<Mutex<Option<background_process::CancellationToken>>>,
    token: background_process::CancellationToken,
    _active: ActiveOperationGuard,
}

impl OperationCancellationGuard {
    fn register(state: &DesktopState) -> Result<Self, String> {
        let token = background_process::CancellationToken::default();
        let active = state.active_operations.register()?;
        let mut slot = state
            .operation_cancellation
            .lock()
            .map_err(|_| "The setup cancellation supervisor is unavailable.".to_string())?;
        if state.shutdown.is_cancelled() {
            return Err("VidXP Desktop is shutting down.".into());
        }
        if slot.is_some() {
            return Err("Another cancellable Desktop operation is already active.".into());
        }
        *slot = Some(token.clone());
        drop(slot);
        Ok(Self {
            slot: state.operation_cancellation.clone(),
            token,
            _active: active,
        })
    }

    fn token(&self) -> background_process::CancellationToken {
        self.token.clone()
    }
}

impl Drop for OperationCancellationGuard {
    fn drop(&mut self) {
        if let Ok(mut active) = self.slot.lock()
            && active.as_ref().is_some_and(|token| token.same(&self.token))
        {
            active.take();
        }
    }
}

fn cancel_active_operation(state: &DesktopState) {
    if let Ok(active) = state.operation_cancellation.lock()
        && let Some(cancellation) = active.as_ref()
    {
        cancellation.cancel();
    }
}

fn manifest() -> Result<RuntimeManifest, String> {
    let manifest: RuntimeManifest = serde_json::from_slice(RUNTIME_MANIFEST_BYTES)
        .map_err(|error| format!("The embedded runtime manifest is invalid: {error}"))?;
    let actual = hex::encode(Sha256::digest(normalized_runtime_constraints().as_ref()));
    if actual != manifest.dependency_constraints_sha256 {
        return Err(format!(
            "The embedded runtime constraints have digest {actual}; expected {}.",
            manifest.dependency_constraints_sha256
        ));
    }
    Ok(manifest)
}

fn normalize_line_endings(bytes: &[u8]) -> Cow<'_, [u8]> {
    if !bytes.windows(2).any(|pair| pair == b"\r\n") {
        return Cow::Borrowed(bytes);
    }

    let mut normalized = Vec::with_capacity(bytes.len());
    let mut offset = 0;
    while offset < bytes.len() {
        if bytes[offset..].starts_with(b"\r\n") {
            normalized.push(b'\n');
            offset += 2;
        } else {
            normalized.push(bytes[offset]);
            offset += 1;
        }
    }
    Cow::Owned(normalized)
}

fn normalized_runtime_constraints() -> Cow<'static, [u8]> {
    normalize_line_endings(RUNTIME_CONSTRAINTS_BYTES)
}

fn manifest_digest() -> String {
    hex::encode(Sha256::digest(RUNTIME_MANIFEST_BYTES))
}

fn desktop_paths(app: &AppHandle) -> Result<DesktopPaths, String> {
    let private_data = app.path().app_local_data_dir().map_err(|error| {
        format!("Could not resolve the private application data directory: {error}")
    })?;
    let local_data = app.path().local_data_dir().map_err(|error| {
        format!("Could not resolve the operating-system data directory: {error}")
    })?;
    let cache = app
        .path()
        .app_cache_dir()
        .map_err(|error| format!("Could not resolve the application cache directory: {error}"))?;
    Ok(desktop_paths_from_roots(&private_data, &cache, &local_data))
}

fn desktop_paths_from_roots(private_data: &Path, cache: &Path, local_data: &Path) -> DesktopPaths {
    let data = local_data.join(PRODUCT_DATA_DIRECTORY_NAME);
    DesktopPaths {
        repository: data.join("repositories").join("default"),
        runtimes: private_data.join("runtimes"),
        python: private_data.join("python"),
        active_runtime: private_data.join("active-runtime.json"),
        activation_journal: private_data.join("activation-journal.json"),
        models: data.join("models"),
        private_data: private_data.to_path_buf(),
        data,
        cache: cache.to_path_buf(),
    }
}

fn move_legacy_shared_directory(source: &Path, destination: &Path) -> Result<bool, String> {
    if !source.exists() {
        return Ok(false);
    }
    if destination.exists() {
        log::warn!(
            "Leaving legacy VidXP data at {} because {} already exists",
            source.display(),
            destination.display()
        );
        return Ok(false);
    }
    let parent = destination
        .parent()
        .ok_or_else(|| format!("{} has no parent directory", destination.display()))?;
    fs::create_dir_all(parent)
        .map_err(|error| format!("Could not create {}: {error}", parent.display()))?;
    fs::rename(source, destination).map_err(|error| {
        format!(
            "Could not move legacy VidXP data from {} to {}: {error}",
            source.display(),
            destination.display()
        )
    })?;
    Ok(true)
}

fn migrate_legacy_shared_data(app: &AppHandle) -> Result<(), String> {
    let paths = desktop_paths(app)?;
    let legacy_models = paths.private_data.join("models");
    let legacy_repositories = paths.private_data.join("repositories");
    let shared_repositories = paths
        .repository
        .parent()
        .expect("the default repository always has a parent");
    let moved_models = move_legacy_shared_directory(&legacy_models, &paths.models)?;
    move_legacy_shared_directory(&legacy_repositories, shared_repositories)?;

    if moved_models && paths.active_runtime.exists() {
        let contents = fs::read(&paths.active_runtime).map_err(|error| {
            format!("Could not read the active runtime during migration: {error}")
        })?;
        let mut active: ActiveRuntime = serde_json::from_slice(&contents)
            .map_err(|error| format!("The active runtime pointer is invalid: {error}"))?;
        if active.model_directory == legacy_models {
            active.model_directory = paths.models.clone();
            write_active_runtime(&paths, &active)?;
        }
    }
    Ok(())
}

fn model_directory(paths: &DesktopPaths, requested: Option<&str>) -> Result<PathBuf, String> {
    let Some(requested) = requested.map(str::trim).filter(|value| !value.is_empty()) else {
        return Ok(paths.models.clone());
    };
    let directory = PathBuf::from(requested);
    if !directory.is_absolute() {
        return Err("The model directory must be an absolute path.".into());
    }
    if directory.is_file() {
        return Err("The selected model location is a file, not a directory.".into());
    }
    Ok(directory)
}

const MAX_MODEL_INVENTORY_ENTRIES: u64 = 100_000;

fn recognize_cached_model(relative: &Path) -> Option<CachedModelEntry> {
    static CATALOG: OnceLock<Vec<ModelCacheCatalogEntry>> = OnceLock::new();
    let catalog = CATALOG.get_or_init(|| {
        serde_json::from_slice(MODEL_CACHE_CATALOG_BYTES)
            .expect("the generated model cache catalog must be valid")
    });
    let path = relative
        .to_string_lossy()
        .replace('\\', "/")
        .to_ascii_lowercase();
    catalog
        .iter()
        .find(|entry| path.ends_with(&entry.relative_artifact.to_ascii_lowercase()))
        .map(|entry| CachedModelEntry {
            id: entry.id.clone(),
            label: entry.label.clone(),
        })
}

fn inventory_model_directory(directory: &Path) -> ModelDirectoryInventory {
    let resolved = fs::canonicalize(directory).unwrap_or_else(|_| directory.to_path_buf());
    let mut inventory = ModelDirectoryInventory {
        directory: resolved.to_string_lossy().into_owned(),
        exists: directory.exists(),
        readable: true,
        total_bytes: 0,
        file_count: 0,
        recognized_models: Vec::new(),
        empty: true,
        verification_required: false,
        truncated: false,
        detail: String::new(),
    };
    if !inventory.exists {
        inventory.detail = "No model directory exists yet; no cached models were found.".into();
        return inventory;
    }
    if !directory.is_dir() {
        inventory.readable = false;
        inventory.detail = "The selected model location is not a readable directory.".into();
        return inventory;
    }
    let root = match fs::read_dir(directory) {
        Ok(entries) => entries,
        Err(error) => {
            inventory.readable = false;
            inventory.detail = format!("The selected model directory could not be read: {error}");
            return inventory;
        }
    };
    let mut pending = vec![(directory.to_path_buf(), root)];
    let mut recognized = BTreeMap::<String, String>::new();
    let mut visited = 0_u64;
    while let Some((_parent, entries)) = pending.pop() {
        for entry in entries {
            visited += 1;
            if visited > MAX_MODEL_INVENTORY_ENTRIES {
                inventory.truncated = true;
                break;
            }
            let Ok(entry) = entry else {
                inventory.truncated = true;
                continue;
            };
            let Ok(file_type) = entry.file_type() else {
                inventory.truncated = true;
                continue;
            };
            if file_type.is_symlink() {
                continue;
            }
            let path = entry.path();
            if file_type.is_dir() {
                match fs::read_dir(&path) {
                    Ok(children) => pending.push((path, children)),
                    Err(_) => inventory.truncated = true,
                }
            } else if file_type.is_file() {
                inventory.file_count += 1;
                if let Ok(metadata) = entry.metadata() {
                    inventory.total_bytes = inventory.total_bytes.saturating_add(metadata.len());
                } else {
                    inventory.truncated = true;
                }
                if let Ok(relative) = path.strip_prefix(directory)
                    && let Some(model) = recognize_cached_model(relative)
                {
                    recognized.insert(model.id, model.label);
                }
            }
        }
        if inventory.truncated && visited > MAX_MODEL_INVENTORY_ENTRIES {
            break;
        }
    }
    inventory.recognized_models = recognized
        .into_iter()
        .map(|(id, label)| CachedModelEntry { id, label })
        .collect();
    inventory.empty = inventory.file_count == 0;
    inventory.verification_required = inventory.file_count > 0;
    inventory.detail = if inventory.empty {
        "No cached models were found.".into()
    } else if inventory.truncated {
        "Cached files were found. The bounded inventory is partial; preparation must verify required artifacts.".into()
    } else {
        "Cached files detected; verification required. VidXP will reuse valid cached files and download only missing material.".into()
    };
    inventory
}

fn selected_capabilities(
    manifest: &RuntimeManifest,
    requested: &[String],
) -> Result<Vec<String>, String> {
    let selected: BTreeSet<_> = requested.iter().cloned().collect();
    if selected.is_empty() {
        return Err("Select at least one capability.".into());
    }
    if let Some(unknown) = selected
        .iter()
        .find(|name| !manifest.capabilities.contains_key(*name))
    {
        return Err(format!("Unknown desktop capability: {unknown}"));
    }
    Ok(selected.into_iter().collect())
}

fn selected_surfaces(
    manifest: &RuntimeManifest,
    requested: &[String],
) -> Result<Vec<String>, String> {
    let selected: BTreeSet<_> = requested.iter().cloned().collect();
    if let Some(unknown) = selected
        .iter()
        .find(|name| !manifest.surfaces.contains_key(*name))
    {
        return Err(format!("Unknown desktop surface: {unknown}"));
    }
    Ok(selected.into_iter().collect())
}

fn package_specification(
    manifest: &RuntimeManifest,
    capabilities: &[String],
    surfaces: &[String],
) -> String {
    package_specification_for_version(manifest, capabilities, surfaces, &manifest.package_version)
}

fn package_specification_for_version(
    manifest: &RuntimeManifest,
    capabilities: &[String],
    surfaces: &[String],
    version: &str,
) -> String {
    let extras = package_extras(manifest, capabilities, surfaces);
    if extras.is_empty() {
        format!("{}=={}", manifest.package_name, version)
    } else {
        format!("{}[{}]=={}", manifest.package_name, extras, version)
    }
}

fn package_extras(
    manifest: &RuntimeManifest,
    capabilities: &[String],
    surfaces: &[String],
) -> String {
    let local_worker_selected = surfaces.iter().any(|name| name == "worker");
    let extras: BTreeSet<_> = manifest
        .surfaces
        .iter()
        .filter(|(name, _)| surfaces.contains(name))
        .map(|(_, surface)| surface.extra.clone())
        .chain(
            capabilities
                .iter()
                .filter(|_| !local_worker_selected)
                .map(|name| manifest.capabilities[name].extra.clone()),
        )
        .collect();
    extras.into_iter().collect::<Vec<_>>().join(",")
}

fn external_installation_arguments(
    manifest: &RuntimeManifest,
    capabilities: &[String],
    surfaces: &[String],
    python_version: &str,
    version: &str,
) -> Result<Vec<String>, String> {
    if version.is_empty()
        || !version
            .chars()
            .all(|character| character.is_ascii_alphanumeric() || ".!+_-".contains(character))
    {
        return Err("The selected installation reported an invalid package version.".into());
    }
    Ok(vec![
        "tool".into(),
        "install".into(),
        "--force".into(),
        "--python".into(),
        python_version.into(),
        "--no-config".into(),
        "--default-index".into(),
        manifest.dependency_index.clone(),
        "--index-strategy".into(),
        "first-index".into(),
        package_specification_for_version(manifest, capabilities, surfaces, version),
    ])
}

fn external_installation_version<'a>(
    manifest: &'a RuntimeManifest,
    runtime_update_required: bool,
    reported_protocol_version: u32,
    observed_package_version: &'a str,
) -> Result<&'a str, String> {
    if runtime_update_required {
        return Ok(&manifest.package_version);
    }
    match reported_protocol_version.cmp(&target_profiles::SUPPORTED_PROBE_PROTOCOL_VERSION) {
        std::cmp::Ordering::Less => Ok(&manifest.package_version),
        std::cmp::Ordering::Equal => Ok(observed_package_version),
        std::cmp::Ordering::Greater => Err(
            "This VidXP installation is newer than this Desktop version. Update VidXP Desktop before changing its features."
                .into(),
        ),
    }
}

fn base_package_specification(manifest: &RuntimeManifest) -> String {
    format!("{}=={}", manifest.package_name, manifest.package_version)
}

fn package_acquisition_arguments(
    manifest: &RuntimeManifest,
    python: &Path,
    wheel: &Path,
) -> Vec<String> {
    let wheel_directory = wheel.parent().unwrap_or_else(|| Path::new("."));
    vec![
        "pip".into(),
        "install".into(),
        "--python".into(),
        python.to_string_lossy().into_owned(),
        "--no-config".into(),
        "--no-deps".into(),
        "--no-index".into(),
        "--find-links".into(),
        wheel_directory.to_string_lossy().into_owned(),
        base_package_specification(manifest),
    ]
}

fn stage_runtime_package_wheel(runtime: &Path) -> Result<PathBuf, String> {
    let wheel_name = Path::new(RUNTIME_PACKAGE_WHEEL_NAME);
    if wheel_name.file_name().and_then(|name| name.to_str()) != Some(RUNTIME_PACKAGE_WHEEL_NAME) {
        return Err("The embedded runtime wheel name is invalid.".into());
    }
    let actual = hex::encode(Sha256::digest(RUNTIME_PACKAGE_WHEEL_BYTES));
    if actual != RUNTIME_PACKAGE_WHEEL_SHA256 {
        return Err(format!(
            "The embedded runtime wheel has digest {actual}; expected {RUNTIME_PACKAGE_WHEEL_SHA256}."
        ));
    }
    let wheel = runtime.join(wheel_name);
    fs::write(&wheel, RUNTIME_PACKAGE_WHEEL_BYTES)
        .map_err(|error| format!("Could not stage the embedded VidXP package: {error}"))?;
    Ok(wheel)
}

struct UvInvocation {
    arguments: Vec<String>,
    working_directory: PathBuf,
}

fn dependency_installation_invocation(
    manifest: &RuntimeManifest,
    capabilities: &[String],
    surfaces: &[String],
    python: &Path,
    constraints: &Path,
    cpu_torch: bool,
) -> Result<UvInvocation, String> {
    // uv 0.12 splits each --constraints value on spaces even when the operating system supplied
    // it as one argument. Keep macOS "Application Support" paths in the working directory and
    // pass only the staged file name.
    let working_directory = constraints.parent().ok_or_else(|| {
        "The staged runtime constraints path has no parent directory.".to_string()
    })?;
    let constraints_file_name = constraints
        .file_name()
        .ok_or_else(|| "The staged runtime constraints path has no file name.".to_string())?;
    let mut arguments = vec![
        "pip".into(),
        "install".into(),
        "--python".into(),
        python.to_string_lossy().into_owned(),
        "--no-config".into(),
        "--default-index".into(),
        manifest.dependency_index.clone(),
        "--index-strategy".into(),
        "first-index".into(),
        "--find-links".into(),
        ".".into(),
        "--constraints".into(),
        constraints_file_name.to_string_lossy().into_owned(),
    ];
    if cpu_torch {
        arguments.extend(["--torch-backend".into(), "cpu".into()]);
    }
    arguments.push(package_specification(manifest, capabilities, surfaces));
    Ok(UvInvocation {
        arguments,
        working_directory: working_directory.to_path_buf(),
    })
}

fn capability_command_arguments(
    manifest: &RuntimeManifest,
    operation: &str,
    capabilities: &[String],
) -> Vec<String> {
    let modalities = capabilities
        .iter()
        .map(|name| manifest.capabilities[name].modality.as_str())
        .collect::<Vec<_>>()
        .join(",");
    let mut arguments = vec![
        operation.into(),
        "--json".into(),
        "--modalities".into(),
        modalities,
    ];
    if operation == "prepare" {
        arguments.push("--yes".into());
    }
    arguments
}

fn executable(runtime: &Path, name: &str) -> PathBuf {
    if cfg!(windows) {
        runtime.join("Scripts").join(format!("{name}.exe"))
    } else {
        runtime.join("bin").join(name)
    }
}

fn executable_candidates(name: &str) -> Vec<PathBuf> {
    let requested = PathBuf::from(name);
    if requested.is_absolute() || requested.components().count() > 1 {
        return vec![requested];
    }
    let mut directories = env::var_os("PATH")
        .map(|value| env::split_paths(&value).collect::<Vec<_>>())
        .unwrap_or_default();
    if cfg!(windows)
        && let Some(local) = env::var_os("LOCALAPPDATA")
    {
        directories.push(
            PathBuf::from(local)
                .join("Microsoft")
                .join("WinGet")
                .join("Links"),
        );
    }
    if cfg!(target_os = "macos") {
        directories.extend([
            PathBuf::from("/opt/homebrew/bin"),
            PathBuf::from("/usr/local/bin"),
        ]);
    } else if !cfg!(windows) {
        directories.extend([
            PathBuf::from("/usr/local/bin"),
            PathBuf::from("/usr/bin"),
            PathBuf::from("/snap/bin"),
        ]);
    }
    directories
        .into_iter()
        .flat_map(|directory| {
            let plain = directory.join(name);
            if cfg!(windows) && plain.extension().is_none() {
                vec![plain.with_extension("exe"), plain]
            } else {
                vec![plain]
            }
        })
        .collect()
}

fn resolve_system_executable(name: &str) -> Option<PathBuf> {
    executable_candidates(name)
        .into_iter()
        .find(|candidate| candidate.is_file())
        .and_then(|candidate| fs::canonicalize(&candidate).ok().or(Some(candidate)))
}

fn combined_output(output: &background_process::BackgroundOutput) -> String {
    format!(
        "{}\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    )
}

fn system_install_plan() -> Option<SystemInstallPlan> {
    media_setup::system_install_plan(resolve_system_executable)
}

fn inspect_media_runtime() -> MediaRuntimeStatus {
    let ffmpeg = resolve_system_executable("ffmpeg");
    let ffprobe = resolve_system_executable("ffprobe");
    let mut errors = Vec::new();
    if let Some(path) = &ffmpeg {
        let mut version_command = Command::new(path);
        version_command.arg("-version");
        let version = checked_output(version_command, "FFmpeg version check");
        if let Err(error) = version {
            errors.push(error);
        } else {
            let mut encoder_command = Command::new(path);
            encoder_command.args(["-hide_banner", "-encoders"]);
            match checked_output(encoder_command, "FFmpeg encoder check") {
                Ok(output) => {
                    let encoders = combined_output(&output);
                    for required in ["libx264", "aac"] {
                        if required_encoder_missing(&encoders, required) {
                            errors.push(format!(
                                "FFmpeg is missing the required {required} encoder."
                            ));
                        }
                    }
                }
                Err(error) => errors.push(error),
            }
        }
    } else {
        errors.push("FFmpeg was not found.".into());
    }
    if let Some(path) = &ffprobe {
        let mut probe_command = Command::new(path);
        probe_command.arg("-version");
        if let Err(error) = checked_output(probe_command, "ffprobe version check") {
            errors.push(error);
        }
    } else {
        errors.push("ffprobe was not found.".into());
    }
    let plan = if errors.is_empty() {
        None
    } else {
        system_install_plan()
    };
    MediaRuntimeStatus {
        ready: errors.is_empty(),
        ffmpeg_executable: ffmpeg.map(|path| path.to_string_lossy().into_owned()),
        ffprobe_executable: ffprobe.map(|path| path.to_string_lossy().into_owned()),
        required_encoders: vec!["libx264".into(), "aac".into()],
        errors,
        package_manager: plan.as_ref().map(|plan| plan.manager.clone()),
        install_command: plan.as_ref().map(|plan| display_command(&plan.command)),
        automatic_install: plan.is_some_and(|plan| plan.automatic),
    }
}

fn verified_media_runtime() -> Result<VerifiedMediaRuntime, String> {
    let status = inspect_media_runtime();
    if !status.ready {
        return Err(format!(
            "{} Run the guided FFmpeg setup, then retry.",
            status.errors.join(" ")
        ));
    }
    Ok(VerifiedMediaRuntime {
        ffmpeg: PathBuf::from(
            status
                .ffmpeg_executable
                .ok_or("FFmpeg did not resolve to an absolute path.")?,
        ),
        ffprobe: PathBuf::from(
            status
                .ffprobe_executable
                .ok_or("ffprobe did not resolve to an absolute path.")?,
        ),
    })
}

fn clean_environment(paths: &DesktopPaths) -> Vec<(String, String)> {
    clean_environment_from(paths, std::env::vars())
}

fn clean_environment_from(
    paths: &DesktopPaths,
    source: impl IntoIterator<Item = (String, String)>,
) -> Vec<(String, String)> {
    let mut environment: Vec<_> = source
        .into_iter()
        .filter(|(key, _)| {
            let upper = key.to_ascii_uppercase();
            !upper.starts_with("VIDXP_")
                && !upper.starts_with("DBOS_")
                && !upper.starts_with("UV_")
                && !upper.starts_with("STREAMLIT_")
                && !upper.starts_with("PYTHON")
                && !upper.starts_with("PYENV_")
                && !upper.starts_with("PIP_")
                && !upper.starts_with("CONDA")
                && upper != "VIRTUAL_ENV"
                && upper != "VIRTUAL_ENV_PROMPT"
                && upper != "_OLD_VIRTUAL_PATH"
        })
        .collect();
    environment.extend([
        (
            "VIDXP_DATA_DIR".into(),
            paths.data.to_string_lossy().into_owned(),
        ),
        (
            "VIDXP_MODEL_CACHE".into(),
            paths.models.to_string_lossy().into_owned(),
        ),
        ("VIDXP_ALLOW_MODEL_DOWNLOADS".into(), "true".into()),
        ("STREAMLIT_SERVER_HEADLESS".into(), "true".into()),
        (
            "STREAMLIT_BROWSER_GATHER_USAGE_STATS".into(),
            "false".into(),
        ),
    ]);
    environment
}

fn configured_command(executable_path: &Path, paths: &DesktopPaths) -> Command {
    let mut command = Command::new(executable_path);
    command.env_clear();
    command.envs(clean_environment(paths));
    command
}

fn checked_output(
    command: Command,
    operation: &str,
) -> Result<background_process::BackgroundOutput, String> {
    let output = background_process::run(
        command,
        background_process::BackgroundPolicy {
            timeout: Duration::from_secs(30),
            max_output_bytes: 1024 * 1024,
        },
        None,
    )
    .map_err(|error| format!("{operation} failed: {}", error.detail))?;
    if output.status.success() {
        return Ok(output);
    }
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_owned();
    let stdout = String::from_utf8_lossy(&output.stdout).trim().to_owned();
    let detail = if stderr.is_empty() { stdout } else { stderr };
    Err(format!("{operation} failed: {detail}"))
}

fn active_runtime(paths: &DesktopPaths) -> Result<ActiveRuntime, String> {
    let contents = fs::read(&paths.active_runtime)
        .map_err(|_| "Local video processing has not been configured yet.".to_string())?;
    let active: ActiveRuntime = serde_json::from_slice(&contents)
        .map_err(|error| format!("The active runtime pointer is invalid: {error}"))?;
    validate_active_runtime_pointer(&active)?;
    Ok(active)
}

fn validate_active_runtime_pointer(active: &ActiveRuntime) -> Result<(), String> {
    if active.schema_version != 2 || active.manifest_sha256 != manifest_digest() {
        return Err("The desktop runtime needs to be installed for this app version.".into());
    }
    if !active
        .profile
        .chars()
        .all(|character| character.is_ascii_hexdigit() || character == '-')
    {
        return Err("The active runtime profile identity is invalid.".into());
    }
    Ok(())
}

fn runtime_directory(paths: &DesktopPaths, active: &ActiveRuntime) -> PathBuf {
    paths.runtimes.join(&active.profile)
}

fn write_active_runtime(paths: &DesktopPaths, active: &ActiveRuntime) -> Result<(), String> {
    let mut destination = AtomicWriteFile::options()
        .open(&paths.active_runtime)
        .map_err(|error| format!("Could not stage the active runtime pointer: {error}"))?;
    serde_json::to_writer(&mut destination, active)
        .map_err(|error| format!("Could not serialize the active runtime pointer: {error}"))?;
    destination
        .flush()
        .and_then(|_| destination.commit())
        .map_err(|error| format!("Could not activate the validated runtime: {error}"))
}

fn write_activation_journal(
    paths: &DesktopPaths,
    journal: &ActivationJournal,
) -> Result<(), String> {
    fs::create_dir_all(&paths.private_data)
        .map_err(|error| format!("Could not create the activation journal directory: {error}"))?;
    let mut destination = AtomicWriteFile::options()
        .open(&paths.activation_journal)
        .map_err(|error| format!("Could not stage the activation journal: {error}"))?;
    serde_json::to_writer(&mut destination, journal)
        .map_err(|error| format!("Could not serialize the activation journal: {error}"))?;
    destination
        .flush()
        .and_then(|_| destination.commit())
        .map_err(|error| format!("Could not persist the activation journal: {error}"))
}

fn read_active_runtime_snapshot(paths: &DesktopPaths) -> Result<Option<Vec<u8>>, String> {
    match fs::read(&paths.active_runtime) {
        Ok(bytes) => Ok(Some(bytes)),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(format!(
            "Could not snapshot the previous active runtime pointer: {error}"
        )),
    }
}

fn restore_active_runtime(paths: &DesktopPaths, previous: Option<&[u8]>) -> Result<(), String> {
    match previous {
        Some(bytes) => {
            let mut destination = AtomicWriteFile::options()
                .open(&paths.active_runtime)
                .map_err(|error| {
                    format!("Could not stage the previous runtime pointer: {error}")
                })?;
            destination.write_all(bytes).map_err(|error| {
                format!("Could not restore the previous runtime pointer: {error}")
            })?;
            destination
                .flush()
                .and_then(|_| destination.commit())
                .map_err(|error| format!("Could not restore the previous runtime pointer: {error}"))
        }
        None if paths.active_runtime.exists() => fs::remove_file(&paths.active_runtime)
            .map_err(|error| format!("Could not restore the empty runtime selection: {error}")),
        None => Ok(()),
    }
}

fn clear_activation_journal(paths: &DesktopPaths) -> Result<(), String> {
    if paths.activation_journal.exists() {
        fs::remove_file(&paths.activation_journal)
            .map_err(|error| format!("Could not clear the activation journal: {error}"))?;
    }
    Ok(())
}

fn managed_runtime_projection_for(
    paths: &DesktopPaths,
    active: &ActiveRuntime,
) -> target_profiles::ManagedRuntimeProjection {
    let runtime = runtime_directory(paths, active);
    let requested_executable = executable(&runtime, "vidxp");
    let executable = fs::canonicalize(&requested_executable).unwrap_or(requested_executable);
    target_profiles::ManagedRuntimeProjection {
        runtime_profile: active.profile.clone(),
        executable,
        data_root: paths.data.clone(),
        repository_root: paths.repository.clone(),
        model_directory: active.model_directory.clone(),
        package_version: active.package_version.clone(),
        capabilities: active.capabilities.clone(),
        surfaces: active.surfaces.clone(),
    }
}

fn resolved_path(path: &Path) -> PathBuf {
    fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf())
}

fn same_path(left: &Path, right: &Path) -> bool {
    let left = resolved_path(left);
    let right = resolved_path(right);
    #[cfg(windows)]
    {
        left.to_string_lossy()
            .eq_ignore_ascii_case(&right.to_string_lossy())
    }
    #[cfg(not(windows))]
    {
        left == right
    }
}

fn path_is_confined(path: &Path, root: &Path) -> bool {
    let path = resolved_path(path);
    let root = resolved_path(root);
    #[cfg(windows)]
    {
        let path = path.to_string_lossy().to_ascii_lowercase();
        let root = root.to_string_lossy().to_ascii_lowercase();
        path == root || path.starts_with(&format!("{root}\\"))
    }
    #[cfg(not(windows))]
    {
        path.starts_with(root)
    }
}

fn managed_probe_error(message: impl Into<String>) -> target_profiles::TargetError {
    target_profiles::TargetError {
        code: target_profiles::TargetErrorCode::InvalidDataRoot,
        message: message.into(),
    }
}

fn validate_managed_runtime_identity(
    runtime: &Path,
    launcher: &Path,
    identity: &target_profiles::RuntimeIdentity,
) -> Result<(), target_profiles::TargetError> {
    if !path_is_confined(launcher, runtime) {
        return Err(managed_probe_error(format!(
            "The managed launcher resolved outside the Desktop-owned runtime at {}.",
            runtime.display()
        )));
    }
    if !same_path(&identity.prefix, runtime) {
        return Err(managed_probe_error(format!(
            "The managed probe reported Python environment {}, but VidXP Desktop owns {}.",
            identity.prefix.display(),
            runtime.display()
        )));
    }
    // POSIX virtual environments commonly symlink their Python executable to a shared base
    // interpreter. The environment prefix, not that resolved interpreter target, establishes
    // which environment the managed launcher is running from.
    Ok(())
}

fn validate_managed_projection(
    paths: &DesktopPaths,
    projection: &target_profiles::ManagedRuntimeProjection,
    desktop_version: &str,
    cancellation: Option<&background_process::CancellationToken>,
) -> Result<target_profiles::ValidatedTarget, target_profiles::TargetError> {
    let runtime = paths.runtimes.join(&projection.runtime_profile);
    let validated = target_profiles::validate_executable_using(
        &projection.executable,
        desktop_version,
        cancellation,
        |executable| configured_command(executable, paths),
    )?;
    for (label, reported, authoritative) in [
        ("data", &validated.data_root, &projection.data_root),
        (
            "repository",
            &validated.repository_root,
            &projection.repository_root,
        ),
        ("model", &validated.model_root, &projection.model_directory),
    ] {
        if !same_path(reported, authoritative) {
            return Err(managed_probe_error(format!(
                "The managed probe reported {label} root {}, but VidXP Desktop owns {}.",
                reported.display(),
                authoritative.display()
            )));
        }
    }
    validate_managed_runtime_identity(&runtime, &validated.executable, &validated.runtime)?;
    Ok(validated)
}

fn managed_runtime_projection(
    paths: &DesktopPaths,
) -> Option<target_profiles::ManagedRuntimeProjection> {
    let contents = fs::read(&paths.active_runtime).ok()?;
    let active: ActiveRuntime = serde_json::from_slice(&contents).ok()?;
    if !active
        .profile
        .chars()
        .all(|character| character.is_ascii_hexdigit() || character == '-')
    {
        log::warn!("Ignoring an active managed runtime with an invalid profile identity");
        return None;
    }
    Some(managed_runtime_projection_for(paths, &active))
}

fn candidate_authorities_match(
    app: &AppHandle,
    paths: &DesktopPaths,
    journal: &ActivationJournal,
) -> bool {
    let active_matches = fs::read(&paths.active_runtime)
        .ok()
        .and_then(|bytes| serde_json::from_slice::<ActiveRuntime>(&bytes).ok())
        .is_some_and(|active| active == journal.candidate_active);
    let targets_match = target_profiles::current_state(app)
        .is_ok_and(|targets| targets == journal.candidate_targets);
    active_matches && targets_match
}

fn mark_journal_stage(
    paths: &DesktopPaths,
    journal: &mut ActivationJournal,
    stage: ActivationStage,
) -> Result<(), String> {
    journal.stage = stage;
    write_activation_journal(paths, journal)
}

fn finish_journal_cleanup(paths: &DesktopPaths, context: &str) {
    if let Err(error) = clear_activation_journal(paths) {
        log::warn!("{context}; activation journal cleanup will be retried: {error}");
    }
}

fn owned_runtime_directory_name(name: &str) -> bool {
    let mut parts = name.split('-');
    let Some(digest) = parts.next() else {
        return false;
    };
    let Some(timestamp) = parts.next() else {
        return false;
    };
    parts.next().is_none()
        && digest.len() == 64
        && digest
            .chars()
            .all(|character| character.is_ascii_hexdigit())
        && !timestamp.is_empty()
        && timestamp
            .chars()
            .all(|character| character.is_ascii_digit())
}

fn owned_staging_directory_name(name: &str) -> bool {
    let Some(remainder) = name.strip_prefix(".staging-") else {
        return false;
    };
    let mut parts = remainder.split('-');
    let (Some(digest), Some(timestamp), Some(pid)) = (parts.next(), parts.next(), parts.next())
    else {
        return false;
    };
    parts.next().is_none()
        && digest.len() == 64
        && digest
            .chars()
            .all(|character| character.is_ascii_hexdigit())
        && !timestamp.is_empty()
        && timestamp
            .chars()
            .all(|character| character.is_ascii_digit())
        && !pid.is_empty()
        && pid.chars().all(|character| character.is_ascii_digit())
}

fn directory_size(path: &Path) -> io::Result<u64> {
    let mut total = 0_u64;
    for entry in fs::read_dir(path)? {
        let entry = entry?;
        let metadata = entry.path().symlink_metadata()?;
        if metadata.file_type().is_symlink() {
            continue;
        }
        if metadata.is_dir() {
            total = total.saturating_add(directory_size(&entry.path())?);
        } else if metadata.is_file() {
            total = total.saturating_add(metadata.len());
        }
    }
    Ok(total)
}

fn reconcile_managed_runtime_storage(paths: &DesktopPaths) -> RuntimeReconciliation {
    let mut report = RuntimeReconciliation::default();
    let mut retained = BTreeSet::new();
    let preserve_unidentified_finalized = match fs::read(&paths.active_runtime) {
        Ok(contents) => match serde_json::from_slice::<ActiveRuntime>(&contents) {
            Ok(active) => {
                retained.insert(active.profile);
                false
            }
            Err(_) => true,
        },
        Err(error) => error.kind() != io::ErrorKind::NotFound,
    };
    if let Ok(contents) = fs::read(&paths.activation_journal)
        && let Ok(journal) = serde_json::from_slice::<ActivationJournal>(&contents)
    {
        retained.insert(journal.candidate_active.profile);
        if let Some(previous) = journal.previous_active_bytes
            && let Ok(active) = serde_json::from_slice::<ActiveRuntime>(&previous)
        {
            retained.insert(active.profile);
        }
    }
    let Ok(entries) = fs::read_dir(&paths.runtimes) else {
        return report;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        let name = entry.file_name().to_string_lossy().into_owned();
        let Ok(file_type) = entry.file_type() else {
            continue;
        };
        if !file_type.is_dir() || file_type.is_symlink() || retained.contains(&name) {
            continue;
        }
        let finalized = owned_runtime_directory_name(&name);
        if (!finalized && !owned_staging_directory_name(&name))
            || (finalized && preserve_unidentified_finalized)
        {
            continue;
        }
        if !path_is_confined(&path, &paths.runtimes) {
            report.failures.push(format!(
                "Refused to reconcile a runtime path outside {}: {}",
                paths.runtimes.display(),
                path.display()
            ));
            continue;
        }
        let bytes = directory_size(&path).unwrap_or_default();
        match fs::remove_dir_all(&path) {
            Ok(()) => {
                report.removed_directories += 1;
                report.reclaimed_bytes = report.reclaimed_bytes.saturating_add(bytes);
            }
            Err(error) => report.failures.push(format!(
                "Could not remove untracked Desktop runtime {}: {error}",
                path.display()
            )),
        }
    }
    report
}

fn log_runtime_reconciliation(paths: &DesktopPaths) {
    let report = reconcile_managed_runtime_storage(paths);
    if report.removed_directories > 0 {
        log::info!(
            "Reconciled {} obsolete managed runtime directories and reclaimed {} bytes",
            report.removed_directories,
            report.reclaimed_bytes
        );
    }
    for failure in report.failures {
        log::warn!("Managed runtime storage cleanup failed: {failure}");
    }
}

fn rollback_activation(
    app: &AppHandle,
    paths: &DesktopPaths,
    journal: &mut ActivationJournal,
    runtime: &Path,
    activation_error: &str,
) -> String {
    if let Err(error) = mark_journal_stage(paths, journal, ActivationStage::RollingBack) {
        return format!(
            "{activation_error}. Rollback could not be durably started: {error}. VidXP Desktop will recover the existing activation journal on its next start. The candidate runtime remains at {}.",
            runtime.display()
        );
    }
    let active_restore = restore_active_runtime(paths, journal.previous_active_bytes.as_deref());
    let target_restore = target_profiles::replace_state(app, journal.previous_targets.clone())
        .map_err(|error| error.to_string());
    if let (Err(active_error), Err(target_error)) = (&active_restore, &target_restore) {
        return format!(
            "{activation_error}. Rollback remains incomplete (runtime pointer: {active_error}; target profile: {target_error}); startup will retry it. The candidate runtime remains at {}.",
            runtime.display()
        );
    }
    if let Err(error) = active_restore {
        return format!(
            "{activation_error}. Target profiles were restored, but the runtime pointer could not be restored: {error}; startup will retry rollback. The candidate runtime remains at {}.",
            runtime.display()
        );
    }
    if let Err(error) = target_restore {
        return format!(
            "{activation_error}. The runtime pointer was restored, but target profiles could not be restored: {error}; startup will retry rollback. The candidate runtime remains at {}.",
            runtime.display()
        );
    }
    if let Err(error) = mark_journal_stage(paths, journal, ActivationStage::RolledBack) {
        log::warn!(
            "Managed activation rolled back, but its completion marker could not be written: {error}"
        );
    }
    finish_journal_cleanup(paths, "Rolled back a failed managed activation");
    format!(
        "{activation_error}. The candidate runtime was retained at {}, while the previous active runtime and target remain selected.",
        runtime.display()
    )
}

fn recover_interrupted_activation(app: &AppHandle, state: &DesktopState) -> Result<(), String> {
    let _transition = TargetTransitionCoordinator::begin(state, TransitionKind::RecoverActivation)
        .map_err(|error| error.to_string())?;
    let paths = desktop_paths(app)?;
    if !paths.activation_journal.exists() {
        return Ok(());
    }
    let contents = fs::read(&paths.activation_journal)
        .map_err(|error| format!("Could not read the activation journal: {error}"))?;
    let mut journal: ActivationJournal = serde_json::from_slice(&contents)
        .map_err(|error| format!("The activation journal is invalid: {error}"))?;
    if journal.schema_version != 2 {
        return Err(format!(
            "The activation journal uses unsupported schema version {}.",
            journal.schema_version
        ));
    }
    let authorities_match = candidate_authorities_match(app, &paths, &journal);
    match activation_recovery(&journal.stage, authorities_match) {
        ActivationRecovery::Complete => {
            write_active_runtime(&paths, &journal.candidate_active)?;
            target_profiles::replace_state(app, journal.candidate_targets.clone())
                .map_err(|error| error.to_string())?;
            mark_journal_stage(&paths, &mut journal, ActivationStage::Committed)?;
        }
        ActivationRecovery::RollBack => {
            mark_journal_stage(&paths, &mut journal, ActivationStage::RollingBack)?;
            restore_active_runtime(&paths, journal.previous_active_bytes.as_deref())?;
            target_profiles::replace_state(app, journal.previous_targets.clone())
                .map_err(|error| error.to_string())?;
            mark_journal_stage(&paths, &mut journal, ActivationStage::RolledBack)?;
        }
    }
    finish_journal_cleanup(&paths, "Recovered an interrupted managed activation");
    Ok(())
}

fn initialize_target_profiles(app: &AppHandle) -> Result<target_profiles::TargetState, String> {
    let manifest = manifest()?;
    let paths = desktop_paths(app)?;
    target_profiles::initialize(
        app,
        managed_runtime_projection(&paths),
        &manifest.desktop_version,
    )
    .map_err(|error| error.to_string())
}

async fn supervised_output(
    command: Command,
    cancellation: background_process::CancellationToken,
    operation: &str,
) -> Result<background_process::BackgroundOutput, String> {
    let output = background_process::run_async(
        command,
        background_process::BackgroundPolicy {
            timeout: Duration::from_secs(30 * 60),
            max_output_bytes: MAX_SETUP_OUTPUT_BYTES,
        },
        cancellation,
    )
    .await
    .map_err(|error| format!("{operation} failed: {}", error.detail))?;
    if output.status.success() {
        return Ok(output);
    }
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_owned();
    let stdout = String::from_utf8_lossy(&output.stdout).trim().to_owned();
    let detail = match (stdout.is_empty(), stderr.is_empty()) {
        (false, false) => format!("{stdout}\n\nAdditional diagnostics:\n{stderr}"),
        (false, true) => stdout,
        (true, false) => stderr,
        (true, true) => "The process did not return an error message.".into(),
    };
    Err(format!("{operation} failed ({}): {detail}", output.status))
}

fn watch_managed_model_progress(
    app: &AppHandle,
    draft_id: &str,
    progress_path: &Path,
    current: u8,
    total: u8,
    stop: &AtomicBool,
) {
    let mut last_contents = None;
    loop {
        if let Ok(contents) = fs::read(progress_path)
            && last_contents.as_deref() != Some(contents.as_slice())
            && let Ok(progress) = serde_json::from_slice(&contents)
        {
            emit_managed_model_progress(app, draft_id, current, total, &progress);
            last_contents = Some(contents);
        }
        if stop.load(Ordering::Acquire) {
            break;
        }
        thread::sleep(Duration::from_millis(100));
    }
}

async fn uv_output(
    app: &AppHandle,
    paths: &DesktopPaths,
    arguments: Vec<String>,
    working_directory: Option<&Path>,
    cancellation: background_process::CancellationToken,
    operation: &str,
) -> Result<(), String> {
    let mut command = app
        .shell()
        .sidecar("uv")
        .map_err(|error| format!("The bundled uv sidecar is unavailable: {error}"))?
        .args(arguments)
        .env_clear();
    if let Some(working_directory) = working_directory {
        command = command.current_dir(working_directory);
    }
    for (key, value) in clean_environment(paths) {
        command = command.env(key, value);
    }
    command = command
        .env("UV_CACHE_DIR", paths.cache.join("uv"))
        .env("UV_PYTHON_INSTALL_DIR", &paths.python)
        .env("UV_NO_CONFIG", "1")
        .env("UV_MANAGED_PYTHON", "1");
    let command: Command = command.into();
    supervised_output(command, cancellation, operation).await?;
    Ok(())
}

async fn uv_captured_output(
    app: &AppHandle,
    paths: &DesktopPaths,
    arguments: Vec<String>,
    cancellation: background_process::CancellationToken,
    operation: &str,
) -> Result<background_process::BackgroundOutput, String> {
    let mut command = app
        .shell()
        .sidecar("uv")
        .map_err(|error| format!("The bundled uv sidecar is unavailable: {error}"))?
        .args(arguments)
        .env_clear();
    for (key, value) in clean_environment(paths) {
        command = command.env(key, value);
    }
    command = command
        .env("UV_CACHE_DIR", paths.cache.join("uv"))
        .env("UV_PYTHON_INSTALL_DIR", &paths.python)
        .env("UV_NO_CONFIG", "1")
        .env("UV_MANAGED_PYTHON", "1");
    let command: Command = command.into();
    supervised_output(command, cancellation, operation).await
}

fn run_vidxp(
    runtime: &Path,
    paths: &DesktopPaths,
    arguments: &[String],
    operation: &str,
) -> Result<background_process::BackgroundOutput, String> {
    let mut command = configured_command(&executable(runtime, "vidxp"), paths);
    command
        .arg("--index-dir")
        .arg(&paths.repository)
        .args(arguments);
    checked_output(command, operation)
}

async fn run_vidxp_supervised(
    runtime: &Path,
    paths: &DesktopPaths,
    arguments: &[String],
    cancellation: background_process::CancellationToken,
    operation: &str,
) -> Result<(), String> {
    let mut command = configured_command(&executable(runtime, "vidxp"), paths);
    command
        .arg("--index-dir")
        .arg(&paths.repository)
        .args(arguments);
    supervised_output(command, cancellation, operation).await?;
    Ok(())
}

#[tauri::command]
fn runtime_manifest(state: tauri::State<'_, DesktopState>) -> Result<RuntimeManifest, String> {
    let _active = state.active_operations.register()?;
    manifest()
}

#[tauri::command]
fn target_state(
    app: AppHandle,
    state: tauri::State<'_, DesktopState>,
) -> Result<target_profiles::TargetState, target_profiles::TargetError> {
    let _active = track_target_operation(&state)?;
    target_profiles::current_state(&app)
}

#[tauri::command]
async fn refresh_target_state(
    app: AppHandle,
    state: tauri::State<'_, DesktopState>,
) -> Result<target_profiles::TargetState, target_profiles::TargetError> {
    let _active = track_target_operation(&state)?;
    let manifest = manifest().map_err(|error| target_profiles::TargetError {
        code: target_profiles::TargetErrorCode::ValidationRequired,
        message: error,
    })?;
    let desktop_version = manifest.desktop_version;
    let transition = TargetTransitionCoordinator::begin(&state, TransitionKind::Revalidate)?;
    let cancellation = state.shutdown.clone();
    let worker_app = app.clone();
    let result = tauri::async_runtime::spawn_blocking(move || {
        let _transition = transition;
        match target_profiles::selected_profile(&worker_app) {
            Ok(profile) => {
                if profile.kind == target_profiles::TargetKind::Managed {
                    let validation = (|| {
                        let paths = desktop_paths(&worker_app).map_err(|message| {
                            transition_error(
                                target_profiles::TargetErrorCode::ManagedRuntimeUnavailable,
                                message,
                            )
                        })?;
                        let active = active_runtime(&paths).map_err(|message| {
                            transition_error(
                                target_profiles::TargetErrorCode::ManagedRuntimeUnavailable,
                                message,
                            )
                        })?;
                        if profile.managed_runtime_profile.as_deref()
                            != Some(active.profile.as_str())
                        {
                            return Err(transition_error(
                                target_profiles::TargetErrorCode::ManagedRuntimeUnavailable,
                                "The selected managed target does not match the active Desktop runtime.",
                            ));
                        }
                        let projection = managed_runtime_projection_for(&paths, &active);
                        validate_managed_projection(
                            &paths,
                            &projection,
                            &desktop_version,
                            Some(&cancellation),
                        )
                    })();
                    let _ = target_profiles::persist_selected_validation(&worker_app, validation);
                } else {
                    let _ = target_profiles::validated_selected_profile_with_cancellation(
                        &worker_app,
                        &desktop_version,
                        Some(&cancellation),
                    );
                }
            }
            Err(error)
                if error.code == target_profiles::TargetErrorCode::SelectedProfileMissing => {}
            Err(error) => return Err(error),
        }
        target_profiles::current_state(&worker_app)
    })
    .await
    .map_err(|error| target_profiles::TargetError {
        code: target_profiles::TargetErrorCode::ValidationRequired,
        message: format!("Target revalidation stopped unexpectedly: {error}"),
    })??;
    refresh_tray_for_selected_target(&app);
    Ok(result)
}

#[tauri::command]
async fn discover_local_targets(
    state: tauri::State<'_, DesktopState>,
) -> Result<Vec<target_profiles::DiscoveredTarget>, String> {
    let _active = state.active_operations.register()?;
    tauri::async_runtime::spawn_blocking(target_profiles::discover_local_targets)
        .await
        .map_err(|error| format!("Target discovery stopped unexpectedly: {error}"))
}

#[tauri::command]
fn choose_local_executable(
    app: AppHandle,
    state: tauri::State<'_, DesktopState>,
) -> Result<Option<String>, String> {
    let _active = state.active_operations.register()?;
    app.dialog()
        .file()
        .set_title("Choose an existing VidXP executable")
        .blocking_pick_file()
        .map(|path| {
            path.into_path()
                .map(|path| path.to_string_lossy().into_owned())
                .map_err(|error| format!("The selected executable path is invalid: {error}"))
        })
        .transpose()
}

#[tauri::command]
async fn inspect_local_target(
    state: tauri::State<'_, DesktopState>,
    executable: String,
) -> Result<target_profiles::TargetInspection, target_profiles::TargetError> {
    let _active = track_target_operation(&state)?;
    let manifest = manifest().map_err(|error| target_profiles::TargetError {
        code: target_profiles::TargetErrorCode::ValidationRequired,
        message: error,
    })?;
    let desktop_version = manifest.desktop_version;
    let cancellation = state.shutdown.clone();
    tauri::async_runtime::spawn_blocking(move || {
        target_profiles::inspect_executable_with_cancellation(
            Path::new(&executable),
            &desktop_version,
            &cancellation,
        )
    })
    .await
    .map_err(|error| {
        transition_error(
            target_profiles::TargetErrorCode::ValidationRequired,
            format!("Target inspection stopped unexpectedly: {error}"),
        )
    })?
}

#[tauri::command]
async fn adopt_local_target(
    app: AppHandle,
    state: tauri::State<'_, DesktopState>,
    executable: String,
    display_name: Option<String>,
) -> Result<target_profiles::TargetState, target_profiles::TargetError> {
    let _active = track_target_operation(&state)?;
    let manifest = manifest().map_err(|error| target_profiles::TargetError {
        code: target_profiles::TargetErrorCode::ValidationRequired,
        message: error,
    })?;
    let transition = TargetTransitionCoordinator::begin(&state, TransitionKind::Adopt)?;
    let desktop_version = manifest.desktop_version;
    let cancellation = state.shutdown.clone();
    let worker_app = app.clone();
    let result = tauri::async_runtime::spawn_blocking(move || {
        let _transition = transition;
        let canonical =
            fs::canonicalize(Path::new(&executable)).unwrap_or_else(|_| PathBuf::from(&executable));
        let validated = target_profiles::validate_executable_using(
            &canonical,
            &desktop_version,
            Some(&cancellation),
            |path| Command::new(path),
        )?;
        let setup = target_profiles::adopt_validated(&worker_app, validated, display_name)?;
        stop_ui_process(&worker_app.state::<DesktopState>());
        stop_api_process(&worker_app.state::<DesktopState>());
        Ok(setup)
    })
    .await
    .map_err(|error| {
        transition_error(
            target_profiles::TargetErrorCode::ValidationRequired,
            format!("Target adoption stopped unexpectedly: {error}"),
        )
    })??;
    refresh_tray_for_selected_target(&app);
    Ok(result)
}

#[tauri::command]
async fn select_target_profile(
    app: AppHandle,
    state: tauri::State<'_, DesktopState>,
    profile_id: String,
) -> Result<target_profiles::TargetState, target_profiles::TargetError> {
    let _active = track_target_operation(&state)?;
    let manifest = manifest().map_err(|error| target_profiles::TargetError {
        code: target_profiles::TargetErrorCode::ValidationRequired,
        message: error,
    })?;
    let transition = TargetTransitionCoordinator::begin(&state, TransitionKind::Select)?;
    let desktop_version = manifest.desktop_version;
    let cancellation = state.shutdown.clone();
    let worker_app = app.clone();
    let result = tauri::async_runtime::spawn_blocking(move || {
        let _transition = transition;
        let candidate = target_profiles::current_state(&worker_app)?
            .profiles
            .into_iter()
            .find(|profile| profile.id == profile_id)
            .ok_or_else(|| {
                transition_error(
                    target_profiles::TargetErrorCode::ProfileNotFound,
                    "The selected VidXP target no longer exists.",
                )
            })?;
        let setup = if candidate.kind == target_profiles::TargetKind::Managed {
            let paths = desktop_paths(&worker_app).map_err(|message| {
                transition_error(
                    target_profiles::TargetErrorCode::ManagedRuntimeUnavailable,
                    message,
                )
            })?;
            let active = active_runtime(&paths).map_err(|message| {
                transition_error(
                    target_profiles::TargetErrorCode::ManagedRuntimeUnavailable,
                    message,
                )
            })?;
            if candidate.managed_runtime_profile.as_deref() != Some(active.profile.as_str()) {
                return Err(transition_error(
                    target_profiles::TargetErrorCode::ManagedRuntimeUnavailable,
                    "The selected managed target does not match the active Desktop runtime.",
                ));
            }
            let projection = managed_runtime_projection_for(&paths, &active);
            let validated = validate_managed_projection(
                &paths,
                &projection,
                &desktop_version,
                Some(&cancellation),
            )?;
            target_profiles::select_validated_profile(&worker_app, &profile_id, validated)?
        } else {
            target_profiles::select_profile(&worker_app, &profile_id, &desktop_version)?
        };
        stop_ui_process(&worker_app.state::<DesktopState>());
        stop_api_process(&worker_app.state::<DesktopState>());
        Ok(setup)
    })
    .await
    .map_err(|error| {
        transition_error(
            target_profiles::TargetErrorCode::ValidationRequired,
            format!("Target selection stopped unexpectedly: {error}"),
        )
    })??;
    refresh_tray_for_selected_target(&app);
    Ok(result)
}

#[tauri::command]
async fn delete_target_profile(
    app: AppHandle,
    state: tauri::State<'_, DesktopState>,
    profile_id: String,
) -> Result<target_profiles::TargetState, target_profiles::TargetError> {
    let _active = track_target_operation(&state)?;
    let transition = TargetTransitionCoordinator::begin(&state, TransitionKind::Delete)?;
    let worker_app = app.clone();
    let result = tauri::async_runtime::spawn_blocking(move || {
        let _transition = transition;
        let selected = target_profiles::current_state(&worker_app)?.selected_profile_id;
        let result = target_profiles::delete_profile(&worker_app, &profile_id)?;
        if selected.as_deref() == Some(&profile_id) {
            stop_ui_process(&worker_app.state::<DesktopState>());
            stop_api_process(&worker_app.state::<DesktopState>());
        }
        Ok(result)
    })
    .await
    .map_err(|error| {
        transition_error(
            target_profiles::TargetErrorCode::ValidationRequired,
            format!("Target deletion stopped unexpectedly: {error}"),
        )
    })??;
    refresh_tray_for_selected_target(&app);
    Ok(result)
}

#[tauri::command]
async fn confirm_forget_target(
    app: AppHandle,
    state: tauri::State<'_, DesktopState>,
    display_name: String,
) -> Result<bool, String> {
    let _active = state.active_operations.register()?;
    tauri::async_runtime::spawn_blocking(move || {
        app.dialog()
            .message(format!(
                "Forget “{display_name}” from VidXP Desktop? The installation itself will not be changed."
            ))
            .title("Forget saved target?")
            .kind(MessageDialogKind::Warning)
            .buttons(MessageDialogButtons::OkCancel)
            .blocking_show()
    })
    .await
    .map_err(|error| format!("The confirmation dialog stopped unexpectedly: {error}"))
}

#[tauri::command]
async fn begin_managed_setup(
    app: AppHandle,
    state: tauri::State<'_, DesktopState>,
) -> Result<ManagedSetupDraft, target_profiles::TargetError> {
    let _active = track_target_operation(&state)?;
    let transition = state.transition.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let state = app.state::<DesktopState>();
        debug_assert!(Arc::ptr_eq(&transition, &state.transition));
        TargetTransitionCoordinator::begin_managed_draft(&app, &state)
    })
    .await
    .map_err(|error| {
        transition_error(
            target_profiles::TargetErrorCode::ValidationRequired,
            format!("Managed setup could not start: {error}"),
        )
    })?
}

#[tauri::command]
async fn cancel_managed_setup(
    app: AppHandle,
    state: tauri::State<'_, DesktopState>,
    draft_id: String,
) -> Result<target_profiles::TargetState, target_profiles::TargetError> {
    let _active = track_target_operation(&state)?;
    let transition = state.transition.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let state = app.state::<DesktopState>();
        debug_assert!(Arc::ptr_eq(&transition, &state.transition));
        TargetTransitionCoordinator::cancel_managed_draft(&app, &state, &draft_id)
    })
    .await
    .map_err(|error| {
        transition_error(
            target_profiles::TargetErrorCode::ValidationRequired,
            format!("Managed setup cancellation stopped unexpectedly: {error}"),
        )
    })?
}

#[tauri::command]
fn choose_model_directory(
    app: AppHandle,
    state: tauri::State<'_, DesktopState>,
) -> Result<Option<String>, String> {
    let _active = state.active_operations.register()?;
    let selection = app
        .dialog()
        .file()
        .set_title("Choose where VidXP stores model files")
        .blocking_pick_folder();
    selection
        .map(|path| {
            path.into_path()
                .map(|path| path.to_string_lossy().into_owned())
                .map_err(|error| format!("The selected model directory is invalid: {error}"))
        })
        .transpose()
}

#[tauri::command]
async fn install_media_runtime(
    app: AppHandle,
    state: tauri::State<'_, DesktopState>,
    draft_id: String,
) -> Result<MediaRuntimeStatus, String> {
    let cancellation = OperationCancellationGuard::register(&state)?;
    let _transition =
        TargetTransitionCoordinator::begin_apply(&state, &draft_id, TransitionKind::InstallMedia)
            .map_err(|error| error.to_string())?;
    let current = tauri::async_runtime::spawn_blocking(inspect_media_runtime)
        .await
        .map_err(|error| format!("Media runtime inspection stopped unexpectedly: {error}"))?;
    if current.ready {
        return Ok(current);
    }
    let plan = system_install_plan().ok_or_else(|| {
        if cfg!(target_os = "macos") {
            "FFmpeg is required. Install Homebrew from https://brew.sh and run `brew install ffmpeg`, or install FFmpeg and ffprobe manually on PATH, then retry.".to_string()
        } else {
            "No supported system package manager was found. Install FFmpeg and ffprobe on PATH, then retry.".to_string()
        }
    })?;
    if !plan.automatic {
        let instruction = format!(
            "VidXP needs FFmpeg and ffprobe.\n\nRun this command in a terminal, then return to VidXP:\n\n{}",
            display_command(&plan.command)
        );
        app.dialog()
            .message(&instruction)
            .title("FFmpeg is required")
            .kind(MessageDialogKind::Warning)
            .blocking_show();
        return Err(instruction);
    }
    let approved = app
        .dialog()
        .message(format!(
            "VidXP needs FFmpeg and ffprobe for video processing.\n\nInstall them with {}?\n\n{}",
            plan.manager,
            display_command(&plan.command)
        ))
        .title("Install FFmpeg")
        .kind(MessageDialogKind::Info)
        .buttons(MessageDialogButtons::OkCancelCustom(
            "Install".into(),
            "Not now".into(),
        ))
        .blocking_show();
    if !approved {
        return Err("FFmpeg setup was deferred.".into());
    }
    let command = app
        .shell()
        .command(plan.command[0].clone())
        .args(&plan.command[1..]);
    let command: Command = command.into();
    supervised_output(
        command,
        cancellation.token(),
        &format!("{} FFmpeg installation", plan.manager),
    )
    .await?;
    let status = tauri::async_runtime::spawn_blocking(inspect_media_runtime)
        .await
        .map_err(|error| format!("Media runtime verification stopped unexpectedly: {error}"))?;
    if !status.ready {
        return Err(format!(
            "FFmpeg installation finished but verification failed: {}",
            status.errors.join(" ")
        ));
    }
    Ok(status)
}

#[tauri::command]
async fn runtime_status(
    app: AppHandle,
    state: tauri::State<'_, DesktopState>,
) -> Result<RuntimeStatus, String> {
    let _active = state.active_operations.register()?;
    tauri::async_runtime::spawn_blocking(move || runtime_status_sync(&app))
        .await
        .map_err(|error| format!("Managed runtime inspection stopped unexpectedly: {error}"))?
}

fn runtime_status_sync(app: &AppHandle) -> Result<RuntimeStatus, String> {
    let manifest = manifest()?;
    let mut paths = desktop_paths(app)?;
    let default_model_directory = paths.models.to_string_lossy().into_owned();
    if !paths.active_runtime.exists() {
        return Ok(RuntimeStatus {
            state: RuntimeState::NeverConfigured,
            ready: false,
            runtime_profile: None,
            package_version: manifest.package_version,
            capabilities: Vec::new(),
            surfaces: Vec::new(),
            model_directory: default_model_directory,
            detail: "No Desktop-managed runtime has been created yet.".into(),
        });
    }
    let contents = fs::read(&paths.active_runtime)
        .map_err(|error| format!("Could not read the active runtime pointer: {error}"))?;
    let active: ActiveRuntime = match serde_json::from_slice(&contents) {
        Ok(active) => active,
        Err(error) => {
            return Ok(RuntimeStatus {
                state: RuntimeState::Broken,
                ready: false,
                runtime_profile: None,
                package_version: manifest.package_version,
                capabilities: Vec::new(),
                surfaces: Vec::new(),
                model_directory: default_model_directory,
                detail: format!("The active runtime pointer is invalid: {error}"),
            });
        }
    };
    paths.models = active.model_directory.clone();
    let runtime = runtime_directory(&paths, &active);
    let mut problems = Vec::new();
    if let Err(error) = validate_active_runtime_pointer(&active) {
        problems.push(error);
    }
    if let Err(error) = verified_media_runtime() {
        problems.push(error);
    }
    let version = run_vidxp(
        &runtime,
        &paths,
        &["--version".into()],
        "VidXP runtime validation",
    )
    .and_then(|output| {
        let actual = String::from_utf8_lossy(&output.stdout).trim().to_owned();
        let expected = format!("VidXP {}", manifest.package_version);
        if actual == expected {
            Ok(output)
        } else {
            Err(format!(
                "The active runtime reported {actual:?}; expected {expected:?}."
            ))
        }
    });
    if let Err(error) = version {
        problems.push(error);
    }
    Ok(configured_runtime_status(active, problems))
}

fn configured_runtime_status(active: ActiveRuntime, problems: Vec<String>) -> RuntimeStatus {
    let ready = problems.is_empty();
    RuntimeStatus {
        state: if ready {
            RuntimeState::Ready
        } else {
            RuntimeState::Broken
        },
        ready,
        runtime_profile: Some(active.profile.clone()),
        package_version: active.package_version,
        capabilities: active.capabilities,
        surfaces: active.surfaces,
        model_directory: active.model_directory.to_string_lossy().into_owned(),
        detail: if ready {
            "Local video processing is ready.".into()
        } else {
            problems.join(" ")
        },
    }
}

#[tauri::command]
async fn model_directory_inventory(
    app: AppHandle,
    state: tauri::State<'_, DesktopState>,
    directory: Option<String>,
) -> Result<ModelDirectoryInventory, String> {
    let _active = state.active_operations.register()?;
    tauri::async_runtime::spawn_blocking(move || {
        let paths = desktop_paths(&app)?;
        let selected = model_directory(&paths, directory.as_deref())?;
        Ok(inventory_model_directory(&selected))
    })
    .await
    .map_err(|error| format!("Model inventory stopped unexpectedly: {error}"))?
}

#[tauri::command]
async fn prepare_managed_models(
    app: AppHandle,
    state: tauri::State<'_, DesktopState>,
    draft_id: String,
) -> Result<target_profiles::TargetState, String> {
    let cancellation = OperationCancellationGuard::register(&state)?;
    let _transition =
        TargetTransitionCoordinator::begin_apply(&state, &draft_id, TransitionKind::PrepareModels)
            .map_err(|error| error.to_string())?;
    let preparation_app = app.clone();
    let (runtime, paths, capabilities) = tauri::async_runtime::spawn_blocking(move || {
        let profile = target_profiles::selected_profile(&preparation_app)
            .map_err(|error| error.to_string())?;
        let mut paths = desktop_paths(&preparation_app)?;
        let active = active_runtime(&paths)?;
        target_profiles::authorize_managed_runtime_action(&profile, &active.profile)
            .map_err(|error| error.to_string())?;
        paths.models = active.model_directory.clone();
        let runtime = runtime_directory(&paths, &active);
        Ok::<_, String>((runtime, paths, active.capabilities))
    })
    .await
    .map_err(|error| format!("Model preparation setup stopped unexpectedly: {error}"))??;

    let manifest = manifest()?;
    let arguments = capability_command_arguments(&manifest, "prepare", &capabilities);
    let mut worker = state.worker_stop.register(runtime.clone(), paths.clone())?;
    let preparation = run_vidxp_supervised(
        &runtime,
        &paths,
        &arguments,
        cancellation.token(),
        "VidXP model preparation",
    )
    .await;
    worker.stop_before(Instant::now() + Duration::from_secs(5));
    preparation?;
    target_profiles::current_state(&app).map_err(|error| error.to_string())
}

#[tauri::command]
async fn install_runtime(
    app: AppHandle,
    state: tauri::State<'_, DesktopState>,
    request: InstallRequest,
) -> Result<InstallTransitionResult, String> {
    let cancellation = OperationCancellationGuard::register(&state)?;
    let mut transition = TargetTransitionCoordinator::begin_apply(
        &state,
        &request.draft_id,
        TransitionKind::InstallRuntime,
    )
    .map_err(|error| error.to_string())?;
    let manifest = manifest()?;
    let capabilities = selected_capabilities(&manifest, &request.capabilities)?;
    let surfaces = selected_surfaces(&manifest, &request.surfaces)?;
    let requested_model_directory = request.model_directory.clone();
    let preparation_app = app.clone();
    let (media_runtime, paths) = tauri::async_runtime::spawn_blocking(move || {
        let media_runtime = verified_media_runtime()?;
        let mut paths = desktop_paths(&preparation_app)?;
        paths.models = model_directory(&paths, requested_model_directory.as_deref())?;
        for directory in [
            &paths.data,
            &paths.cache,
            &paths.repository,
            &paths.runtimes,
            &paths.python,
            &paths.models,
        ] {
            fs::create_dir_all(directory)
                .map_err(|error| format!("Could not create {}: {error}", directory.display()))?;
        }
        Ok::<_, String>((media_runtime, paths))
    })
    .await
    .map_err(|error| format!("Managed runtime preparation stopped unexpectedly: {error}"))??;

    let profile_seed = format!(
        "{}:{}:{}:{}:{}",
        manifest_digest(),
        std::env::consts::OS,
        std::env::consts::ARCH,
        capabilities.join(","),
        surfaces.join(",")
    );
    let profile_hash = hex::encode(Sha256::digest(profile_seed.as_bytes()));
    let timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| format!("The system clock is invalid: {error}"))?
        .as_nanos();
    let profile = format!("{profile_hash}-{timestamp}");
    let runtime = paths.runtimes.join(&profile);
    let constraints = runtime.join(RUNTIME_CONSTRAINTS_FILE_NAME);
    let progress_total = if request.prepare_models { 8 } else { 7 };

    let install_result = async {
        emit_managed_setup_progress(
            &app,
            &request.draft_id,
            2,
            progress_total,
            "python",
            "Preparing an isolated Python runtime",
        );
        uv_output(
            &app,
            &paths,
            vec![
                "venv".into(),
                runtime.to_string_lossy().into_owned(),
                "--python".into(),
                manifest.python_version.clone(),
                "--managed-python".into(),
                "--no-config".into(),
            ],
            None,
            cancellation.token(),
            "Managed Python setup",
        )
        .await?;

        let constraints_path = constraints.clone();
        let wheel_runtime = runtime.clone();
        let runtime_wheel = tauri::async_runtime::spawn_blocking(move || {
            fs::write(&constraints_path, normalized_runtime_constraints().as_ref())
                .map_err(|error| format!("Could not write runtime constraints: {error}"))?;
            stage_runtime_package_wheel(&wheel_runtime)
        })
        .await
        .map_err(|error| format!("Runtime constraint staging stopped unexpectedly: {error}"))??;

        emit_managed_setup_progress(
            &app,
            &request.draft_id,
            3,
            progress_total,
            "package",
            "Acquiring the VidXP package",
        );
        uv_output(
            &app,
            &paths,
            package_acquisition_arguments(
                &manifest,
                &executable(&runtime, "python"),
                &runtime_wheel,
            ),
            None,
            cancellation.token(),
            "VidXP package acquisition",
        )
        .await?;

        let dependency_installation = dependency_installation_invocation(
            &manifest,
            &capabilities,
            &surfaces,
            &executable(&runtime, "python"),
            &constraints,
            !cfg!(target_os = "macos"),
        )?;
        emit_managed_setup_progress(
            &app,
            &request.draft_id,
            4,
            progress_total,
            "dependencies",
            "Installing the selected search features",
        );
        uv_output(
            &app,
            &paths,
            dependency_installation.arguments,
            Some(&dependency_installation.working_directory),
            cancellation.token(),
            "VidXP package installation",
        )
        .await?;
        if let Err(error) = fs::remove_file(&runtime_wheel) {
            log::warn!(
                "Installed the embedded VidXP package, but could not remove its staged wheel: {error}"
            );
        }

        emit_managed_setup_progress(
            &app,
            &request.draft_id,
            5,
            progress_total,
            "media",
            "Configuring FFmpeg and video codecs",
        );
        run_vidxp_supervised(
            &runtime,
            &paths,
            &[
                "init".into(),
                "--json".into(),
                "--ffmpeg".into(),
                media_runtime.ffmpeg.to_string_lossy().into_owned(),
                "--ffprobe".into(),
                media_runtime.ffprobe.to_string_lossy().into_owned(),
            ],
            cancellation.token(),
            "FFmpeg configuration",
        )
        .await?;

        emit_managed_setup_progress(
            &app,
            &request.draft_id,
            6,
            progress_total,
            "validation",
            "Validating installed packages and video tools",
        );
        let mut doctor_arguments = capability_command_arguments(&manifest, "doctor", &capabilities);
        doctor_arguments.push("--no-models".into());
        run_vidxp_supervised(
            &runtime,
            &paths,
            &doctor_arguments,
            cancellation.token(),
            "VidXP dependency validation",
        )
        .await?;

        if request.prepare_models {
            emit_managed_setup_progress(
                &app,
                &request.draft_id,
                7,
                progress_total,
                "models",
                "Verifying and downloading selected model files",
            );
            let progress_path = runtime.join(".managed-model-progress.json");
            let mut prepare_arguments =
                capability_command_arguments(&manifest, "prepare", &capabilities);
            prepare_arguments.push("--progress-file".into());
            prepare_arguments.push(progress_path.to_string_lossy().into_owned());
            let mut worker = state.worker_stop.register(runtime.clone(), paths.clone())?;
            let preparation_app = app.clone();
            let preparation_draft_id = request.draft_id.clone();
            let monitor_stop = Arc::new(AtomicBool::new(false));
            let monitor_stop_worker = monitor_stop.clone();
            let progress_path_worker = progress_path.clone();
            let progress_monitor = thread::spawn(move || {
                watch_managed_model_progress(
                    &preparation_app,
                    &preparation_draft_id,
                    &progress_path_worker,
                    7,
                    progress_total,
                    &monitor_stop_worker,
                );
            });
            let preparation = run_vidxp_supervised(
                &runtime,
                &paths,
                &prepare_arguments,
                cancellation.token(),
                "VidXP model preparation",
            )
            .await;
            monitor_stop.store(true, Ordering::Release);
            let monitor_result = progress_monitor.join();
            let _ = fs::remove_file(&progress_path);
            worker.stop_before(Instant::now() + Duration::from_secs(5));
            preparation?;
            monitor_result
                .map_err(|_| "VidXP model progress stopped unexpectedly".to_owned())?;
        }

        Ok::<(), String>(())
    }
    .await;
    if let Err(error) = install_result {
        let failed_runtime = runtime.clone();
        let cleanup_error = tauri::async_runtime::spawn_blocking(move || {
            if failed_runtime.exists() {
                fs::remove_dir_all(&failed_runtime).err()
            } else {
                None
            }
        })
        .await
        .map_err(|join| {
            format!("{error}. Candidate-runtime cleanup stopped unexpectedly: {join}")
        })?;
        return Err(match cleanup_error {
            Some(cleanup_error) => format!(
                "{error}. The previous active runtime was not changed. VidXP could not remove the failed candidate runtime at {}: {cleanup_error}",
                runtime.display()
            ),
            None => format!(
                "{error}. The previous active runtime was not changed, and the failed candidate runtime was removed."
            ),
        });
    }

    emit_managed_setup_progress(
        &app,
        &request.draft_id,
        progress_total,
        progress_total,
        "activation",
        "Activating VidXP and cleaning up installation files",
    );
    let active = ActiveRuntime {
        schema_version: 2,
        manifest_sha256: manifest_digest(),
        profile,
        package_version: manifest.package_version.clone(),
        capabilities: capabilities.clone(),
        surfaces: surfaces.clone(),
        model_directory: paths.models.clone(),
    };
    let activation_app = app.clone();
    let activation_cancellation = cancellation.token();
    let cache_paths = paths.clone();
    let activation_paths = paths;
    let activation_manifest_version = manifest.desktop_version.clone();
    let activation = tauri::async_runtime::spawn_blocking(move || {
        let previous_active_bytes = read_active_runtime_snapshot(&activation_paths)?;
        let previous_targets =
            target_profiles::current_state(&activation_app).map_err(|error| error.to_string())?;
        let projection = managed_runtime_projection_for(&activation_paths, &active);
        let validated = validate_managed_projection(
            &activation_paths,
            &projection,
            &activation_manifest_version,
            Some(&activation_cancellation),
        );
        let candidate_targets = match validated.and_then(|validated| {
            target_profiles::prepare_managed_activation(
                &activation_app,
                projection,
                validated,
            )
        }) {
            Ok(candidate) => candidate,
            Err(error) => {
                let cleanup = fs::remove_dir_all(&runtime);
                return Err(match cleanup {
                    Ok(()) => format!(
                        "The installed runtime failed the Desktop compatibility contract and was not activated: {error}"
                    ),
                    Err(cleanup) => format!(
                        "The installed runtime failed the Desktop compatibility contract and was not activated: {error}. Cleanup also failed for {}: {cleanup}",
                        runtime.display()
                    ),
                });
            }
        };
        let mut journal = ActivationJournal {
            schema_version: 2,
            stage: ActivationStage::Prepared,
            previous_active_bytes,
            previous_targets,
            candidate_active: active.clone(),
            candidate_targets: candidate_targets.clone(),
        };
        if let Err(error) = write_activation_journal(&activation_paths, &journal) {
            let cleanup = fs::remove_dir_all(&runtime);
            return Err(match cleanup {
                Ok(()) => error,
                Err(cleanup) => format!(
                    "{error}. The finalized but untracked runtime could not be removed from {}: {cleanup}",
                    runtime.display()
                ),
            });
        }

        if let Err(error) = target_profiles::replace_state(&activation_app, candidate_targets.clone())
            .map_err(|error| error.to_string())
        {
            return Err(rollback_activation(
                &activation_app,
                &activation_paths,
                &mut journal,
                &runtime,
                &error,
            ));
        }
        if let Err(error) = mark_journal_stage(
            &activation_paths,
            &mut journal,
            ActivationStage::ProfileWritten,
        ) {
            return Err(rollback_activation(
                &activation_app,
                &activation_paths,
                &mut journal,
                &runtime,
                &error,
            ));
        }
        if let Err(error) = write_active_runtime(&activation_paths, &active) {
            return Err(rollback_activation(
                &activation_app,
                &activation_paths,
                &mut journal,
                &runtime,
                &error,
            ));
        }

        // Both authoritative files are durable at this point. Journal marking and
        // removal are retryable cleanup and must never turn a committed activation
        // into a reported rollback.
        if let Err(error) = mark_journal_stage(
            &activation_paths,
            &mut journal,
            ActivationStage::Committed,
        ) {
            log::warn!(
                "Managed activation committed, but its journal could not be marked committed: {error}"
            );
        }
        finish_journal_cleanup(&activation_paths, "Committed a managed activation");
        log_runtime_reconciliation(&activation_paths);
        Ok::<_, String>(candidate_targets)
    })
    .await
    .map_err(|error| format!("Managed activation stopped unexpectedly: {error}"))??;
    if let Err(error) = uv_output(
        &app,
        &cache_paths,
        vec!["cache".into(), "prune".into(), "--ci".into()],
        None,
        cancellation.token(),
        "VidXP installation cache cleanup",
    )
    .await
    {
        log::warn!("VidXP was activated, but its installation cache could not be pruned: {error}");
    }
    stop_ui_process(&state);
    stop_api_process(&state);
    transition.commit_draft();
    refresh_tray_for_selected_target(&app);

    Ok(InstallTransitionResult {
        install: InstallResult {
            package_version: manifest.package_version,
            capabilities,
            surfaces,
            model_directory: activation
                .selected_profile()
                .and_then(|profile| profile.model_directory.as_ref())
                .map_or_else(String::new, |path| path.to_string_lossy().into_owned()),
            prepared: request.prepare_models,
        },
        setup: activation,
    })
}

fn stopped_browser_status(detail: impl Into<String>) -> BrowserServiceStatus {
    BrowserServiceStatus {
        state: "stopped",
        running: false,
        shared: false,
        port: None,
        local_url: None,
        network_url: None,
        detail: detail.into(),
    }
}

fn running_browser_status(ui: &ManagedUi) -> BrowserServiceStatus {
    BrowserServiceStatus {
        state: "ready",
        running: true,
        shared: ui.shared,
        port: Some(ui.port),
        local_url: Some(ui.local_url.clone()),
        network_url: ui.network_url.clone(),
        detail: if ui.shared {
            "The browser interface is available on this local network.".into()
        } else {
            "The browser interface is available only on this computer.".into()
        },
    }
}

fn start_ui(
    app: &AppHandle,
    state: &DesktopState,
    shared: bool,
) -> Result<BrowserServiceStatus, String> {
    let manifest = manifest()?;
    let selected = target_profiles::selected_profile(app).map_err(|error| error.to_string())?;
    let mut paths = desktop_paths(app)?;
    let profile = if selected.kind == target_profiles::TargetKind::Managed {
        let active = active_runtime(&paths)?;
        if selected.managed_runtime_profile.as_deref() != Some(active.profile.as_str()) {
            return Err(
                "The selected managed target no longer matches the active Desktop runtime.".into(),
            );
        }
        paths.models = active.model_directory.clone();
        let projection = managed_runtime_projection_for(&paths, &active);
        let validation = validate_managed_projection(
            &paths,
            &projection,
            &manifest.desktop_version,
            Some(&state.shutdown),
        );
        target_profiles::persist_selected_validation(app, validation)
            .map_err(|error| error.to_string())?
    } else {
        target_profiles::validated_selected_profile_with_cancellation(
            app,
            &manifest.desktop_version,
            Some(&state.shutdown),
        )
        .map_err(|error| error.to_string())?
    };
    target_profiles::authorize_lifecycle(&profile, target_profiles::LifecycleAction::Launch)
        .map_err(|error| error.to_string())?;
    if !profile.frontend.launchable {
        return Err(
            "The selected VidXP installation cannot launch the supported browser interface.".into(),
        );
    }
    paths.repository = profile.repository_root.clone();
    if let Some(model_directory) = &profile.model_directory {
        paths.models = model_directory.clone();
    }
    let mut active_process = state
        .ui_process
        .lock()
        .map_err(|_| "The desktop process supervisor is unavailable.".to_string())?;
    if let Some(ui) = active_process.as_mut() {
        let running = ui
            .process
            .try_wait()
            .map_err(|error| format!("Could not inspect the interface process: {error}"))?
            .is_none();
        match ui_process_action(running, &ui.profile_id, &profile.id) {
            UiProcessAction::Reuse if ui.shared == shared => {
                return Ok(running_browser_status(ui));
            }
            UiProcessAction::Reuse | UiProcessAction::Replace => {
                ui.process.terminate_and_reap();
            }
            UiProcessAction::Start => {}
        }
        *active_process = None;
    }

    let listener = TcpListener::bind((if shared { "0.0.0.0" } else { "127.0.0.1" }, 0))
        .map_err(|error| format!("Could not reserve a local interface port: {error}"))?;
    let port = listener
        .local_addr()
        .map_err(|error| format!("Could not identify the local interface port: {error}"))?
        .port();
    drop(listener);
    let nonce = browser_readiness_nonce();
    let readiness_file = paths
        .private_data
        .join(format!("browser-readiness-{nonce}.json"));
    if let Err(error) = fs::remove_file(&readiness_file)
        && error.kind() != io::ErrorKind::NotFound
    {
        return Err(format!(
            "Could not clear the stale browser readiness marker: {error}"
        ));
    }

    let mut command = target_command(&profile, &paths, &profile.executable);
    configure_ui_service_command(
        &mut command,
        &profile.repository_root,
        port,
        &readiness_file,
        &nonce,
        shared,
    );
    let mut process = background_process::spawn_service(command)
        .map_err(|error| format!("Could not start the VidXP interface: {}", error.detail))?;
    let network_url = browser_readiness::wait_for_browser_readiness(
        &mut process,
        &readiness_file,
        &nonce,
        port,
        Instant::now() + Duration::from_secs(30),
        &state.shutdown,
    )?;
    if shared && network_url.is_none() {
        process.terminate_and_reap();
        return Err("The browser interface started, but VidXP could not determine its local-network address.".into());
    }
    let local_url = format!("http://127.0.0.1:{port}");
    let ui = ManagedUi {
        process,
        port,
        local_url,
        network_url,
        shared,
        profile_id: profile.id.clone(),
    };
    let status = running_browser_status(&ui);
    *active_process = Some(ui);
    Ok(status)
}

fn stop_ui_process(state: &DesktopState) {
    let Ok(mut active) = state.ui_process.lock() else {
        return;
    };
    if let Some(mut ui) = active.take() {
        ui.process.terminate_and_reap();
    }
}

fn inspect_browser_service(state: &DesktopState) -> Result<BrowserServiceStatus, String> {
    let mut active = state
        .ui_process
        .lock()
        .map_err(|_| "The browser process supervisor is unavailable.".to_string())?;
    let Some(ui) = active.as_mut() else {
        return Ok(stopped_browser_status("The browser interface is stopped."));
    };
    if ui
        .process
        .try_wait()
        .map_err(|error| format!("Could not inspect the browser interface: {error}"))?
        .is_some()
    {
        *active = None;
        return Ok(stopped_browser_status("The browser interface exited."));
    }
    Ok(running_browser_status(ui))
}

#[tauri::command]
fn browser_service_status(
    app: AppHandle,
    state: tauri::State<'_, DesktopState>,
) -> Result<BrowserServiceStatus, String> {
    let _active = state.active_operations.register()?;
    let status = inspect_browser_service(&state)?;
    refresh_tray_menu(&app);
    Ok(status)
}

async fn start_browser_mode(app: AppHandle, shared: bool) -> Result<BrowserServiceStatus, String> {
    let state = app.state::<DesktopState>();
    let _active = state.active_operations.register()?;
    let worker_app = app.clone();
    let result = tauri::async_runtime::spawn_blocking(move || {
        let state = worker_app.state::<DesktopState>();
        start_ui(&worker_app, &state, shared)
    })
    .await
    .map_err(|error| format!("Browser sharing startup stopped unexpectedly: {error}"))?;
    refresh_tray_menu(&app);
    result
}

#[tauri::command]
async fn start_shared_browser(
    app: AppHandle,
    _state: tauri::State<'_, DesktopState>,
) -> Result<BrowserServiceStatus, String> {
    start_browser_mode(app, true).await
}

#[tauri::command]
fn stop_browser_service(
    app: AppHandle,
    state: tauri::State<'_, DesktopState>,
) -> Result<BrowserServiceStatus, String> {
    let _active = state.active_operations.register()?;
    stop_ui_process(&state);
    refresh_tray_menu(&app);
    Ok(stopped_browser_status("The browser interface was stopped."))
}

fn target_companion_executable(profile: &target_profiles::TargetProfile, name: &str) -> PathBuf {
    let filename = if cfg!(windows) {
        format!("{name}.exe")
    } else {
        name.to_owned()
    };
    profile
        .executable
        .parent()
        .map_or_else(|| PathBuf::from(&filename), |parent| parent.join(&filename))
}

fn target_command(
    profile: &target_profiles::TargetProfile,
    paths: &DesktopPaths,
    executable_path: &Path,
) -> Command {
    match profile.kind {
        target_profiles::TargetKind::Managed => configured_command(executable_path, paths),
        target_profiles::TargetKind::ExistingLocal => Command::new(executable_path),
    }
}

fn selected_target_context(
    app: &AppHandle,
) -> Result<(target_profiles::TargetProfile, DesktopPaths), String> {
    let profile = target_profiles::selected_profile(app).map_err(|error| error.to_string())?;
    let mut paths = desktop_paths(app)?;
    paths.data = profile.data_root.clone();
    paths.repository = profile.repository_root.clone();
    if let Some(model_directory) = &profile.model_directory {
        paths.models = model_directory.clone();
    }
    Ok((profile, paths))
}

fn execute_target_json(
    command: Command,
    operation: &str,
    timeout: Duration,
) -> Result<serde_json::Value, String> {
    let output = background_process::run(
        command,
        background_process::BackgroundPolicy {
            timeout,
            max_output_bytes: MAX_SETUP_OUTPUT_BYTES,
        },
        None,
    )
    .map_err(|error| format!("{operation} failed: {}", error.detail))?;
    let payload = serde_json::from_slice(&output.stdout).map_err(|error| {
        let stderr = String::from_utf8_lossy(&output.stderr).trim().to_owned();
        format!(
            "{operation} did not return valid JSON: {error}{}",
            if stderr.is_empty() {
                String::new()
            } else {
                format!(". {stderr}")
            }
        )
    })?;
    Ok(payload)
}

#[tauri::command]
async fn target_doctor(
    app: AppHandle,
    state: tauri::State<'_, DesktopState>,
) -> Result<serde_json::Value, String> {
    let _active = state.active_operations.register()?;
    tauri::async_runtime::spawn_blocking(move || {
        let (profile, paths) = selected_target_context(&app)?;
        let arguments = capability_command_arguments(&manifest()?, "doctor", &profile.capabilities);
        let mut command = target_command(&profile, &paths, &profile.executable);
        command
            .arg("--data-dir")
            .arg(&profile.data_root)
            .arg("--index-dir")
            .arg(&profile.repository_root)
            .args(arguments);
        execute_target_json(command, "VidXP doctor", Duration::from_secs(180))
    })
    .await
    .map_err(|error| format!("VidXP doctor stopped unexpectedly: {error}"))?
}

#[tauri::command]
async fn configure_external_installation(
    app: AppHandle,
    state: tauri::State<'_, DesktopState>,
    capabilities: Vec<String>,
    surfaces: Vec<String>,
) -> Result<target_profiles::TargetState, String> {
    let cancellation = OperationCancellationGuard::register(&state)?;
    let _transition =
        TargetTransitionCoordinator::begin(&state, TransitionKind::ConfigureExternalInstallation)
            .map_err(|error| error.to_string())?;
    let manifest = manifest()?;
    let selected_surfaces = selected_surfaces(&manifest, &surfaces)?;
    let selected_capabilities: Vec<_> = capabilities
        .into_iter()
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect();
    if let Some(unknown) = selected_capabilities
        .iter()
        .find(|name| !manifest.capabilities.contains_key(*name))
    {
        return Err(format!("Unknown VidXP search feature: {unknown}"));
    }
    let (profile, paths) = selected_target_context(&app)?;
    if profile.kind != target_profiles::TargetKind::ExistingLocal
        || profile.lifecycle_ownership != target_profiles::LifecycleOwnership::External
    {
        return Err("Use the managed setup screen to change this VidXP installation.".into());
    }
    let runtime_update_required = profile
        .validation_error
        .as_ref()
        .is_some_and(|error| error.code == target_profiles::TargetErrorCode::RuntimeUpdateRequired);
    if !runtime_update_required
        && profile.probe_protocol_version == target_profiles::SUPPORTED_PROBE_PROTOCOL_VERSION
        && selected_surfaces == profile.surfaces
        && selected_capabilities == profile.capabilities
    {
        return target_profiles::current_state(&app).map_err(|error| error.to_string());
    }
    let runtime = profile.runtime.as_ref().ok_or_else(|| {
        "The selected installation did not report its Python environment.".to_string()
    })?;
    if !runtime.python_executable.is_file() {
        return Err(
            "The selected installation's Python environment is no longer available.".into(),
        );
    }
    let tool_directory_output = uv_captured_output(
        &app,
        &paths,
        vec!["tool".into(), "dir".into()],
        cancellation.token(),
        "VidXP installation lookup",
    )
    .await?;
    let tool_directory = PathBuf::from(
        String::from_utf8_lossy(&tool_directory_output.stdout)
            .trim()
            .to_owned(),
    );
    let expected_environment = tool_directory.join(&manifest.package_name);
    if !same_path(&runtime.prefix, &expected_environment) {
        return Err(
            "This VidXP installation is not an isolated uv tool installation. Use its package manager to change installed features, then check it again."
                .into(),
        );
    }
    stop_ui_process(&state);
    stop_api_process(&state);
    let target_version = external_installation_version(
        &manifest,
        runtime_update_required,
        profile.probe_protocol_version,
        &profile.observed_vidxp_version,
    )?;
    let arguments = external_installation_arguments(
        &manifest,
        &selected_capabilities,
        &selected_surfaces,
        &runtime.python_version,
        target_version,
    )?;
    uv_output(
        &app,
        &paths,
        arguments,
        None,
        cancellation.token(),
        "VidXP feature update",
    )
    .await?;
    target_profiles::validated_selected_profile_with_cancellation(
        &app,
        &manifest.desktop_version,
        Some(&state.shutdown),
    )
    .map_err(|error| error.to_string())?;
    let result = target_profiles::current_state(&app).map_err(|error| error.to_string())?;
    refresh_tray_for_selected_target(&app);
    Ok(result)
}

#[tauri::command]
async fn mcp_client_config(
    app: AppHandle,
    state: tauri::State<'_, DesktopState>,
) -> Result<String, String> {
    let _active = state.active_operations.register()?;
    tauri::async_runtime::spawn_blocking(move || {
        let (profile, paths) = selected_target_context(&app)?;
        if !profile
            .surfaces
            .iter()
            .any(|surface| surface == "mcp" || surface == "server")
        {
            return Err(
                "The selected VidXP installation does not expose an installed MCP surface.".into(),
            );
        }
        let executable_path = target_companion_executable(&profile, "vidxp-mcp");
        if !executable_path.is_file() {
            return Err(format!(
                "The selected installation did not provide {}.",
                executable_path.display()
            ));
        }
        let mut command = target_command(&profile, &paths, &executable_path);
        command
            .arg("--print-config")
            .arg("--repository")
            .arg("default")
            .arg("--index-directory")
            .arg(&profile.repository_root)
            .arg("--data-dir")
            .arg(&profile.data_root);
        let output = checked_output(command, "VidXP MCP configuration")?;
        let payload: serde_json::Value = serde_json::from_slice(&output.stdout)
            .map_err(|error| format!("VidXP returned invalid MCP configuration JSON: {error}"))?;
        serde_json::to_string_pretty(&payload)
            .map_err(|error| format!("Could not format the MCP configuration: {error}"))
    })
    .await
    .map_err(|error| format!("MCP configuration stopped unexpectedly: {error}"))?
}

#[tauri::command]
async fn install_codex_plugin(
    app: AppHandle,
    state: tauri::State<'_, DesktopState>,
) -> Result<CodexPluginInstallResult, String> {
    let _active = state.active_operations.register()?;
    tauri::async_runtime::spawn_blocking(move || {
        let (profile, paths) = selected_target_context(&app)?;
        if !profile
            .surfaces
            .iter()
            .any(|surface| surface == "mcp" || surface == "server")
        {
            return Err(
                "The selected VidXP installation does not expose an installed MCP surface.".into(),
            );
        }
        let installer_path = target_companion_executable(&profile, "vidxp-codex-plugin");
        if !installer_path.is_file() {
            return Err(format!(
                "The selected installation did not provide {}. Update VidXP and try again.",
                installer_path.display()
            ));
        }
        let marketplace_root = paths.private_data.join("codex-marketplace");
        let mut command = target_command(&profile, &paths, &installer_path);
        command
            .arg("--marketplace-root")
            .arg(&marketplace_root)
            .arg("--repository")
            .arg("default")
            .arg("--index-directory")
            .arg(&profile.repository_root)
            .arg("--data-dir")
            .arg(&profile.data_root);
        let output = checked_output(command, "VidXP Codex plugin setup")?;
        serde_json::from_slice(&output.stdout)
            .map_err(|error| format!("VidXP returned invalid Codex setup details: {error}"))
    })
    .await
    .map_err(|error| format!("Codex plugin setup stopped unexpectedly: {error}"))?
}

fn execute_worker_action(app: &AppHandle, action: &str) -> Result<LocalWorkerStatus, String> {
    let (profile, paths) = selected_target_context(app)?;
    if !profile.surfaces.iter().any(|surface| surface == "worker") {
        return Err("Local video processing is not installed for this VidXP setup.".into());
    }
    let mut command = target_command(&profile, &paths, &profile.executable);
    command
        .arg("--data-dir")
        .arg(&profile.data_root)
        .arg("--index-dir")
        .arg(&profile.repository_root)
        .args(["jobs", action]);
    let payload = execute_target_json(command, "VidXP local processing", Duration::from_secs(120))?;
    serde_json::from_value(payload)
        .map_err(|error| format!("VidXP returned an invalid processing status: {error}"))
}

fn remember_worker_status(
    state: &DesktopState,
    profile_id: String,
    status: &Result<LocalWorkerStatus, String>,
) {
    if let Ok(mut current) = state.worker_status.lock() {
        *current = Some((profile_id, status.clone()));
    }
}

async fn run_worker_action(
    app: AppHandle,
    action: &'static str,
) -> Result<LocalWorkerStatus, String> {
    let state = app.state::<DesktopState>();
    let _active = state.active_operations.register()?;
    let profile_id = target_profiles::selected_profile(&app)
        .map_err(|error| error.to_string())?
        .id;
    let worker_app = app.clone();
    let result =
        tauri::async_runtime::spawn_blocking(move || execute_worker_action(&worker_app, action))
            .await
            .map_err(|error| format!("Local processing action stopped unexpectedly: {error}"))?;
    remember_worker_status(&state, profile_id, &result);
    refresh_tray_menu(&app);
    result
}

#[tauri::command]
async fn local_worker_status(
    app: AppHandle,
    _state: tauri::State<'_, DesktopState>,
) -> Result<LocalWorkerStatus, String> {
    run_worker_action(app, "worker-status").await
}

#[tauri::command]
async fn start_local_worker(
    app: AppHandle,
    _state: tauri::State<'_, DesktopState>,
) -> Result<LocalWorkerStatus, String> {
    run_worker_action(app, "start-worker").await
}

#[tauri::command]
async fn stop_local_worker(
    app: AppHandle,
    _state: tauri::State<'_, DesktopState>,
) -> Result<LocalWorkerStatus, String> {
    run_worker_action(app, "stop-worker").await
}

fn http_health_is_ready(host: &str, port: u16) -> bool {
    let Ok(address) = format!("{host}:{port}").parse::<SocketAddr>() else {
        return false;
    };
    let Ok(mut stream) = TcpStream::connect_timeout(&address, Duration::from_millis(200)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(300)));
    if stream
        .write_all(
            format!("GET /health HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n").as_bytes(),
        )
        .is_err()
    {
        return false;
    }
    let mut response = [0_u8; 128];
    stream
        .read(&mut response)
        .is_ok_and(|read| String::from_utf8_lossy(&response[..read]).starts_with("HTTP/1.1 200"))
}

fn stopped_server_status(detail: impl Into<String>) -> LocalServerStatus {
    LocalServerStatus {
        state: "stopped",
        running: false,
        shared: false,
        port: None,
        origin: None,
        health_url: None,
        mcp_url: None,
        bearer_token: None,
        detail: detail.into(),
    }
}

fn running_server_status(service: &ManagedApiService, healthy: bool) -> LocalServerStatus {
    LocalServerStatus {
        state: if healthy { "ready" } else { "starting" },
        running: true,
        shared: service.shared,
        port: Some(service.port),
        health_url: Some(service.health_url.clone()),
        mcp_url: Some(service.mcp_url.clone()),
        origin: Some(service.origin.clone()),
        bearer_token: service.bearer_token.clone(),
        detail: if healthy {
            if service.shared {
                "The API and MCP service is available on this local network.".into()
            } else {
                "The API and MCP service is available only on this computer.".into()
            }
        } else {
            "The service process is running but its health endpoint is not ready.".into()
        },
    }
}

fn stop_api_process(state: &DesktopState) {
    let Ok(mut active) = state.api_process.lock() else {
        return;
    };
    if let Some(mut service) = active.take() {
        service.process.terminate_and_reap();
    }
}

fn inspect_local_server(state: &DesktopState) -> Result<LocalServerStatus, String> {
    let mut active = state
        .api_process
        .lock()
        .map_err(|_| "The API process supervisor is unavailable.".to_string())?;
    let Some(service) = active.as_mut() else {
        return Ok(stopped_server_status(
            "The local API and MCP service is stopped.",
        ));
    };
    if service
        .process
        .try_wait()
        .map_err(|error| format!("Could not inspect the local service process: {error}"))?
        .is_some()
    {
        *active = None;
        return Ok(stopped_server_status(
            "The local API and MCP service exited.",
        ));
    }
    let healthy = http_health_is_ready(&service.health_host, service.port);
    Ok(running_server_status(service, healthy))
}

#[tauri::command]
fn local_server_status(
    app: AppHandle,
    state: tauri::State<'_, DesktopState>,
) -> Result<LocalServerStatus, String> {
    let _active = state.active_operations.register()?;
    let status = inspect_local_server(&state)?;
    refresh_tray_menu(&app);
    Ok(status)
}

fn api_service_command(
    profile: &target_profiles::TargetProfile,
    paths: &DesktopPaths,
    executable_path: &Path,
    port: u16,
    shared: bool,
) -> Command {
    let mut command = target_command(profile, paths, executable_path);
    command
        .env("VIDXP_REPOSITORY_ROOT", &profile.repository_root)
        .env("VIDXP_MODEL_CACHE", &paths.models)
        .arg("--data-dir")
        .arg(&profile.data_root)
        .arg("--port")
        .arg(port.to_string());
    if shared {
        command.arg("--share");
    }
    command
}

fn start_server_mode(
    app: &AppHandle,
    state: &DesktopState,
    shared: bool,
) -> Result<LocalServerStatus, String> {
    let (profile, paths) = selected_target_context(app)?;
    if !profile.surfaces.iter().any(|surface| surface == "server") {
        return Err(
            "The selected VidXP installation does not include the app integration service.".into(),
        );
    }
    let mut active = state
        .api_process
        .lock()
        .map_err(|_| "The API process supervisor is unavailable.".to_string())?;
    if let Some(service) = active.as_mut() {
        let running = service
            .process
            .try_wait()
            .map_err(|error| format!("Could not inspect the local service process: {error}"))?
            .is_none();
        if running && service.profile_id == profile.id && service.shared == shared {
            let healthy = http_health_is_ready(&service.health_host, service.port);
            return Ok(running_server_status(service, healthy));
        }
        service.process.terminate_and_reap();
        *active = None;
    }
    let listener = TcpListener::bind((if shared { "0.0.0.0" } else { "127.0.0.1" }, 0))
        .map_err(|error| format!("Could not reserve a local API port: {error}"))?;
    let port = listener
        .local_addr()
        .map_err(|error| format!("Could not identify the local API port: {error}"))?
        .port();
    drop(listener);
    let executable_path = target_companion_executable(&profile, "vidxp-api");
    if !executable_path.is_file() {
        return Err(format!(
            "The selected installation did not provide {}.",
            executable_path.display()
        ));
    }
    let (health_host, origin, health_url, mcp_url, bearer_token) = if shared {
        let mut details_command =
            api_service_command(&profile, &paths, &executable_path, port, true);
        details_command.arg("--print-share-details");
        let output = checked_output(details_command, "VidXP network sharing setup")?;
        let details: ApiShareDetails = serde_json::from_slice(&output.stdout)
            .map_err(|error| format!("VidXP returned invalid network sharing details: {error}"))?;
        if details.port != port {
            return Err("VidXP reported the wrong network sharing port.".into());
        }
        (
            details.host,
            details.origin,
            details.health_url,
            details.mcp_url,
            Some(details.bearer_token),
        )
    } else {
        let origin = format!("http://127.0.0.1:{port}");
        (
            "127.0.0.1".into(),
            origin.clone(),
            format!("{origin}/health"),
            format!("{origin}/mcp"),
            None,
        )
    };
    let command = api_service_command(&profile, &paths, &executable_path, port, shared);
    let mut process = background_process::spawn_service(command).map_err(|error| {
        format!(
            "Could not start the local API and MCP service: {}",
            error.detail
        )
    })?;
    let deadline = Instant::now() + Duration::from_secs(30);
    loop {
        if http_health_is_ready(&health_host, port) {
            break;
        }
        if process
            .try_wait()
            .map_err(|error| format!("Could not inspect the local service: {error}"))?
            .is_some()
        {
            return Err("The local API and MCP service exited before becoming healthy.".into());
        }
        if Instant::now() >= deadline {
            return Err(
                "The local API and MCP service did not become healthy within 30 seconds.".into(),
            );
        }
        thread::sleep(Duration::from_millis(100));
    }
    let service = ManagedApiService {
        process,
        port,
        health_host,
        origin,
        health_url,
        mcp_url,
        bearer_token,
        shared,
        profile_id: profile.id,
    };
    let status = running_server_status(&service, true);
    *active = Some(service);
    Ok(status)
}

async fn start_server(app: AppHandle, shared: bool) -> Result<LocalServerStatus, String> {
    let state = app.state::<DesktopState>();
    let _active = state.active_operations.register()?;
    let worker_app = app.clone();
    let result = tauri::async_runtime::spawn_blocking(move || {
        let state = worker_app.state::<DesktopState>();
        start_server_mode(&worker_app, &state, shared)
    })
    .await
    .map_err(|error| format!("Local service startup stopped unexpectedly: {error}"))?;
    refresh_tray_menu(&app);
    result
}

#[tauri::command]
async fn start_local_server(
    app: AppHandle,
    _state: tauri::State<'_, DesktopState>,
) -> Result<LocalServerStatus, String> {
    start_server(app, false).await
}

#[tauri::command]
async fn start_shared_server(
    app: AppHandle,
    _state: tauri::State<'_, DesktopState>,
) -> Result<LocalServerStatus, String> {
    start_server(app, true).await
}

#[tauri::command]
fn stop_local_server(
    app: AppHandle,
    state: tauri::State<'_, DesktopState>,
) -> Result<LocalServerStatus, String> {
    let _active = state.active_operations.register()?;
    stop_api_process(&state);
    refresh_tray_menu(&app);
    Ok(stopped_server_status(
        "The Desktop-owned API and MCP service was stopped.",
    ))
}

fn browser_readiness_nonce() -> String {
    let sequence = READINESS_SEQUENCE.fetch_add(1, Ordering::Relaxed);
    let timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .unwrap_or_default();
    hex::encode(Sha256::digest(format!(
        "{}:{timestamp}:{sequence}",
        std::process::id()
    )))
}

fn configure_ui_service_command(
    command: &mut Command,
    repository_root: &Path,
    port: u16,
    readiness_file: &Path,
    nonce: &str,
    shared: bool,
) {
    command
        // The desktop owns the one intentional browser open after readiness. Without
        // headless mode Streamlit also opens the URL, producing duplicate tabs and
        // potentially visible launcher consoles on Windows.
        .env("STREAMLIT_SERVER_HEADLESS", "true")
        .env("VIDXP_DESKTOP_READINESS_FILE", readiness_file)
        .env("VIDXP_DESKTOP_READINESS_NONCE", nonce)
        .env("VIDXP_DESKTOP_UI_PORT", port.to_string())
        .env("VIDXP_DESKTOP_UI_SHARED", if shared { "1" } else { "0" })
        .arg("--index-dir")
        .arg(repository_root)
        .arg("ui");
    if shared {
        command.arg("--share");
    } else {
        command.args(["--host", "127.0.0.1"]);
    }
    command.args(["--port", &port.to_string()]);
}

fn hide_main_window(app: &AppHandle) -> Result<(), String> {
    let window = app
        .get_webview_window("main")
        .ok_or("The VidXP desktop window is unavailable.")?;
    window
        .hide()
        .map_err(|error| format!("Could not hide VidXP to the system tray: {error}"))
}

fn show_main_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}

fn configured_runtime(app: &AppHandle) -> bool {
    target_profiles::current_state(app)
        .ok()
        .is_some_and(|state| state.selected_profile().is_some())
}

fn browser_surface_configured(app: &AppHandle) -> bool {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .unwrap_or(u64::MAX);
    target_profiles::current_state(app)
        .ok()
        .and_then(|state| state.selected_profile().cloned())
        .is_some_and(|profile| profile.is_ready(now) && profile.frontend.launchable)
}

struct BrowserOpenGuard(AppHandle);

fn claim_browser_open(active: &AtomicBool) -> bool {
    active
        .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
        .is_ok()
}

impl Drop for BrowserOpenGuard {
    fn drop(&mut self) {
        self.0
            .state::<DesktopState>()
            .browser_open_active
            .store(false, Ordering::Release);
    }
}

async fn open_ui_in_browser(app: AppHandle) -> Result<(), String> {
    let state = app.state::<DesktopState>();
    if !claim_browser_open(&state.browser_open_active) {
        return Ok(());
    }
    let _browser_guard = BrowserOpenGuard(app.clone());
    let _active = state.active_operations.register()?;
    let transition = TargetTransitionCoordinator::begin(&state, TransitionKind::OpenBrowser)
        .map_err(|error| error.to_string())?;
    let current = inspect_browser_service(&state)?;
    let status = if current.running {
        current
    } else {
        let worker_app = app.clone();
        tauri::async_runtime::spawn_blocking(move || {
            let _transition = transition;
            let state = worker_app.state::<DesktopState>();
            start_ui(&worker_app, &state, false)
        })
        .await
        .map_err(|error| format!("VidXP interface startup stopped unexpectedly: {error}"))??
    };
    let url = status
        .local_url
        .ok_or_else(|| "VidXP did not report its local browser address.".to_string())?;
    app.opener()
        .open_url(&url, None::<&str>)
        .map_err(|error| format!("Could not open VidXP in the default browser: {error}"))?;
    refresh_tray_menu(&app);
    hide_main_window(&app)
}

fn open_browser_or_show_manager(app: &AppHandle) {
    if !browser_surface_configured(app) {
        show_main_window(app);
        return;
    }
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        if let Err(error) = open_ui_in_browser(app.clone()).await {
            show_main_window(&app);
            app.dialog()
                .message(error)
                .title("VidXP could not open")
                .kind(MessageDialogKind::Error)
                .blocking_show();
        }
    });
}

fn current_unix_seconds() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .unwrap_or(u64::MAX)
}

fn tray_installation_label(profile: Option<&target_profiles::TargetProfile>, now: u64) -> String {
    let Some(profile) = profile else {
        return "No installation selected".into();
    };
    match profile.validation_error.as_ref().map(|error| &error.code) {
        Some(target_profiles::TargetErrorCode::RuntimeUpdateRequired) => {
            format!("{} · Update required", profile.display_name)
        }
        Some(_) => format!("{} · Needs attention", profile.display_name),
        None if !profile.is_ready(now) => format!("{} · Check setup", profile.display_name),
        None => profile.display_name.clone(),
    }
}

fn tray_capability_state(selected: bool, installed: bool, ready: bool) -> Option<&'static str> {
    if !selected || !ready {
        Some("Unavailable")
    } else if !installed {
        Some("Not installed")
    } else {
        None
    }
}

fn tray_browser_label(
    status: &BrowserServiceStatus,
    selected: bool,
    installed: bool,
    ready: bool,
    status_known: bool,
) -> String {
    if let Some(state) = tray_capability_state(selected, installed, ready) {
        return format!("Browser · {state}");
    }
    if !status_known {
        return "Browser · Status unknown".into();
    }
    if !status.running {
        return "Browser · Off".into();
    }
    if status.shared {
        return "Browser · Shared".into();
    }
    "Browser · Private".into()
}

fn tray_worker_label(
    status: Option<&Result<LocalWorkerStatus, String>>,
    selected: bool,
    installed: bool,
    ready: bool,
) -> String {
    if let Some(state) = tray_capability_state(selected, installed, ready) {
        return format!("Processing · {state}");
    }
    match status {
        Some(Ok(status)) if status.running => "Processing · On",
        Some(Ok(_)) => "Processing · Off",
        Some(Err(_)) => "Processing · Status unknown",
        None => "Processing · Checking…",
    }
    .into()
}

fn tray_server_label(
    status: &LocalServerStatus,
    selected: bool,
    installed: bool,
    ready: bool,
    status_known: bool,
) -> String {
    if let Some(state) = tray_capability_state(selected, installed, ready) {
        return format!("App integration · {state}");
    }
    if !status_known {
        return "App integration · Status unknown".into();
    }
    if !status.running {
        return "App integration · Off".into();
    }
    format!(
        "App integration · {}",
        if status.shared { "Shared" } else { "Private" }
    )
}

fn refresh_tray_menu(app: &AppHandle) {
    let state = app.state::<DesktopState>();
    let items = state.tray_menu.lock().ok().and_then(|items| items.clone());
    let Some(items) = items else {
        return;
    };
    let target_state = target_profiles::current_state(app).ok();
    let profile = target_state
        .as_ref()
        .and_then(target_profiles::TargetState::selected_profile);
    let selected = profile.is_some();
    let ready = profile.is_some_and(|profile| profile.is_ready(current_unix_seconds()));
    let browser_installed = profile.is_some_and(|profile| profile.frontend.launchable);
    let worker_installed =
        profile.is_some_and(|profile| profile.surfaces.iter().any(|surface| surface == "worker"));
    let server_installed =
        profile.is_some_and(|profile| profile.surfaces.iter().any(|surface| surface == "server"));
    let browser_available = ready && browser_installed;
    let worker_available = ready && worker_installed;
    let server_available = ready && server_installed;
    let browser_result = inspect_browser_service(&state);
    let browser_status_known = browser_result.is_ok();
    let browser = browser_result
        .unwrap_or_else(|error| stopped_browser_status(format!("Status unavailable: {error}")));
    let server_result = inspect_local_server(&state);
    let server_status_known = server_result.is_ok();
    let server = server_result
        .unwrap_or_else(|error| stopped_server_status(format!("Status unavailable: {error}")));
    let worker = profile.and_then(|profile| {
        state.worker_status.lock().ok().and_then(|cached| {
            cached
                .as_ref()
                .filter(|(profile_id, _)| profile_id == &profile.id)
                .map(|(_, status)| status.clone())
        })
    });

    let _ = items
        .installation
        .set_text(tray_installation_label(profile, current_unix_seconds()));
    let _ = items.browser.set_text(tray_browser_label(
        &browser,
        selected,
        browser_installed,
        ready,
        browser_status_known,
    ));
    let _ = items.browser.set_enabled(browser_available);
    let _ = items.open_browser.set_text(if browser.running {
        "Open VidXP"
    } else {
        "Start and open VidXP"
    });
    let _ = items.open_browser.set_enabled(browser_available);
    let _ = items.share_browser.set_text(if browser.running {
        "Share on local network"
    } else {
        "Start and share"
    });
    let _ = items
        .share_browser
        .set_enabled(browser_available && !browser.shared);
    let _ = items.stop_browser.set_enabled(browser.running);

    let _ = items.worker.set_text(tray_worker_label(
        worker.as_ref(),
        selected,
        worker_installed,
        ready,
    ));
    let _ = items.worker.set_enabled(worker_available);
    let _ = items.start_worker.set_enabled(
        worker_available
            && worker
                .as_ref()
                .is_some_and(|status| status.as_ref().is_ok_and(|status| !status.running)),
    );
    let _ = items.stop_worker.set_enabled(
        worker
            .as_ref()
            .is_some_and(|status| status.as_ref().is_ok_and(|status| status.running)),
    );

    let _ = items.server.set_text(tray_server_label(
        &server,
        selected,
        server_installed,
        ready,
        server_status_known,
    ));
    let _ = items.server.set_enabled(server_available);
    let _ = items.start_server.set_text(if server.shared {
        "Make private"
    } else {
        "Start privately"
    });
    let _ = items
        .start_server
        .set_enabled(server_available && (!server.running || server.shared));
    let _ = items
        .share_server
        .set_enabled(server_available && !server.shared);
    let _ = items.share_server.set_text(if server.running {
        "Share on local network"
    } else {
        "Start and share"
    });
    let _ = items.stop_server.set_enabled(server.running);
}

fn refresh_tray_for_selected_target(app: &AppHandle) {
    let state = app.state::<DesktopState>();
    if let Ok(mut worker) = state.worker_status.lock() {
        *worker = None;
    }
    refresh_tray_menu(app);
    let profile = target_profiles::selected_profile(app).ok();
    if profile.is_some_and(|profile| {
        profile.is_ready(current_unix_seconds())
            && profile.surfaces.iter().any(|surface| surface == "worker")
    }) {
        let status_app = app.clone();
        tauri::async_runtime::spawn(async move {
            let _ = run_worker_action(status_app, "worker-status").await;
        });
    }
}

fn show_tray_action_error(app: &AppHandle, error: String) {
    show_main_window(app);
    app.dialog()
        .message(error)
        .title("VidXP action could not complete")
        .kind(MessageDialogKind::Error)
        .blocking_show();
}

fn perform_service_action(app: &AppHandle, action: DesktopAction) {
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        let result = match action {
            DesktopAction::ShareBrowser => start_browser_mode(app.clone(), true).await.map(|_| ()),
            DesktopAction::StopBrowser => {
                let state = app.state::<DesktopState>();
                state.active_operations.register().map(|_active| {
                    stop_ui_process(&state);
                    refresh_tray_menu(&app);
                })
            }
            DesktopAction::StartWorker => run_worker_action(app.clone(), "start-worker")
                .await
                .map(|_| ()),
            DesktopAction::StopWorker => run_worker_action(app.clone(), "stop-worker")
                .await
                .map(|_| ()),
            DesktopAction::StartServer => start_server(app.clone(), false).await.map(|_| ()),
            DesktopAction::ShareServer => start_server(app.clone(), true).await.map(|_| ()),
            DesktopAction::StopServer => {
                let state = app.state::<DesktopState>();
                state.active_operations.register().map(|_active| {
                    stop_api_process(&state);
                    refresh_tray_menu(&app);
                })
            }
            DesktopAction::Manage | DesktopAction::OpenBrowser | DesktopAction::Quit => Ok(()),
        };
        if let Err(error) = result {
            refresh_tray_menu(&app);
            show_tray_action_error(&app, error);
        }
    });
}

fn perform_desktop_action(app: &AppHandle, action: DesktopAction) {
    match action {
        DesktopAction::Manage => show_main_window(app),
        DesktopAction::OpenBrowser => open_browser_or_show_manager(app),
        DesktopAction::ShareBrowser
        | DesktopAction::StopBrowser
        | DesktopAction::StartWorker
        | DesktopAction::StopWorker
        | DesktopAction::StartServer
        | DesktopAction::ShareServer
        | DesktopAction::StopServer => perform_service_action(app, action),
        DesktopAction::Quit => begin_shutdown(app),
    }
}

fn begin_shutdown(app: &AppHandle) {
    let state = app.state::<DesktopState>();
    if let Err(error) = state.active_operations.close() {
        log::error!("Could not close operation registration during shutdown: {error}");
    }
    if state.shutdown_started.swap(true, Ordering::AcqRel) {
        return;
    }
    state.shutdown.cancel();
    cancel_active_operation(&state);
    log::info!("VidXP supervised shutdown requested");
    stop_ui_process(&state);
    stop_api_process(&state);
    let app = app.clone();
    let operations = state.active_operations.clone();
    tauri::async_runtime::spawn(async move {
        let shutdown_app = app.clone();
        let result = tauri::async_runtime::spawn_blocking(move || {
            let deadline = Instant::now() + Duration::from_secs(20);
            if !operations.wait_until_idle(Instant::now() + Duration::from_secs(10)) {
                log::error!(
                    "Timed out waiting for supervised operations to acknowledge cancellation; forcing final owned-process cleanup"
                );
            }
            shutdown(&shutdown_app, deadline);
        })
        .await;
        if let Err(error) = result {
            log::error!("The shutdown coordinator stopped unexpectedly: {error}");
        }
        log::info!("VidXP supervised shutdown completed");
        app.exit(0);
    });
}

#[tauri::command]
async fn launch_ui(app: AppHandle) -> Result<(), String> {
    open_ui_in_browser(app).await
}

fn create_tray(app: &tauri::App) -> tauri::Result<()> {
    let installation = MenuItem::with_id(
        app,
        "installation-status",
        "VidXP installation",
        false,
        None::<&str>,
    )?;
    let open = MenuItem::with_id(app, "open", "Open VidXP", true, None::<&str>)?;
    let share_browser = MenuItem::with_id(
        app,
        "share-browser",
        "Share on local network",
        true,
        None::<&str>,
    )?;
    let stop_browser = MenuItem::with_id(app, "stop-browser", "Stop browser", false, None::<&str>)?;
    let browser = Submenu::with_items(
        app,
        "Browser · Checking…",
        true,
        &[&share_browser, &stop_browser],
    )?;
    let start_worker =
        MenuItem::with_id(app, "start-worker", "Start processing", false, None::<&str>)?;
    let stop_worker =
        MenuItem::with_id(app, "stop-worker", "Stop processing", false, None::<&str>)?;
    let worker = Submenu::with_items(
        app,
        "Processing · Checking…",
        true,
        &[&start_worker, &stop_worker],
    )?;
    let start_server =
        MenuItem::with_id(app, "start-server", "Start privately", true, None::<&str>)?;
    let share_server = MenuItem::with_id(
        app,
        "share-server",
        "Share on local network",
        true,
        None::<&str>,
    )?;
    let stop_server =
        MenuItem::with_id(app, "stop-server", "Stop integration", false, None::<&str>)?;
    let server = Submenu::with_items(
        app,
        "App integration · Checking…",
        true,
        &[&start_server, &share_server, &stop_server],
    )?;
    let manage = MenuItem::with_id(app, "manage", "Manage VidXP", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit VidXP", true, None::<&str>)?;
    let separator = PredefinedMenuItem::separator(app)?;
    let separator_two = PredefinedMenuItem::separator(app)?;
    let menu = Menu::with_items(
        app,
        &[
            &installation,
            &separator,
            &open,
            &browser,
            &worker,
            &server,
            &separator_two,
            &manage,
            &quit,
        ],
    )?;
    let items = TrayMenuItems {
        installation,
        browser,
        open_browser: open,
        share_browser,
        stop_browser,
        worker,
        start_worker,
        stop_worker,
        server,
        start_server,
        share_server,
        stop_server,
    };
    if let Ok(mut current) = app.state::<DesktopState>().tray_menu.lock() {
        *current = Some(items);
    }
    let mut tray = TrayIconBuilder::with_id("vidxp")
        .tooltip("VidXP")
        .menu(&menu)
        .show_menu_on_left_click(true)
        .on_menu_event(|app, event| {
            if let Some(action) =
                action_for_activation(DesktopActivation::Tray(event.id().as_ref()))
            {
                perform_desktop_action(app, action);
            }
        });
    if let Some(icon) = app.default_window_icon() {
        tray = tray.icon(icon.clone());
    }
    tray.build(app)?;
    refresh_tray_for_selected_target(app.handle());
    Ok(())
}

fn stop_worker_before(runtime: &Path, paths: &DesktopPaths, deadline: Instant) {
    let remaining = deadline.saturating_duration_since(Instant::now());
    if remaining.is_zero() {
        log::error!("Shutdown deadline elapsed before a managed worker could be stopped");
        return;
    }
    let mut command = configured_command(&executable(runtime, "vidxp"), paths);
    command
        .arg("--index-dir")
        .arg(&paths.repository)
        .args(["jobs", "stop-worker"]);
    let _ = background_process::run(
        command,
        background_process::BackgroundPolicy {
            timeout: remaining.min(Duration::from_secs(5)),
            max_output_bytes: 64 * 1024,
        },
        None,
    );
}

fn shutdown(app: &AppHandle, deadline: Instant) {
    log::info!("Stopping active VidXP processes");
    let state = app.state::<DesktopState>();
    cancel_active_operation(&state);
    stop_ui_process(&state);
    stop_api_process(&state);
    let Ok(mut paths) = desktop_paths(app) else {
        log::warn!("Could not resolve desktop paths during shutdown");
        return;
    };
    let operation_worker = state.worker_stop.stop_active_before(deadline);
    let Ok(profile) = target_profiles::selected_profile(app) else {
        log::info!("No selected VidXP target needs worker shutdown");
        return;
    };
    if target_profiles::authorize_lifecycle(
        &profile,
        target_profiles::LifecycleAction::BroadProcessStop,
    )
    .is_err()
    {
        log::info!("Skipping broad worker shutdown for an externally owned VidXP target");
        return;
    }
    let Ok(active) = active_runtime(&paths) else {
        log::info!("No active desktop-managed VidXP runtime needs worker shutdown");
        return;
    };
    if profile.managed_runtime_profile.as_deref() != Some(active.profile.as_str()) {
        log::warn!("Skipping worker shutdown because the selected managed target is not active");
        return;
    }
    paths.models = active.model_directory.clone();
    let runtime = runtime_directory(&paths, &active);
    if operation_worker
        .as_ref()
        .is_none_or(|stopped| !same_path(stopped, &runtime))
    {
        stop_worker_before(&runtime, &paths, deadline);
    }
    log::info!("Active VidXP worker shutdown finished");
}

pub fn run() {
    let builder = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            if let Some(action) = action_for_activation(DesktopActivation::SingleInstance) {
                perform_desktop_action(app, action);
            }
        }))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_log::Builder::new().build())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_store::Builder::default().build())
        .manage(DesktopState::default())
        .setup(|app| {
            migrate_legacy_shared_data(app.handle()).map_err(io::Error::other)?;
            recover_interrupted_activation(app.handle(), &app.state::<DesktopState>())
                .map_err(io::Error::other)?;
            if let Ok(paths) = desktop_paths(app.handle()) {
                log_runtime_reconciliation(&paths);
            }
            if let Err(error) = initialize_target_profiles(app.handle()) {
                log::error!("Target profile initialization failed: {error}");
            }
            create_tray(app)?;
            if let Some(action) = action_for_activation(DesktopActivation::Startup) {
                perform_desktop_action(app.handle(), action);
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            runtime_manifest,
            target_state,
            refresh_target_state,
            discover_local_targets,
            choose_local_executable,
            inspect_local_target,
            adopt_local_target,
            select_target_profile,
            delete_target_profile,
            confirm_forget_target,
            begin_managed_setup,
            cancel_managed_setup,
            choose_model_directory,
            install_media_runtime,
            runtime_status,
            model_directory_inventory,
            prepare_managed_models,
            install_runtime,
            launch_ui,
            target_doctor,
            configure_external_installation,
            mcp_client_config,
            install_codex_plugin,
            local_worker_status,
            start_local_worker,
            stop_local_worker,
            browser_service_status,
            start_shared_browser,
            stop_browser_service,
            local_server_status,
            start_local_server,
            start_shared_server,
            stop_local_server
        ]);
    let app = builder
        .build(tauri::generate_context!())
        .expect("could not initialize VidXP desktop");
    app.run(|app_handle, event| match event {
        RunEvent::WindowEvent {
            label,
            event: WindowEvent::CloseRequested { api, .. },
            ..
        } if label == "main" => {
            if app_handle.state::<DesktopState>().shutdown.is_cancelled() {
                return;
            }
            api.prevent_close();
            match close_action(configured_runtime(app_handle)) {
                DesktopCloseAction::HideToTray => {
                    let _ = hide_main_window(app_handle);
                }
                DesktopCloseAction::Quit => begin_shutdown(app_handle),
            }
        }
        RunEvent::ExitRequested { api, .. }
            if !app_handle.state::<DesktopState>().shutdown.is_cancelled() =>
        {
            api.prevent_exit();
            begin_shutdown(app_handle);
        }
        _ => {}
    });
}

#[cfg(test)]
mod tests {
    use super::{
        ActivationJournal, ActivationRecovery, ActivationStage, ActiveRuntime, DesktopAction,
        DesktopActivation, DesktopCloseAction, DesktopState, DraftPhase, DraftRecord,
        ManagedSetupDraft, RUNTIME_CONSTRAINTS_FILE_NAME, RUNTIME_PACKAGE_WHEEL_NAME,
        TargetTransitionCoordinator, TransitionKind, UiProcessAction, WorkerStopSupervisor,
        action_for_activation, activation_recovery, base_package_specification,
        capability_command_arguments, claim_browser_open, clean_environment_from, close_action,
        configure_ui_service_command, configured_runtime_status,
        dependency_installation_invocation, desktop_paths_from_roots, display_command,
        external_installation_arguments, external_installation_version, inventory_model_directory,
        manifest, manifest_digest, normalize_line_endings, normalized_runtime_constraints,
        package_acquisition_arguments, package_specification, read_active_runtime_snapshot,
        reconcile_managed_runtime_storage, required_encoder_missing, restore_active_runtime,
        selected_capabilities, selected_surfaces, ui_process_action,
        validate_managed_runtime_identity, write_activation_journal, write_active_runtime,
    };
    use std::{
        ffi::OsStr,
        fs,
        path::{Path, PathBuf},
        process::Command,
        sync::{
            Arc,
            atomic::{AtomicUsize, Ordering},
        },
    };

    #[test]
    fn desktop_runtime_is_private_while_product_data_is_shared() {
        let paths =
            desktop_paths_from_roots(Path::new("private"), Path::new("cache"), Path::new("local"));

        assert_eq!(paths.data, PathBuf::from("local").join("VidXP"));
        assert_eq!(
            paths.repository,
            PathBuf::from("local")
                .join("VidXP")
                .join("repositories")
                .join("default")
        );
        assert_eq!(
            paths.models,
            PathBuf::from("local").join("VidXP").join("models")
        );
        assert_eq!(paths.runtimes, PathBuf::from("private").join("runtimes"));
        assert_eq!(paths.python, PathBuf::from("private").join("python"));
        assert_eq!(
            paths.active_runtime,
            PathBuf::from("private").join("active-runtime.json")
        );
        assert_eq!(
            paths.activation_journal,
            PathBuf::from("private").join("activation-journal.json")
        );
    }

    #[test]
    fn managed_runtime_accepts_a_shared_posix_base_interpreter() {
        let root = std::env::temp_dir().join(format!(
            "vidxp-managed-runtime-identity-{}",
            std::process::id()
        ));
        let runtime = root.join("runtimes").join("profile");
        let launcher = runtime.join("bin").join("vidxp");
        fs::create_dir_all(launcher.parent().expect("launcher parent")).expect("runtime");
        fs::write(&launcher, b"launcher").expect("launcher");
        let identity = crate::target_profiles::RuntimeIdentity {
            python_executable: root
                .join("python")
                .join("cpython")
                .join("bin")
                .join("python3"),
            python_version: "3.13.5".into(),
            implementation: "CPython".into(),
            prefix: runtime.clone(),
            base_prefix: root.join("python").join("cpython"),
        };

        assert!(validate_managed_runtime_identity(&runtime, &launcher, &identity).is_ok());
        assert!(
            validate_managed_runtime_identity(&runtime, &root.join("other-vidxp"), &identity)
                .is_err()
        );
        let mut wrong_prefix = identity;
        wrong_prefix.prefix = root.join("other-environment");
        assert!(validate_managed_runtime_identity(&runtime, &launcher, &wrong_prefix).is_err());
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn managed_commands_discard_hostile_inherited_environment_and_restore_owned_roots() {
        let paths =
            desktop_paths_from_roots(Path::new("private"), Path::new("cache"), Path::new("local"));
        let environment = clean_environment_from(
            &paths,
            [
                ("PATH".into(), "safe-path".into()),
                ("VIDXP_DATA_DIR".into(), "attacker-data".into()),
                ("VIDXP_MODEL_CACHE".into(), "attacker-models".into()),
                ("PYTHONPATH".into(), "attacker-python".into()),
                ("VIRTUAL_ENV".into(), "attacker-venv".into()),
                ("PIP_INDEX_URL".into(), "attacker-index".into()),
                ("CONDA_PREFIX".into(), "attacker-conda".into()),
                ("UV_INDEX".into(), "attacker-uv".into()),
            ],
        )
        .into_iter()
        .collect::<std::collections::BTreeMap<_, _>>();

        assert_eq!(
            environment.get("PATH").map(String::as_str),
            Some("safe-path")
        );
        assert_eq!(
            environment.get("VIDXP_DATA_DIR").map(String::as_str),
            Some(paths.data.to_string_lossy().as_ref())
        );
        assert_eq!(
            environment.get("VIDXP_MODEL_CACHE").map(String::as_str),
            Some(paths.models.to_string_lossy().as_ref())
        );
        for rejected in [
            "PYTHONPATH",
            "VIRTUAL_ENV",
            "PIP_INDEX_URL",
            "CONDA_PREFIX",
            "UV_INDEX",
        ] {
            assert!(!environment.contains_key(rejected));
        }
    }

    #[test]
    fn interrupted_activation_never_recommits_after_rollback_begins() {
        assert_eq!(
            activation_recovery(&ActivationStage::Prepared, false),
            ActivationRecovery::RollBack
        );
        assert_eq!(
            activation_recovery(&ActivationStage::ProfileWritten, false),
            ActivationRecovery::RollBack
        );
        assert_eq!(
            activation_recovery(&ActivationStage::ProfileWritten, true),
            ActivationRecovery::Complete
        );
        assert_eq!(
            activation_recovery(&ActivationStage::Committed, false),
            ActivationRecovery::Complete
        );
        for stage in [ActivationStage::RollingBack, ActivationStage::RolledBack] {
            assert_eq!(
                activation_recovery(&stage, true),
                ActivationRecovery::RollBack
            );
        }
    }

    #[test]
    fn concurrent_target_transitions_return_a_stable_conflict() {
        let state = DesktopState::default();
        let first = TargetTransitionCoordinator::begin(&state, TransitionKind::Adopt)
            .expect("first transition");
        let conflict = TargetTransitionCoordinator::begin(&state, TransitionKind::Select)
            .err()
            .expect("conflict");

        assert_eq!(
            conflict.code,
            crate::target_profiles::TargetErrorCode::OperationConflict
        );
        drop(first);
        assert!(TargetTransitionCoordinator::begin(&state, TransitionKind::Select).is_ok());
    }

    #[test]
    fn shutdown_tracking_waits_for_probe_install_model_and_browser_operations() {
        let state = DesktopState::default();
        let probe = state.active_operations.register().expect("probe");
        let install = state.active_operations.register().expect("package install");
        let models = state
            .active_operations
            .register()
            .expect("model preparation");
        let browser = state.active_operations.register().expect("browser startup");
        assert!(
            !state
                .active_operations
                .wait_until_idle(std::time::Instant::now() + std::time::Duration::from_millis(20))
        );
        drop((probe, install, models, browser));
        assert!(
            state
                .active_operations
                .wait_until_idle(std::time::Instant::now() + std::time::Duration::from_secs(1))
        );
    }

    #[test]
    fn concurrent_cancellation_registration_preserves_the_shutdown_owner() {
        let state = DesktopState::default();
        let operation_a = super::OperationCancellationGuard::register(&state).expect("operation A");
        let token_a = operation_a.token();
        assert_eq!(
            super::OperationCancellationGuard::register(&state)
                .err()
                .as_deref(),
            Some("Another cancellable Desktop operation is already active.")
        );
        assert!(
            state
                .operation_cancellation
                .lock()
                .expect("cancellation slot")
                .as_ref()
                .is_some_and(|token| token.same(&token_a))
        );
        state.shutdown.cancel();
        super::cancel_active_operation(&state);
        assert!(state.shutdown.is_cancelled());
        assert!(token_a.is_cancelled());
        drop(operation_a);
        assert!(
            state
                .operation_cancellation
                .lock()
                .expect("cancellation slot")
                .is_none()
        );
        assert!(
            state
                .active_operations
                .wait_until_idle(std::time::Instant::now() + std::time::Duration::from_secs(1))
        );
    }

    fn worker_supervisor_fixture() -> (
        Arc<WorkerStopSupervisor>,
        Arc<AtomicUsize>,
        super::DesktopPaths,
    ) {
        let stops = Arc::new(AtomicUsize::new(0));
        let observed = stops.clone();
        let supervisor = Arc::new(WorkerStopSupervisor::with_stopper(Arc::new(
            move |_runtime, _paths, _deadline| {
                observed.fetch_add(1, Ordering::SeqCst);
            },
        )));
        let paths =
            desktop_paths_from_roots(Path::new("private"), Path::new("cache"), Path::new("local"));
        (supervisor, stops, paths)
    }

    #[test]
    fn worker_owner_stops_once_on_normal_completion() {
        let (supervisor, stops, paths) = worker_supervisor_fixture();
        let mut worker = supervisor
            .register(PathBuf::from("runtime"), paths)
            .expect("worker registration");
        worker.stop_before(std::time::Instant::now() + std::time::Duration::from_secs(1));
        drop(worker);
        assert_eq!(stops.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn worker_owner_stops_once_when_cancelled_or_failed() {
        for _outcome in ["cancelled", "failed"] {
            let (supervisor, stops, paths) = worker_supervisor_fixture();
            let worker = supervisor
                .register(PathBuf::from("runtime"), paths)
                .expect("worker registration");
            drop(worker);
            assert_eq!(stops.load(Ordering::SeqCst), 1);
        }
    }

    #[test]
    fn shutdown_claims_an_active_worker_and_prevents_duplicate_stop() {
        let (supervisor, stops, paths) = worker_supervisor_fixture();
        let worker = supervisor
            .register(PathBuf::from("runtime"), paths)
            .expect("worker registration");
        assert_eq!(
            supervisor
                .stop_active_before(std::time::Instant::now() + std::time::Duration::from_secs(1)),
            Some(PathBuf::from("runtime"))
        );
        drop(worker);
        assert_eq!(stops.load(Ordering::SeqCst), 1);
        assert_eq!(
            supervisor
                .stop_active_before(std::time::Instant::now() + std::time::Duration::from_secs(1)),
            Some(PathBuf::from("runtime"))
        );
        assert_eq!(stops.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn managed_draft_cancellation_is_scoped_and_rejected_while_applying() {
        let state = DesktopState::default();
        state.transition.lock().expect("transition").draft = Some(DraftRecord {
            draft: ManagedSetupDraft {
                id: "draft-current".into(),
                previous_profile_id: Some("local-1".into()),
            },
            phase: DraftPhase::Draft,
        });

        let stale = TargetTransitionCoordinator::cancel_draft(&state, "draft-stale")
            .expect_err("stale draft");
        assert_eq!(
            stale.code,
            crate::target_profiles::TargetErrorCode::DraftMismatch
        );

        let applying = TargetTransitionCoordinator::begin_apply(
            &state,
            "draft-current",
            TransitionKind::InstallRuntime,
        )
        .expect("apply");
        let conflict = TargetTransitionCoordinator::cancel_draft(&state, "draft-current")
            .expect_err("applying draft");
        assert_eq!(
            conflict.code,
            crate::target_profiles::TargetErrorCode::DraftApplying
        );
        drop(applying);
        TargetTransitionCoordinator::cancel_draft(&state, "draft-current")
            .expect("cancel settled draft");
        assert_eq!(
            state
                .transition
                .lock()
                .expect("transition")
                .draft
                .as_ref()
                .expect("draft")
                .phase,
            DraftPhase::Cancelled
        );
        let finished = TargetTransitionCoordinator::cancel_draft(&state, "draft-current")
            .expect_err("finished draft");
        assert_eq!(
            finished.code,
            crate::target_profiles::TargetErrorCode::DraftMismatch
        );
    }

    #[test]
    fn previous_runtime_snapshot_accepts_current_old_malformed_and_missing_pointers() {
        let root = std::env::temp_dir().join(format!(
            "vidxp-active-snapshot-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .expect("clock")
                .as_nanos()
        ));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(root.join("private")).expect("private directory");
        let paths = desktop_paths_from_roots(
            &root.join("private"),
            &root.join("cache"),
            &root.join("local"),
        );
        assert_eq!(read_active_runtime_snapshot(&paths).expect("missing"), None);

        for bytes in [
            br#"{"schema_version":2,"manifest_sha256":"current"}"#.as_slice(),
            br#"{"schema_version":2,"manifest_sha256":"old"}"#.as_slice(),
            br#"not-json"#.as_slice(),
        ] {
            fs::write(&paths.active_runtime, bytes).expect("pointer");
            assert_eq!(
                read_active_runtime_snapshot(&paths).expect("snapshot"),
                Some(bytes.to_vec())
            );
            restore_active_runtime(&paths, Some(bytes)).expect("restore");
            assert_eq!(fs::read(&paths.active_runtime).expect("restored"), bytes);
        }
        restore_active_runtime(&paths, None).expect("remove pointer");
        assert!(!paths.active_runtime.exists());
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn runtime_reconciliation_preserves_authorities_and_bounds_repeated_updates() {
        let root = std::env::temp_dir().join(format!(
            "vidxp-runtime-reconcile-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .expect("clock")
                .as_nanos()
        ));
        let paths = desktop_paths_from_roots(
            &root.join("private"),
            &root.join("cache"),
            &root.join("local"),
        );
        fs::create_dir_all(&paths.runtimes).expect("runtimes");
        let profile = |digest: char, generation: u8| {
            format!("{}-{generation}", digest.to_string().repeat(64))
        };
        let active_profile = profile('a', 3);
        let candidate_profile = profile('b', 4);
        let previous_profile = profile('c', 2);
        let obsolete_profile = profile('d', 1);
        let staging_profile = format!(".staging-{}-5-42", "e".repeat(64));
        for name in [
            &active_profile,
            &candidate_profile,
            &previous_profile,
            &obsolete_profile,
            &staging_profile,
            "external-runtime",
        ] {
            fs::create_dir_all(paths.runtimes.join(name)).expect("runtime directory");
            fs::write(paths.runtimes.join(name).join("payload"), b"runtime").expect("payload");
        }
        let runtime = |profile: String| ActiveRuntime {
            schema_version: 2,
            manifest_sha256: manifest_digest(),
            profile,
            package_version: "0.4.0-b".into(),
            capabilities: vec!["scene".into()],
            surfaces: vec!["browser".into()],
            model_directory: paths.models.clone(),
        };
        let active = runtime(active_profile.clone());
        write_active_runtime(&paths, &active).expect("active pointer");
        let journal = ActivationJournal {
            schema_version: 2,
            stage: ActivationStage::Prepared,
            previous_active_bytes: Some(
                serde_json::to_vec(&runtime(previous_profile.clone())).expect("previous"),
            ),
            previous_targets: crate::target_profiles::TargetState::default(),
            candidate_active: runtime(candidate_profile.clone()),
            candidate_targets: crate::target_profiles::TargetState::default(),
        };
        write_activation_journal(&paths, &journal).expect("journal");

        let report = reconcile_managed_runtime_storage(&paths);
        assert_eq!(report.removed_directories, 2);
        assert!(report.reclaimed_bytes > 0);
        for retained in [
            &active_profile,
            &candidate_profile,
            &previous_profile,
            "external-runtime",
        ] {
            assert!(
                paths.runtimes.join(retained).exists(),
                "retained {retained}"
            );
        }
        assert!(!paths.runtimes.join(obsolete_profile).exists());
        assert!(!paths.runtimes.join(staging_profile).exists());

        fs::remove_file(&paths.activation_journal).expect("clear journal");
        let report = reconcile_managed_runtime_storage(&paths);
        assert_eq!(report.removed_directories, 2);
        assert!(paths.runtimes.join(active_profile).exists());
        assert!(paths.runtimes.join("external-runtime").exists());
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn corrupt_active_pointer_preserves_unidentified_finalized_runtimes() {
        let root = std::env::temp_dir().join(format!(
            "vidxp-corrupt-pointer-reconcile-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .expect("clock")
                .as_nanos()
        ));
        let paths = desktop_paths_from_roots(
            &root.join("private"),
            &root.join("cache"),
            &root.join("local"),
        );
        fs::create_dir_all(&paths.runtimes).expect("runtimes");
        let finalized = format!("{}-1", "a".repeat(64));
        fs::create_dir_all(paths.runtimes.join(&finalized)).expect("finalized runtime");
        fs::write(&paths.active_runtime, b"not-json").expect("corrupt pointer");

        let report = reconcile_managed_runtime_storage(&paths);
        assert_eq!(report.removed_directories, 0);
        assert!(paths.runtimes.join(finalized).exists());
        let _ = fs::remove_dir_all(root);
    }

    fn status_fixture(model_directory: &str) -> ActiveRuntime {
        ActiveRuntime {
            schema_version: 2,
            manifest_sha256: manifest_digest(),
            profile: "a".repeat(64) + "-1",
            package_version: "0.4.0-b".into(),
            capabilities: vec!["scene".into()],
            surfaces: vec!["browser".into()],
            model_directory: PathBuf::from(model_directory),
        }
    }

    #[test]
    fn missing_ffmpeg_preserves_the_managed_runtime_configuration() {
        let status = configured_runtime_status(
            status_fixture("custom-models"),
            vec!["FFmpeg was not found.".into()],
        );
        assert_eq!(status.state, super::RuntimeState::Broken);
        assert_eq!(status.capabilities, ["scene"]);
        assert_eq!(status.surfaces, ["browser"]);
        assert_eq!(status.model_directory, "custom-models");
        assert!(status.runtime_profile.is_some());
    }

    #[test]
    fn missing_encoder_and_damaged_runtime_preserve_custom_model_storage() {
        for problem in [
            "FFmpeg does not provide required encoder libx264.",
            "The active runtime executable is damaged.",
        ] {
            let status =
                configured_runtime_status(status_fixture("D:\\VidXP models"), vec![problem.into()]);
            assert_eq!(status.state, super::RuntimeState::Broken);
            assert_eq!(status.capabilities, ["scene"]);
            assert_eq!(status.surfaces, ["browser"]);
            assert_eq!(status.model_directory, "D:\\VidXP models");
        }
    }

    #[test]
    fn repeated_browser_open_requests_are_coalesced() {
        let active = std::sync::atomic::AtomicBool::new(false);
        assert!(claim_browser_open(&active));
        assert!(!claim_browser_open(&active));
    }

    #[test]
    fn capability_selection_is_sorted_and_rejects_unknown_values() {
        let manifest = manifest().expect("manifest");
        let selected = selected_capabilities(
            &manifest,
            &["scene".into(), "dialogue".into(), "scene".into()],
        )
        .expect("selection");

        assert_eq!(selected, ["dialogue", "scene"]);
        assert!(selected_capabilities(&manifest, &["other".into()]).is_err());
    }

    #[test]
    fn runtime_constraints_digest_is_independent_of_checkout_line_endings() {
        let canonical = normalized_runtime_constraints();
        let windows = canonical
            .split(|byte| *byte == b'\n')
            .collect::<Vec<_>>()
            .join(&b"\r\n"[..]);

        assert_eq!(
            normalize_line_endings(&windows).as_ref(),
            canonical.as_ref()
        );
    }

    #[test]
    fn package_specification_has_one_sorted_extra_set() {
        let manifest = manifest().expect("manifest");
        let version = &manifest.package_version;

        assert_eq!(
            base_package_specification(&manifest),
            format!("vidxp=={version}")
        );
        assert_eq!(
            package_specification(
                &manifest,
                &["scene".into(), "dialogue".into()],
                &["browser".into()],
            ),
            format!("vidxp[dialogue,frontend,scene]=={version}")
        );
        assert_eq!(
            package_specification(&manifest, &["scene".into()], &[]),
            format!("vidxp[scene]=={version}")
        );
        assert_eq!(
            package_specification(
                &manifest,
                &["actor".into(), "dialogue".into(), "scene".into()],
                &["worker".into()],
            ),
            format!("vidxp[local-worker]=={version}")
        );
        assert_eq!(
            selected_surfaces(&manifest, &["browser".into(), "browser".into()])
                .expect("surface selection"),
            ["browser"]
        );
        assert!(selected_surfaces(&manifest, &["unknown".into()]).is_err());
    }

    #[test]
    fn external_install_recreates_the_reported_version_with_the_selected_features() {
        let manifest = manifest().expect("manifest");
        let arguments = external_installation_arguments(
            &manifest,
            &["scene".into()],
            &["server".into(), "mcp".into()],
            "3.14.6",
            "0.4.0-b.1",
        )
        .expect("external surface arguments");

        assert_eq!(&arguments[..3], ["tool", "install", "--force"]);
        assert!(
            arguments
                .windows(2)
                .any(|items| items == ["--python", "3.14.6"])
        );
        assert_eq!(
            arguments.last().expect("package"),
            "vidxp[mcp,scene,server]==0.4.0-b.1"
        );
        assert!(
            external_installation_arguments(
                &manifest,
                &[],
                &["mcp".into()],
                "3.14.6",
                "0.4.0 @ https://example.invalid/package.whl",
            )
            .is_err()
        );
    }

    #[test]
    fn external_install_updates_old_contracts_and_preserves_compatible_versions() {
        let manifest = manifest().expect("manifest");
        let supported = crate::target_profiles::SUPPORTED_PROBE_PROTOCOL_VERSION;

        assert_eq!(
            external_installation_version(&manifest, false, supported - 1, "older-release")
                .expect("older contract"),
            manifest.package_version
        );
        assert_eq!(
            external_installation_version(&manifest, true, supported, "older-release")
                .expect("missing management contract"),
            manifest.package_version
        );
        assert_eq!(
            external_installation_version(&manifest, false, supported, "compatible-release")
                .expect("compatible contract"),
            "compatible-release"
        );
        assert!(
            external_installation_version(&manifest, false, supported + 1, "newer-release")
                .is_err()
        );
    }

    #[test]
    fn managed_install_uses_the_bundled_package_and_public_dependency_index() {
        let manifest = manifest().expect("manifest");
        let python = Path::new("managed-python");
        let constraints = Path::new("staging").join(RUNTIME_CONSTRAINTS_FILE_NAME);
        let wheel = Path::new("staging").join(RUNTIME_PACKAGE_WHEEL_NAME);
        let selected_package_index = manifest.dependency_index.as_str();
        let acquisition = package_acquisition_arguments(&manifest, python, &wheel);
        let dependency_installation = dependency_installation_invocation(
            &manifest,
            &["scene".into()],
            &[],
            python,
            &constraints,
            true,
        )
        .expect("dependency installation");
        let dependencies = dependency_installation.arguments;

        assert_eq!(selected_package_index, "https://pypi.org/simple");
        assert_eq!(manifest.dependency_index, "https://pypi.org/simple");
        assert!(acquisition.iter().any(|item| item == "--no-deps"));
        assert!(acquisition.iter().any(|item| item == "--no-index"));
        assert!(
            acquisition
                .windows(2)
                .any(|items| items == ["--find-links", "staging"])
        );
        assert_eq!(
            acquisition.last(),
            Some(&base_package_specification(&manifest))
        );
        assert!(
            !acquisition
                .iter()
                .any(|item| item == selected_package_index)
        );
        assert!(!dependencies.iter().any(|item| item == "--no-deps"));
        assert!(
            dependencies
                .iter()
                .any(|item| item == &manifest.dependency_index)
        );
        assert!(dependencies.iter().any(|item| item == "--torch-backend"));
        assert!(
            dependencies
                .windows(2)
                .any(|items| items == ["--constraints", "runtime-constraints.txt"])
        );
        assert!(
            dependencies
                .windows(2)
                .any(|items| items == ["--find-links", "."])
        );
        assert_eq!(
            dependencies.last(),
            Some(&package_specification(&manifest, &["scene".into()], &[]))
        );
        assert_eq!(
            dependency_installation.working_directory,
            Path::new("staging")
        );
    }

    #[test]
    fn dependency_constraints_with_spaced_parent_use_a_local_file_name() {
        let manifest = manifest().expect("manifest");
        let constraints = Path::new("Users")
            .join("grayhat")
            .join("Library")
            .join("Application Support")
            .join("dev.grayhat.vidxp")
            .join("runtimes")
            .join("staging")
            .join(RUNTIME_CONSTRAINTS_FILE_NAME);
        let invocation = dependency_installation_invocation(
            &manifest,
            &["scene".into()],
            &[],
            Path::new("managed-python"),
            &constraints,
            false,
        )
        .expect("dependency installation");

        assert_eq!(
            invocation
                .arguments
                .windows(2)
                .find(|items| items[0] == "--constraints"),
            Some(&["--constraints".into(), RUNTIME_CONSTRAINTS_FILE_NAME.into()][..])
        );
        assert_eq!(invocation.working_directory, constraints.parent().unwrap());
    }

    #[test]
    fn capability_commands_pass_one_comma_separated_option() {
        let manifest = manifest().expect("manifest");

        assert_eq!(
            capability_command_arguments(&manifest, "doctor", &["dialogue".into(), "scene".into()]),
            ["doctor", "--json", "--modalities", "dialogue,scene"]
        );
        assert_eq!(
            capability_command_arguments(&manifest, "prepare", &["scene".into()]),
            ["prepare", "--json", "--modalities", "scene", "--yes"]
        );
    }

    #[test]
    fn capability_commands_pass_an_explicit_empty_option() {
        let manifest = manifest().expect("manifest");

        assert_eq!(
            capability_command_arguments(&manifest, "doctor", &[]),
            ["doctor", "--json", "--modalities", ""]
        );
    }

    #[test]
    fn ffmpeg_encoder_check_matches_complete_encoder_names() {
        let encoders = " V....D libx264 H.264\n A....D aac AAC";

        assert!(!required_encoder_missing(encoders, "libx264"));
        assert!(!required_encoder_missing(encoders, "aac"));
        assert!(required_encoder_missing(encoders, "libx265"));
    }

    #[test]
    fn desktop_ui_service_is_headless_so_only_the_desktop_opens_the_browser() {
        let mut command = Command::new("vidxp");

        configure_ui_service_command(
            &mut command,
            Path::new("repository"),
            43123,
            Path::new("readiness.json"),
            "nonce",
            false,
        );

        assert!(command.get_envs().any(|(key, value)| {
            key == OsStr::new("STREAMLIT_SERVER_HEADLESS") && value == Some(OsStr::new("true"))
        }));
        assert!(command.get_envs().any(|(key, value)| {
            key == OsStr::new("VIDXP_DESKTOP_READINESS_NONCE") && value == Some(OsStr::new("nonce"))
        }));
        assert_eq!(
            command
                .get_args()
                .map(|argument| argument.to_string_lossy().into_owned())
                .collect::<Vec<_>>(),
            [
                "--index-dir",
                "repository",
                "ui",
                "--host",
                "127.0.0.1",
                "--port",
                "43123",
            ]
        );

        let mut shared = Command::new("vidxp");
        configure_ui_service_command(
            &mut shared,
            Path::new("repository"),
            43124,
            Path::new("shared-readiness.json"),
            "shared-nonce",
            true,
        );
        assert!(shared.get_envs().any(|(key, value)| {
            key == OsStr::new("VIDXP_DESKTOP_UI_SHARED") && value == Some(OsStr::new("1"))
        }));
        assert_eq!(
            shared
                .get_args()
                .map(|argument| argument.to_string_lossy().into_owned())
                .collect::<Vec<_>>(),
            [
                "--index-dir",
                "repository",
                "ui",
                "--share",
                "--port",
                "43124"
            ]
        );
    }

    #[test]
    fn startup_and_single_instance_activation_manage_without_opening_the_browser() {
        assert_eq!(
            action_for_activation(DesktopActivation::Startup),
            Some(DesktopAction::Manage)
        );
        assert_eq!(
            action_for_activation(DesktopActivation::SingleInstance),
            Some(DesktopAction::Manage)
        );
    }

    #[test]
    fn tray_service_actions_are_unambiguous() {
        assert_eq!(
            action_for_activation(DesktopActivation::Tray("manage")),
            Some(DesktopAction::Manage)
        );
        assert_eq!(
            action_for_activation(DesktopActivation::Tray("open")),
            Some(DesktopAction::OpenBrowser)
        );
        for (id, expected) in [
            ("share-browser", DesktopAction::ShareBrowser),
            ("stop-browser", DesktopAction::StopBrowser),
            ("start-worker", DesktopAction::StartWorker),
            ("stop-worker", DesktopAction::StopWorker),
            ("start-server", DesktopAction::StartServer),
            ("share-server", DesktopAction::ShareServer),
            ("stop-server", DesktopAction::StopServer),
        ] {
            assert_eq!(
                action_for_activation(DesktopActivation::Tray(id)),
                Some(expected)
            );
        }
        assert_eq!(
            action_for_activation(DesktopActivation::Tray("quit")),
            Some(DesktopAction::Quit)
        );
        assert_eq!(
            action_for_activation(DesktopActivation::Tray("other")),
            None
        );
    }

    #[test]
    fn tray_service_labels_are_compact_and_distinguish_availability() {
        let browser = super::BrowserServiceStatus {
            state: "ready",
            running: true,
            shared: true,
            port: Some(43124),
            local_url: Some("http://127.0.0.1:43124".into()),
            network_url: Some("http://192.168.1.20:43124".into()),
            detail: String::new(),
        };
        let server = super::LocalServerStatus {
            state: "ready",
            running: true,
            shared: false,
            port: Some(43125),
            origin: Some("http://127.0.0.1:43125".into()),
            health_url: Some("http://127.0.0.1:43125/health".into()),
            mcp_url: Some("http://127.0.0.1:43125/mcp".into()),
            bearer_token: None,
            detail: String::new(),
        };

        assert_eq!(
            super::tray_browser_label(&browser, true, true, true, true),
            "Browser · Shared"
        );
        assert_eq!(
            super::tray_server_label(&server, true, true, true, true),
            "App integration · Private"
        );
        assert_eq!(
            super::tray_browser_label(&browser, true, false, true, true),
            "Browser · Not installed"
        );
        assert_eq!(
            super::tray_server_label(&server, true, true, false, true),
            "App integration · Unavailable"
        );
        assert_eq!(
            super::tray_browser_label(&browser, true, true, true, false),
            "Browser · Status unknown"
        );
        assert_eq!(
            super::tray_installation_label(None, 0),
            "No installation selected"
        );
    }

    #[test]
    fn tray_worker_labels_report_actual_state() {
        let running = Ok(super::LocalWorkerStatus {
            running: true,
            detail: String::new(),
        });
        let stopped = Ok(super::LocalWorkerStatus {
            running: false,
            detail: String::new(),
        });
        let unavailable = Err("timed out".into());

        assert_eq!(
            super::tray_worker_label(Some(&running), true, true, true),
            "Processing · On"
        );
        assert_eq!(
            super::tray_worker_label(Some(&stopped), true, true, true),
            "Processing · Off"
        );
        assert_eq!(
            super::tray_worker_label(Some(&unavailable), true, true, true),
            "Processing · Status unknown"
        );
        assert_eq!(
            super::tray_worker_label(None, true, false, true),
            "Processing · Not installed"
        );
    }

    #[test]
    fn repeated_browser_actions_reuse_one_service_and_target_changes_replace_it() {
        assert_eq!(
            ui_process_action(true, "selected", "selected"),
            UiProcessAction::Reuse
        );
        assert_eq!(
            ui_process_action(true, "previous", "selected"),
            UiProcessAction::Replace
        );
        assert_eq!(
            ui_process_action(false, "selected", "selected"),
            UiProcessAction::Start
        );
    }

    #[test]
    fn closing_a_configured_desktop_hides_it_and_manage_can_restore_it() {
        assert_eq!(close_action(true), DesktopCloseAction::HideToTray);
        assert_eq!(
            action_for_activation(DesktopActivation::Tray("manage")),
            Some(DesktopAction::Manage)
        );
        assert_eq!(close_action(false), DesktopCloseAction::Quit);
    }

    #[test]
    fn package_manager_command_is_presented_as_copyable_text() {
        assert_eq!(
            display_command(&[
                "winget".into(),
                "install".into(),
                "--id".into(),
                "Gyan.FFmpeg".into(),
            ]),
            "winget install --id Gyan.FFmpeg"
        );
    }

    #[test]
    fn populated_model_inventory_reports_totals_and_known_cache_conventions() {
        let root =
            std::env::temp_dir().join(format!("vidxp-model-inventory-{}", std::process::id()));
        let siglip = root
            .join("models--google--siglip2-base-patch16-224")
            .join("snapshots")
            .join("75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2");
        let opencv = root.join("opencv-zoo");
        fs::create_dir_all(&siglip).expect("siglip directory");
        fs::create_dir_all(&opencv).expect("opencv directory");
        fs::write(siglip.join("model.safetensors"), [0_u8; 7]).expect("model file");
        fs::write(opencv.join("face_detection_yunet_2026may.onnx"), [0_u8; 5])
            .expect("artifact file");

        let inventory = inventory_model_directory(&root);

        assert!(inventory.exists);
        assert!(inventory.readable);
        assert_eq!(inventory.file_count, 2);
        assert_eq!(inventory.total_bytes, 12);
        assert_eq!(
            inventory
                .recognized_models
                .iter()
                .map(|model| model.label.as_str())
                .collect::<Vec<_>>(),
            ["google/siglip2-base-patch16-224", "yunet"]
        );
        assert!(inventory.verification_required);
        assert!(inventory.detail.contains("verification required"));
        fs::remove_dir_all(root).expect("remove test inventory");
    }

    #[test]
    fn empty_and_unreadable_model_locations_are_typed_states() {
        let root = std::env::temp_dir().join(format!(
            "vidxp-empty-model-inventory-{}",
            std::process::id()
        ));
        fs::create_dir_all(&root).expect("empty directory");
        let empty = inventory_model_directory(&root);
        assert!(empty.empty);
        assert!(empty.readable);
        assert!(!empty.verification_required);

        let file = root.join("not-a-directory");
        fs::write(&file, b"x").expect("file location");
        let unreadable = inventory_model_directory(&file);
        assert!(unreadable.exists);
        assert!(!unreadable.readable);
        assert!(unreadable.detail.contains("not a readable directory"));
        fs::remove_dir_all(root).expect("remove test inventory");
    }
}
