use std::{
    borrow::Cow,
    collections::{BTreeMap, BTreeSet},
    env, fs,
    io::{self, Write},
    net::{SocketAddr, TcpListener, TcpStream},
    path::{Path, PathBuf},
    process::Command,
    sync::{
        Arc, Mutex, OnceLock,
        atomic::{AtomicBool, Ordering},
    },
    thread,
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

mod background_process;
mod target_profiles;

const RUNTIME_MANIFEST_BYTES: &[u8] = include_bytes!("../../runtime-manifest.json");
const RUNTIME_CONSTRAINTS_BYTES: &[u8] = include_bytes!("../../runtime-constraints.txt");
const MODEL_CACHE_CATALOG_BYTES: &[u8] = include_bytes!("../../model-cache-catalog.json");
const PRODUCT_DATA_DIRECTORY_NAME: &str = "VidXP";
const MAX_SETUP_OUTPUT_BYTES: usize = 4 * 1024 * 1024;

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
    package_version: String,
    capabilities: Vec<String>,
    surfaces: Vec<String>,
    model_directory: String,
    detail: String,
}

#[derive(Serialize)]
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

struct SystemInstallPlan {
    manager: String,
    command: Vec<String>,
    automatic: bool,
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

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
enum ActivationStage {
    Prepared,
    ProfileProjected,
    ActiveWritten,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ActivationRecovery {
    RollBack,
    Complete,
}

fn activation_recovery(stage: &ActivationStage) -> ActivationRecovery {
    match stage {
        ActivationStage::ActiveWritten => ActivationRecovery::Complete,
        ActivationStage::Prepared | ActivationStage::ProfileProjected => {
            ActivationRecovery::RollBack
        }
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

struct ManagedUi {
    process: background_process::OwnedChild,
    url: String,
    profile_id: String,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum UiProcessAction {
    Reuse,
    Replace,
    Start,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum DesktopAction {
    Manage,
    OpenBrowser,
    Quit,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum DesktopCloseAction {
    HideToTray,
    Quit,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum DesktopActivation<'a> {
    Startup,
    SingleInstance,
    Tray(&'a str),
}

struct DesktopState {
    ui_process: Mutex<Option<ManagedUi>>,
    operation_worker_runtime: Mutex<Option<PathBuf>>,
    operation_cancellation: Arc<Mutex<Option<background_process::CancellationToken>>>,
    transition: Arc<Mutex<TransitionState>>,
    browser_open_active: AtomicBool,
    shutdown: background_process::CancellationToken,
}

impl Default for DesktopState {
    fn default() -> Self {
        Self {
            ui_process: Mutex::new(None),
            operation_worker_runtime: Mutex::new(None),
            operation_cancellation: Arc::new(Mutex::new(None)),
            transition: Arc::new(Mutex::new(TransitionState::default())),
            browser_open_active: AtomicBool::new(false),
            shutdown: background_process::CancellationToken::default(),
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
}

impl OperationCancellationGuard {
    fn register(state: &DesktopState) -> Result<Self, String> {
        let token = background_process::CancellationToken::default();
        *state
            .operation_cancellation
            .lock()
            .map_err(|_| "The setup cancellation supervisor is unavailable.".to_string())? =
            Some(token.clone());
        Ok(Self {
            slot: state.operation_cancellation.clone(),
            token,
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

fn package_index(package_version: &str) -> &'static str {
    if package_version.split_once('-').is_some() {
        "https://test.pypi.org/simple"
    } else {
        "https://pypi.org/simple"
    }
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
        package_index(&manifest.package_version).into(),
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

fn required_encoder_missing(output: &str, encoder: &str) -> bool {
    !output
        .lines()
        .flat_map(|line| line.split_whitespace())
        .any(|token| token == encoder)
}

fn system_install_plan() -> Option<SystemInstallPlan> {
    if cfg!(windows) {
        resolve_system_executable("winget")?;
        return Some(SystemInstallPlan {
            manager: "Windows Package Manager".into(),
            command: vec![
                "winget".into(),
                "install".into(),
                "--id".into(),
                "Gyan.FFmpeg".into(),
                "--exact".into(),
                "--source".into(),
                "winget".into(),
                "--accept-package-agreements".into(),
                "--accept-source-agreements".into(),
            ],
            automatic: true,
        });
    }
    if cfg!(target_os = "macos") {
        let brew = resolve_system_executable("brew")?;
        return Some(SystemInstallPlan {
            manager: "Homebrew".into(),
            command: vec![
                brew.to_string_lossy().into_owned(),
                "install".into(),
                "ffmpeg".into(),
            ],
            automatic: true,
        });
    }
    if resolve_system_executable("apt-get").is_some() {
        return Some(SystemInstallPlan {
            manager: "APT".into(),
            command: vec![
                "sudo".into(),
                "apt-get".into(),
                "install".into(),
                "ffmpeg".into(),
            ],
            automatic: false,
        });
    }
    if resolve_system_executable("dnf").is_some() {
        return Some(SystemInstallPlan {
            manager: "DNF".into(),
            command: vec![
                "sudo".into(),
                "dnf".into(),
                "install".into(),
                "ffmpeg".into(),
            ],
            automatic: false,
        });
    }
    None
}

fn display_command(arguments: &[String]) -> String {
    arguments
        .iter()
        .map(|argument| {
            if argument.contains(char::is_whitespace) {
                format!("\"{}\"", argument.replace('"', "\\\""))
            } else {
                argument.clone()
            }
        })
        .collect::<Vec<_>>()
        .join(" ")
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
    let mut environment: Vec<_> = std::env::vars()
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
    Ok(active)
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

fn recover_interrupted_activation(app: &AppHandle, state: &DesktopState) -> Result<(), String> {
    let _transition = TargetTransitionCoordinator::begin(state, TransitionKind::RecoverActivation)
        .map_err(|error| error.to_string())?;
    let paths = desktop_paths(app)?;
    if !paths.activation_journal.exists() {
        return Ok(());
    }
    let contents = fs::read(&paths.activation_journal)
        .map_err(|error| format!("Could not read the activation journal: {error}"))?;
    let journal: ActivationJournal = serde_json::from_slice(&contents)
        .map_err(|error| format!("The activation journal is invalid: {error}"))?;
    if journal.schema_version != 2 {
        return Err(format!(
            "The activation journal uses unsupported schema version {}.",
            journal.schema_version
        ));
    }
    match activation_recovery(&journal.stage) {
        ActivationRecovery::Complete => {
            write_active_runtime(&paths, &journal.candidate_active)?;
            target_profiles::replace_state(app, journal.candidate_targets)
                .map_err(|error| error.to_string())?;
        }
        ActivationRecovery::RollBack => {
            restore_active_runtime(&paths, journal.previous_active_bytes.as_deref())?;
            target_profiles::replace_state(app, journal.previous_targets)
                .map_err(|error| error.to_string())?;
        }
    }
    clear_activation_journal(&paths)
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
fn runtime_manifest() -> Result<RuntimeManifest, String> {
    manifest()
}

#[tauri::command]
fn target_state(
    app: AppHandle,
) -> Result<target_profiles::TargetState, target_profiles::TargetError> {
    target_profiles::current_state(&app)
}

#[tauri::command]
async fn refresh_target_state(
    app: AppHandle,
    state: tauri::State<'_, DesktopState>,
) -> Result<target_profiles::TargetState, target_profiles::TargetError> {
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
            Ok(_) => {
                let _ = target_profiles::validated_selected_profile_with_cancellation(
                    &app,
                    &desktop_version,
                    Some(&cancellation),
                );
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
async fn discover_local_targets() -> Result<Vec<target_profiles::DiscoveredTarget>, String> {
    tauri::async_runtime::spawn_blocking(target_profiles::discover_local_targets)
        .await
        .map_err(|error| format!("Target discovery stopped unexpectedly: {error}"))
}

#[tauri::command]
fn choose_local_executable(app: AppHandle) -> Result<Option<String>, String> {
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
    executable: String,
) -> Result<target_profiles::TargetInspection, target_profiles::TargetError> {
    let manifest = manifest().map_err(|error| target_profiles::TargetError {
        code: target_profiles::TargetErrorCode::ValidationRequired,
        message: error,
    })?;
    let desktop_version = manifest.desktop_version;
    tauri::async_runtime::spawn_blocking(move || {
        target_profiles::inspect_executable(Path::new(&executable), &desktop_version)
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
    let manifest = manifest().map_err(|error| target_profiles::TargetError {
        code: target_profiles::TargetErrorCode::ValidationRequired,
        message: error,
    })?;
    let transition = TargetTransitionCoordinator::begin(&state, TransitionKind::Adopt)?;
    let desktop_version = manifest.desktop_version;
    tauri::async_runtime::spawn_blocking(move || {
        let _transition = transition;
        let canonical =
            fs::canonicalize(Path::new(&executable)).unwrap_or_else(|_| PathBuf::from(&executable));
        let setup = target_profiles::adopt_local(&app, &canonical, display_name, &desktop_version)?;
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
    let manifest = manifest().map_err(|error| target_profiles::TargetError {
        code: target_profiles::TargetErrorCode::ValidationRequired,
        message: error,
    })?;
    let transition = TargetTransitionCoordinator::begin(&state, TransitionKind::Select)?;
    let desktop_version = manifest.desktop_version;
    tauri::async_runtime::spawn_blocking(move || {
        let _transition = transition;
        let setup = target_profiles::select_profile(&app, &profile_id, &desktop_version)?;
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
async fn begin_managed_setup(
    app: AppHandle,
    state: tauri::State<'_, DesktopState>,
) -> Result<ManagedSetupDraft, target_profiles::TargetError> {
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
async fn media_runtime_status() -> Result<MediaRuntimeStatus, String> {
    tauri::async_runtime::spawn_blocking(inspect_media_runtime)
        .await
        .map_err(|error| format!("Media runtime inspection stopped unexpectedly: {error}"))
}

#[tauri::command]
fn choose_model_directory(app: AppHandle) -> Result<Option<String>, String> {
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
    let _transition =
        TargetTransitionCoordinator::begin_apply(&state, &draft_id, TransitionKind::InstallMedia)
            .map_err(|error| error.to_string())?;
    let cancellation = OperationCancellationGuard::register(&state)?;
    let current = tauri::async_runtime::spawn_blocking(inspect_media_runtime)
        .await
        .map_err(|error| format!("Media runtime inspection stopped unexpectedly: {error}"))?;
    if current.ready {
        return Ok(current);
    }
    let plan = system_install_plan().ok_or("No supported system package manager was found.")?;
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
async fn runtime_status(app: AppHandle) -> Result<RuntimeStatus, String> {
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
            package_version: manifest.package_version,
            capabilities: Vec::new(),
            surfaces: Vec::new(),
            model_directory: default_model_directory,
            detail: "No Desktop-managed runtime has been created yet.".into(),
        });
    }
    if let Err(detail) = verified_media_runtime() {
        return Ok(RuntimeStatus {
            state: RuntimeState::Broken,
            ready: false,
            package_version: manifest.package_version,
            capabilities: Vec::new(),
            surfaces: Vec::new(),
            model_directory: default_model_directory,
            detail,
        });
    }
    let active = match active_runtime(&paths) {
        Ok(active) => active,
        Err(detail) => {
            return Ok(RuntimeStatus {
                state: RuntimeState::Broken,
                ready: false,
                package_version: manifest.package_version,
                capabilities: Vec::new(),
                surfaces: Vec::new(),
                model_directory: default_model_directory,
                detail,
            });
        }
    };
    paths.models = active.model_directory.clone();
    let runtime = runtime_directory(&paths, &active);
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
    Ok(RuntimeStatus {
        state: if version.is_ok() {
            RuntimeState::Ready
        } else {
            RuntimeState::Broken
        },
        ready: version.is_ok(),
        package_version: active.package_version,
        capabilities: active.capabilities,
        surfaces: active.surfaces,
        model_directory: active.model_directory.to_string_lossy().into_owned(),
        detail: version
            .err()
            .unwrap_or_else(|| "Local video processing is ready.".into()),
    })
}

#[tauri::command]
async fn model_directory_inventory(
    app: AppHandle,
    directory: Option<String>,
) -> Result<ModelDirectoryInventory, String> {
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
    let _transition =
        TargetTransitionCoordinator::begin_apply(&state, &draft_id, TransitionKind::PrepareModels)
            .map_err(|error| error.to_string())?;
    let cancellation = OperationCancellationGuard::register(&state)?;
    let preparation_app = app.clone();
    let (runtime, paths, capabilities) = tauri::async_runtime::spawn_blocking(move || {
        let profile = target_profiles::selected_profile(&preparation_app)
            .map_err(|error| error.to_string())?;
        target_profiles::authorize_lifecycle(&profile, target_profiles::LifecycleAction::Install)
            .map_err(|error| error.to_string())?;
        let mut paths = desktop_paths(&preparation_app)?;
        let active = active_runtime(&paths)?;
        if profile.managed_runtime_profile.as_deref() != Some(active.profile.as_str()) {
            return Err(
                "The selected managed target no longer matches the active Desktop runtime."
                    .to_string(),
            );
        }
        paths.models = active.model_directory.clone();
        let runtime = runtime_directory(&paths, &active);
        Ok::<_, String>((runtime, paths, active.capabilities))
    })
    .await
    .map_err(|error| format!("Model preparation setup stopped unexpectedly: {error}"))??;

    let manifest = manifest()?;
    let arguments = capability_command_arguments(&manifest, "prepare", &capabilities);
    *state
        .operation_worker_runtime
        .lock()
        .map_err(|_| "The preparation worker supervisor is unavailable.".to_string())? =
        Some(runtime.clone());
    let preparation = run_vidxp_supervised(
        &runtime,
        &paths,
        &arguments,
        cancellation.token(),
        "VidXP model preparation",
    )
    .await;
    let shutdown_runtime = runtime.clone();
    let shutdown_paths = paths;
    let _ = tauri::async_runtime::spawn_blocking(move || {
        stop_worker(&shutdown_runtime, &shutdown_paths);
    })
    .await;
    if let Ok(mut worker_runtime) = state.operation_worker_runtime.lock() {
        worker_runtime.take();
    }
    preparation?;
    target_profiles::current_state(&app).map_err(|error| error.to_string())
}

#[tauri::command]
async fn install_runtime(
    app: AppHandle,
    state: tauri::State<'_, DesktopState>,
    request: InstallRequest,
) -> Result<InstallTransitionResult, String> {
    let mut transition = TargetTransitionCoordinator::begin_apply(
        &state,
        &request.draft_id,
        TransitionKind::InstallRuntime,
    )
    .map_err(|error| error.to_string())?;
    let cancellation = OperationCancellationGuard::register(&state)?;
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
            *state
                .operation_worker_runtime
                .lock()
                .map_err(|_| "The preparation worker supervisor is unavailable.")? =
                Some(staging.clone());
            let preparation = run_vidxp_supervised(
                &staging,
                &paths,
                &prepare_arguments,
                cancellation.token(),
                "VidXP model preparation",
            )
            .await;
            let _ = run_vidxp(
                &staging,
                &paths,
                &["jobs".into(), "stop-worker".into()],
                "VidXP preparation worker shutdown",
            );
            if let Ok(mut worker_runtime) = state.operation_worker_runtime.lock() {
                worker_runtime.take();
            }
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
        fs::rename(&staging, &runtime)
            .map_err(|error| format!("Could not finalize the validated runtime: {error}"))?;

        let candidate_targets = match target_profiles::prepare_managed_activation(
            &activation_app,
            managed_runtime_projection_for(&activation_paths, &active),
            &activation_manifest_version,
        ) {
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

        let activation_result = (|| {
            target_profiles::replace_state(&activation_app, candidate_targets.clone())
                .map_err(|error| error.to_string())?;
            journal.stage = ActivationStage::ProfileProjected;
            write_activation_journal(&activation_paths, &journal)?;
            write_active_runtime(&activation_paths, &active)?;
            journal.stage = ActivationStage::ActiveWritten;
            write_activation_journal(&activation_paths, &journal)?;
            clear_activation_journal(&activation_paths)
        })();
        if let Err(error) = activation_result {
            let target_rollback = target_profiles::replace_state(
                &activation_app,
                journal.previous_targets.clone(),
            )
                .map_err(|rollback| rollback.to_string());
            let runtime_rollback = restore_active_runtime(
                &activation_paths,
                journal.previous_active_bytes.as_deref(),
            );
            if target_rollback.is_ok() && runtime_rollback.is_ok() {
                let _ = clear_activation_journal(&activation_paths);
                return Err(format!(
                    "{error}. The installed runtime was retained at {}, while the previous active runtime and target remain selected.",
                    runtime.display()
                ));
            }
            return Err(format!(
                "{error}. Automatic rollback was incomplete; VidXP Desktop will recover the activation journal on its next start. The installed runtime remains at {}.",
                runtime.display()
            ));
        }
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
    let profile = target_profiles::validated_selected_profile(app, &manifest.desktop_version)
        .map_err(|error| error.to_string())?;
    target_profiles::authorize_lifecycle(&profile, target_profiles::LifecycleAction::Launch)
        .map_err(|error| error.to_string())?;
    if !profile.frontend.launchable {
        return Err(
            "The selected VidXP installation cannot launch the supported browser interface.".into(),
        );
    }
    let mut paths = desktop_paths(app)?;
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
    configure_ui_service_command(&mut command, &profile.repository_root, port);
    let mut process = background_process::spawn_service(command)
        .map_err(|error| format!("Could not start the VidXP interface: {}", error.detail))?;

    let address = SocketAddr::from(([127, 0, 0, 1], port));
    let deadline = Instant::now() + Duration::from_secs(30);
    while Instant::now() < deadline {
        if let Some(status) = process
            .try_wait()
            .map_err(|error| format!("Could not inspect the interface process: {error}"))?
        {
            return Err(format!(
                "The VidXP interface exited during startup ({status})."
            ));
        }
        if TcpStream::connect_timeout(&address, Duration::from_millis(100)).is_ok() {
            let url = format!("http://127.0.0.1:{port}");
            *active_process = Some(ManagedUi {
                process,
                url: url.clone(),
                profile_id: profile.id.clone(),
            });
            return Ok(url);
        }
        thread::sleep(Duration::from_millis(100));
    }
    process.terminate_and_reap();
    Err("The VidXP interface did not become ready in 30 seconds.".into())
}

fn stop_ui_process(state: &DesktopState) {
    let Ok(mut active) = state.ui_process.lock() else {
        return;
    };
    if let Some(mut ui) = active.take() {
        ui.process.terminate_and_reap();
    }
}

fn ui_process_action(
    running: bool,
    active_profile_id: &str,
    requested_profile_id: &str,
) -> UiProcessAction {
    if !running {
        UiProcessAction::Start
    } else if active_profile_id == requested_profile_id {
        UiProcessAction::Reuse
    } else {
        UiProcessAction::Replace
    }
}

fn configure_ui_service_command(command: &mut Command, repository_root: &Path, port: u16) {
    command
        // The desktop owns the one intentional browser open after readiness. Without
        // headless mode Streamlit also opens the URL, producing duplicate tabs and
        // potentially visible launcher consoles on Windows.
        .env("STREAMLIT_SERVER_HEADLESS", "true")
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
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .unwrap_or(u64::MAX);
    target_profiles::current_state(app)
        .ok()
        .and_then(|state| state.selected_profile().cloned())
        .is_some_and(|profile| profile.is_ready(now))
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

fn action_for_activation(activation: DesktopActivation<'_>) -> Option<DesktopAction> {
    match activation {
        DesktopActivation::Startup | DesktopActivation::SingleInstance => {
            Some(DesktopAction::Manage)
        }
        DesktopActivation::Tray("open") => Some(DesktopAction::OpenBrowser),
        DesktopActivation::Tray("manage") => Some(DesktopAction::Manage),
        DesktopActivation::Tray("quit") => Some(DesktopAction::Quit),
        DesktopActivation::Tray(_) => None,
    }
}

fn perform_desktop_action(app: &AppHandle, action: DesktopAction) {
    match action {
        DesktopAction::Manage => show_main_window(app),
        DesktopAction::OpenBrowser => open_browser_or_show_manager(app),
        DesktopAction::Quit => begin_shutdown(app),
    }
}

fn close_action(configured: bool) -> DesktopCloseAction {
    if configured {
        DesktopCloseAction::HideToTray
    } else {
        DesktopCloseAction::Quit
    }
}

fn begin_shutdown(app: &AppHandle) {
    let state = app.state::<DesktopState>();
    if state.shutdown.is_cancelled() {
        return;
    }
    state.shutdown.cancel();
    if let Ok(active) = state.operation_cancellation.lock()
        && let Some(cancellation) = active.as_ref()
    {
        cancellation.cancel();
    }
    log::info!("VidXP supervised shutdown requested");
    shutdown(app);
    log::info!("VidXP supervised shutdown completed");
    std::process::exit(0);
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

fn stop_worker(runtime: &Path, paths: &DesktopPaths) {
    let mut command = configured_command(&executable(runtime, "vidxp"), paths);
    command
        .arg("--index-dir")
        .arg(&paths.repository)
        .args(["jobs", "stop-worker"]);
    let _ = background_process::run(
        command,
        background_process::BackgroundPolicy {
            timeout: Duration::from_secs(5),
            max_output_bytes: 64 * 1024,
        },
        None,
    );
}

fn shutdown(app: &AppHandle) {
    log::info!("Stopping active VidXP processes");
    let state = app.state::<DesktopState>();
    if let Ok(active_operation) = state.operation_cancellation.lock()
        && let Some(cancellation) = active_operation.as_ref()
    {
        cancellation.cancel();
    }
    stop_ui_process(&state);
    let Ok(mut paths) = desktop_paths(app) else {
        log::warn!("Could not resolve desktop paths during shutdown");
        return;
    };
    if let Ok(mut operation_worker) = state.operation_worker_runtime.lock()
        && let Some(runtime) = operation_worker.take()
    {
        stop_worker(&runtime, &paths);
    }
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
    stop_worker(&runtime, &paths);
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
            begin_managed_setup,
            cancel_managed_setup,
            media_runtime_status,
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
        ActivationRecovery, ActivationStage, DesktopAction, DesktopActivation, DesktopCloseAction,
        DesktopState, DraftPhase, DraftRecord, ManagedSetupDraft, TargetTransitionCoordinator,
        TransitionKind, UiProcessAction, action_for_activation, activation_recovery,
        base_package_specification, capability_command_arguments, claim_browser_open, close_action,
        configure_ui_service_command, dependency_installation_arguments, desktop_paths_from_roots,
        display_command, inventory_model_directory, manifest, normalize_line_endings,
        normalized_runtime_constraints, package_acquisition_arguments, package_index,
        package_specification, read_active_runtime_snapshot, required_encoder_missing,
        restore_active_runtime, selected_capabilities, selected_surfaces, ui_process_action,
    };
    use std::{
        ffi::OsStr,
        fs,
        path::{Path, PathBuf},
        process::Command,
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
    fn interrupted_activation_recovers_only_after_both_authorities_were_written() {
        assert_eq!(
            activation_recovery(&ActivationStage::Prepared),
            ActivationRecovery::RollBack
        );
        assert_eq!(
            activation_recovery(&ActivationStage::ProfileProjected),
            ActivationRecovery::RollBack
        );
        assert_eq!(
            activation_recovery(&ActivationStage::ActiveWritten),
            ActivationRecovery::Complete
        );
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
        let selected_package_index = package_index(&manifest.package_version);
        let acquisition = package_acquisition_arguments(&manifest, python);
        let dependencies = dependency_installation_arguments(
            &manifest,
            &["scene".into()],
            &[],
            python,
            constraints,
            true,
        );

        let expected_package_index = if manifest.package_version.contains('-') {
            "https://test.pypi.org/simple"
        } else {
            "https://pypi.org/simple"
        };

        assert_eq!(selected_package_index, expected_package_index);
        assert_eq!(package_index("0.3.0-b.1"), "https://test.pypi.org/simple");
        assert_eq!(package_index("0.3.0"), "https://pypi.org/simple");
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

        configure_ui_service_command(&mut command, Path::new("repository"), 43123);

        assert!(command.get_envs().any(|(key, value)| {
            key == OsStr::new("STREAMLIT_SERVER_HEADLESS") && value == Some(OsStr::new("true"))
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
