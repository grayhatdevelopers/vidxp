use std::{
    borrow::Cow,
    collections::{BTreeMap, BTreeSet},
    env, fs,
    io::{self, Write},
    net::TcpListener,
    path::{Path, PathBuf},
    process::Command,
    sync::{
        Arc, Mutex, OnceLock,
        atomic::{AtomicBool, AtomicU64, Ordering},
    },
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

use atomic_write_file::AtomicWriteFile;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use tauri::{
    AppHandle, Manager, RunEvent, WindowEvent,
    menu::{Menu, MenuItem},
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

const RUNTIME_MANIFEST_BYTES: &[u8] = include_bytes!("../../runtime-manifest.json");
const RUNTIME_CONSTRAINTS_BYTES: &[u8] = include_bytes!("../../runtime-constraints.txt");
const MODEL_CACHE_CATALOG_BYTES: &[u8] = include_bytes!("../../model-cache-catalog.json");
const PRODUCT_DATA_DIRECTORY_NAME: &str = "VidXP";
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
    url: String,
    profile_id: String,
}

struct DesktopState {
    ui_process: Mutex<Option<ManagedUi>>,
    worker_stop: Arc<WorkerStopSupervisor>,
    operation_cancellation: Arc<Mutex<Option<background_process::CancellationToken>>>,
    transition: Arc<Mutex<TransitionState>>,
    browser_open_active: AtomicBool,
    shutdown: background_process::CancellationToken,
    shutdown_started: AtomicBool,
    active_operations: Arc<ActiveOperations>,
}

impl Default for DesktopState {
    fn default() -> Self {
        Self {
            ui_process: Mutex::new(None),
            worker_stop: Arc::new(WorkerStopSupervisor::default()),
            operation_cancellation: Arc::new(Mutex::new(None)),
            transition: Arc::new(Mutex::new(TransitionState::default())),
            browser_open_active: AtomicBool::new(false),
            shutdown: background_process::CancellationToken::default(),
            shutdown_started: AtomicBool::new(false),
            active_operations: Arc::new(ActiveOperations::default()),
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
    let extras: BTreeSet<_> = manifest
        .surfaces
        .iter()
        .filter(|(name, _)| surfaces.contains(name))
        .map(|(_, surface)| surface.extra.clone())
        .chain(
            capabilities
                .iter()
                .map(|name| manifest.capabilities[name].extra.clone()),
        )
        .collect();
    format!(
        "{}[{}]=={}",
        manifest.package_name,
        extras.into_iter().collect::<Vec<_>>().join(","),
        manifest.package_version
    )
}

fn base_package_specification(manifest: &RuntimeManifest) -> String {
    format!("{}=={}", manifest.package_name, manifest.package_version)
}

fn package_acquisition_arguments(manifest: &RuntimeManifest, python: &Path) -> Vec<String> {
    vec![
        "pip".into(),
        "install".into(),
        "--python".into(),
        python.to_string_lossy().into_owned(),
        "--no-config".into(),
        "--no-deps".into(),
        "--default-index".into(),
        manifest.dependency_index.clone(),
        "--index-strategy".into(),
        "first-index".into(),
        base_package_specification(manifest),
    ]
}

fn dependency_installation_arguments(
    manifest: &RuntimeManifest,
    capabilities: &[String],
    surfaces: &[String],
    python: &Path,
    constraints: &Path,
    cpu_torch: bool,
) -> Vec<String> {
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
        "--constraints".into(),
        constraints.to_string_lossy().into_owned(),
    ];
    if cpu_torch {
        arguments.extend(["--torch-backend".into(), "cpu".into()]);
    }
    arguments.push(package_specification(manifest, capabilities, surfaces));
    arguments
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
    vec![
        operation.into(),
        "--json".into(),
        "--modalities".into(),
        modalities,
    ]
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
    if !path_is_confined(&validated.executable, &runtime)
        || !path_is_confined(&validated.runtime.python_executable, &runtime)
        || !same_path(&validated.runtime.prefix, &runtime)
    {
        return Err(managed_probe_error(
            "The managed probe reported a launcher or Python runtime outside the active Desktop-owned environment.",
        ));
    }
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
    let detail = if stderr.is_empty() { stdout } else { stderr };
    Err(format!("{operation} failed ({}): {detail}", output.status))
}

async fn uv_output(
    app: &AppHandle,
    paths: &DesktopPaths,
    arguments: Vec<String>,
    cancellation: background_process::CancellationToken,
    operation: &str,
) -> Result<(), String> {
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
    supervised_output(command, cancellation, operation).await?;
    Ok(())
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
    tauri::async_runtime::spawn_blocking(move || {
        let _transition = transition;
        match target_profiles::selected_profile(&app) {
            Ok(profile) => {
                if profile.kind == target_profiles::TargetKind::Managed {
                    let validation = (|| {
                        let paths = desktop_paths(&app).map_err(|message| {
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
                    let _ = target_profiles::persist_selected_validation(&app, validation);
                } else {
                    let _ = target_profiles::validated_selected_profile_with_cancellation(
                        &app,
                        &desktop_version,
                        Some(&cancellation),
                    );
                }
            }
            Err(error)
                if error.code == target_profiles::TargetErrorCode::SelectedProfileMissing => {}
            Err(error) => return Err(error),
        }
        target_profiles::current_state(&app)
    })
    .await
    .map_err(|error| target_profiles::TargetError {
        code: target_profiles::TargetErrorCode::ValidationRequired,
        message: format!("Target revalidation stopped unexpectedly: {error}"),
    })?
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
    tauri::async_runtime::spawn_blocking(move || {
        let _transition = transition;
        let canonical =
            fs::canonicalize(Path::new(&executable)).unwrap_or_else(|_| PathBuf::from(&executable));
        let validated = target_profiles::validate_executable_using(
            &canonical,
            &desktop_version,
            Some(&cancellation),
            |path| Command::new(path),
        )?;
        let setup = target_profiles::adopt_validated(&app, validated, display_name)?;
        stop_ui_process(&app.state::<DesktopState>());
        Ok(setup)
    })
    .await
    .map_err(|error| {
        transition_error(
            target_profiles::TargetErrorCode::ValidationRequired,
            format!("Target adoption stopped unexpectedly: {error}"),
        )
    })?
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
    tauri::async_runtime::spawn_blocking(move || {
        let _transition = transition;
        let candidate = target_profiles::current_state(&app)?
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
            let paths = desktop_paths(&app).map_err(|message| {
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
            target_profiles::select_validated_profile(&app, &profile_id, validated)?
        } else {
            target_profiles::select_profile(&app, &profile_id, &desktop_version)?
        };
        stop_ui_process(&app.state::<DesktopState>());
        Ok(setup)
    })
    .await
    .map_err(|error| {
        transition_error(
            target_profiles::TargetErrorCode::ValidationRequired,
            format!("Target selection stopped unexpectedly: {error}"),
        )
    })?
}

#[tauri::command]
async fn delete_target_profile(
    app: AppHandle,
    state: tauri::State<'_, DesktopState>,
    profile_id: String,
) -> Result<target_profiles::TargetState, target_profiles::TargetError> {
    let _active = track_target_operation(&state)?;
    let transition = TargetTransitionCoordinator::begin(&state, TransitionKind::Delete)?;
    tauri::async_runtime::spawn_blocking(move || {
        let _transition = transition;
        let selected = target_profiles::current_state(&app)?.selected_profile_id;
        let result = target_profiles::delete_profile(&app, &profile_id)?;
        if selected.as_deref() == Some(&profile_id) {
            stop_ui_process(&app.state::<DesktopState>());
        }
        Ok(result)
    })
    .await
    .map_err(|error| {
        transition_error(
            target_profiles::TargetErrorCode::ValidationRequired,
            format!("Target deletion stopped unexpectedly: {error}"),
        )
    })?
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
    let staging_name = format!(".staging-{profile_hash}-{timestamp}-{}", std::process::id());
    let staging = paths.runtimes.join(&staging_name);
    let constraints = staging.join("runtime-constraints.txt");

    let install_result = async {
        uv_output(
            &app,
            &paths,
            vec![
                "venv".into(),
                staging.to_string_lossy().into_owned(),
                "--python".into(),
                manifest.python_version.clone(),
                "--managed-python".into(),
                "--no-config".into(),
            ],
            cancellation.token(),
            "Managed Python setup",
        )
        .await?;

        let constraints_path = constraints.clone();
        tauri::async_runtime::spawn_blocking(move || {
            fs::write(&constraints_path, normalized_runtime_constraints().as_ref())
                .map_err(|error| format!("Could not write runtime constraints: {error}"))
        })
        .await
        .map_err(|error| format!("Runtime constraint staging stopped unexpectedly: {error}"))??;

        uv_output(
            &app,
            &paths,
            package_acquisition_arguments(&manifest, &executable(&staging, "python")),
            cancellation.token(),
            "VidXP package acquisition",
        )
        .await?;

        uv_output(
            &app,
            &paths,
            dependency_installation_arguments(
                &manifest,
                &capabilities,
                &surfaces,
                &executable(&staging, "python"),
                &constraints,
                !cfg!(target_os = "macos"),
            ),
            cancellation.token(),
            "VidXP package installation",
        )
        .await?;

        run_vidxp_supervised(
            &staging,
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

        let doctor_arguments = capability_command_arguments(&manifest, "doctor", &capabilities);
        run_vidxp_supervised(
            &staging,
            &paths,
            &doctor_arguments,
            cancellation.token(),
            "VidXP dependency validation",
        )
        .await?;

        if request.prepare_models {
            let prepare_arguments =
                capability_command_arguments(&manifest, "prepare", &capabilities);
            let mut worker = state.worker_stop.register(staging.clone(), paths.clone())?;
            let preparation = run_vidxp_supervised(
                &staging,
                &paths,
                &prepare_arguments,
                cancellation.token(),
                "VidXP model preparation",
            )
            .await;
            worker.stop_before(Instant::now() + Duration::from_secs(5));
            preparation?;
        }

        Ok::<(), String>(())
    }
    .await;
    if let Err(error) = install_result {
        let failed_staging = staging.clone();
        let cleanup_error = tauri::async_runtime::spawn_blocking(move || {
            if failed_staging.exists() {
                fs::remove_dir_all(&failed_staging).err()
            } else {
                None
            }
        })
        .await
        .map_err(|join| format!("{error}. Staged-runtime cleanup stopped unexpectedly: {join}"))?;
        return Err(match cleanup_error {
            Some(cleanup_error) => format!(
                "{error}. The previous active runtime was not changed. VidXP could not remove the failed staged runtime at {}: {cleanup_error}",
                staging.display()
            ),
            None => format!(
                "{error}. The previous active runtime was not changed, and the failed staged runtime was removed."
            ),
        });
    }

    let profile = format!("{profile_hash}-{timestamp}");
    let runtime = paths.runtimes.join(&profile);
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
    let activation_paths = paths;
    let activation_manifest_version = manifest.desktop_version.clone();
    let activation = tauri::async_runtime::spawn_blocking(move || {
        let previous_active_bytes = read_active_runtime_snapshot(&activation_paths)?;
        let previous_targets =
            target_profiles::current_state(&activation_app).map_err(|error| error.to_string())?;
        if let Err(error) = fs::rename(&staging, &runtime) {
            let cleanup = fs::remove_dir_all(&staging);
            return Err(match cleanup {
                Ok(()) => format!("Could not finalize the validated runtime: {error}"),
                Err(cleanup) => format!(
                    "Could not finalize the validated runtime: {error}. The staging directory at {} could not be removed: {cleanup}",
                    staging.display()
                ),
            });
        }

        let projection = managed_runtime_projection_for(&activation_paths, &active);
        let validated = validate_managed_projection(
            &activation_paths,
            &projection,
            &activation_manifest_version,
            Some(&cancellation.token()),
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
    stop_ui_process(&state);
    transition.commit_draft();

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

fn start_ui(app: &AppHandle, state: &DesktopState) -> Result<String, String> {
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
            UiProcessAction::Reuse => return Ok(ui.url.clone()),
            UiProcessAction::Replace => {
                ui.process.terminate_and_reap();
            }
            UiProcessAction::Start => {}
        }
        *active_process = None;
    }

    let listener = TcpListener::bind(("127.0.0.1", 0))
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

    let mut command = match profile.kind {
        target_profiles::TargetKind::Managed => {
            let active = active_runtime(&paths)?;
            if profile.managed_runtime_profile.as_deref() != Some(active.profile.as_str()) {
                return Err(
                    "The selected managed target no longer matches the active desktop runtime."
                        .into(),
                );
            }
            configured_command(&profile.executable, &paths)
        }
        target_profiles::TargetKind::ExistingLocal => Command::new(&profile.executable),
    };
    configure_ui_service_command(
        &mut command,
        &profile.repository_root,
        port,
        &readiness_file,
        &nonce,
    );
    let mut process = background_process::spawn_service(command)
        .map_err(|error| format!("Could not start the VidXP interface: {}", error.detail))?;
    browser_readiness::wait_for_browser_readiness(
        &mut process,
        &readiness_file,
        &nonce,
        port,
        Instant::now() + Duration::from_secs(30),
        &state.shutdown,
    )?;
    let url = format!("http://127.0.0.1:{port}");
    *active_process = Some(ManagedUi {
        process,
        url: url.clone(),
        profile_id: profile.id.clone(),
    });
    Ok(url)
}

fn stop_ui_process(state: &DesktopState) {
    let Ok(mut active) = state.ui_process.lock() else {
        return;
    };
    if let Some(mut ui) = active.take() {
        ui.process.terminate_and_reap();
    }
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
) {
    command
        // The desktop owns the one intentional browser open after readiness. Without
        // headless mode Streamlit also opens the URL, producing duplicate tabs and
        // potentially visible launcher consoles on Windows.
        .env("STREAMLIT_SERVER_HEADLESS", "true")
        .env("VIDXP_DESKTOP_READINESS_FILE", readiness_file)
        .env("VIDXP_DESKTOP_READINESS_NONCE", nonce)
        .env("VIDXP_DESKTOP_UI_PORT", port.to_string())
        .arg("--index-dir")
        .arg(repository_root)
        .args(["ui", "--host", "127.0.0.1", "--port", &port.to_string()]);
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
    let worker_app = app.clone();
    let url = tauri::async_runtime::spawn_blocking(move || {
        let _transition = transition;
        let state = worker_app.state::<DesktopState>();
        start_ui(&worker_app, &state)
    })
    .await
    .map_err(|error| format!("VidXP interface startup stopped unexpectedly: {error}"))??;
    app.opener()
        .open_url(&url, None::<&str>)
        .map_err(|error| format!("Could not open VidXP in the default browser: {error}"))?;
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

fn perform_desktop_action(app: &AppHandle, action: DesktopAction) {
    match action {
        DesktopAction::Manage => show_main_window(app),
        DesktopAction::OpenBrowser => open_browser_or_show_manager(app),
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
    let open = MenuItem::with_id(app, "open", "Open VidXP", true, None::<&str>)?;
    let manage = MenuItem::with_id(app, "manage", "Manage VidXP", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit VidXP", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open, &manage, &quit])?;
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
            launch_ui
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
        ManagedSetupDraft, TargetTransitionCoordinator, TransitionKind, UiProcessAction,
        WorkerStopSupervisor, action_for_activation, activation_recovery,
        base_package_specification, capability_command_arguments, claim_browser_open,
        clean_environment_from, close_action, configure_ui_service_command,
        configured_runtime_status, dependency_installation_arguments, desktop_paths_from_roots,
        display_command, inventory_model_directory, manifest, manifest_digest,
        normalize_line_endings, normalized_runtime_constraints, package_acquisition_arguments,
        package_specification, read_active_runtime_snapshot, reconcile_managed_runtime_storage,
        required_encoder_missing, restore_active_runtime, selected_capabilities, selected_surfaces,
        ui_process_action, write_activation_journal, write_active_runtime,
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
            selected_surfaces(&manifest, &["browser".into(), "browser".into()])
                .expect("surface selection"),
            ["browser"]
        );
        assert!(selected_surfaces(&manifest, &["unknown".into()]).is_err());
    }

    #[test]
    fn package_and_dependencies_use_channel_specific_indexes() {
        let manifest = manifest().expect("manifest");
        let python = Path::new("managed-python");
        let constraints = Path::new("runtime-constraints.txt");
        let selected_package_index = manifest.dependency_index.as_str();
        let acquisition = package_acquisition_arguments(&manifest, python);
        let dependencies = dependency_installation_arguments(
            &manifest,
            &["scene".into()],
            &[],
            python,
            constraints,
            true,
        );

        assert_eq!(selected_package_index, "https://pypi.org/simple");
        assert_eq!(manifest.dependency_index, "https://pypi.org/simple");
        assert!(acquisition.iter().any(|item| item == "--no-deps"));
        assert!(
            acquisition
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
    }

    #[test]
    fn capability_commands_pass_one_comma_separated_option() {
        let manifest = manifest().expect("manifest");

        assert_eq!(
            capability_command_arguments(&manifest, "doctor", &["dialogue".into(), "scene".into()]),
            ["doctor", "--json", "--modalities", "dialogue,scene"]
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
    fn tray_manage_browser_and_quit_actions_are_unambiguous() {
        assert_eq!(
            action_for_activation(DesktopActivation::Tray("manage")),
            Some(DesktopAction::Manage)
        );
        assert_eq!(
            action_for_activation(DesktopActivation::Tray("open")),
            Some(DesktopAction::OpenBrowser)
        );
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
