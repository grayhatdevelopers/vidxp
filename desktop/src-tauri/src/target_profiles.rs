use std::{
    collections::{BTreeMap, BTreeSet, HashSet},
    fs,
    path::{Path, PathBuf},
    process::Command,
    sync::Arc,
    time::{Duration, SystemTime, UNIX_EPOCH},
};

use crate::background_process::{
    self, BackgroundErrorKind, BackgroundOutput, BackgroundPolicy, CancellationToken,
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use tauri::{AppHandle, Wry};
use tauri_plugin_store::{Store, StoreExt};

const STORE_FILE: &str = "target-profiles.json";
const STORE_SCHEMA_KEY: &str = "schema_version";
const PROFILES_KEY: &str = "profiles";
const SELECTED_PROFILE_KEY: &str = "selected_profile_id";
const CURRENT_STORE_SCHEMA_VERSION: u32 = 1;
pub const CURRENT_PROFILE_SCHEMA_VERSION: u32 = 1;
const SUPPORTED_PROBE_SCHEMA_VERSION: u32 = 1;
pub const SUPPORTED_PROBE_PROTOCOL_VERSION: u32 = 1;
const SUPPORTED_LAUNCH_PROTOCOL_VERSION: u32 = 2;
const PRODUCT_ID: &str = "dev.grayhat.vidxp";
const PROBE_TIMEOUT: Duration = Duration::from_secs(10);
const VALIDATION_MAX_AGE: Duration = Duration::from_secs(24 * 60 * 60);
const MAX_PROBE_STREAM_BYTES: usize = 256 * 1024;

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum TargetKind {
    ExistingLocal,
    Managed,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum LifecycleOwnership {
    External,
    Desktop,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum LifecycleAction {
    Validate,
    Launch,
    Install,
    BroadProcessStop,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum TargetErrorCode {
    ExecutableMissing,
    ExecutableInvalid,
    ProbeCouldNotStart,
    ProbeFailed,
    ProbeTimeout,
    ProbeOutputTooLarge,
    MalformedProbe,
    NotVidxp,
    ProbeChallengeMismatch,
    LauncherIdentityMismatch,
    UnsupportedProbeSchema,
    UnsupportedProbeProtocol,
    RuntimeUpdateRequired,
    UnsupportedLaunchProtocol,
    UnsupportedLaunchContract,
    InvalidDataRoot,
    StoreUnavailable,
    StoreCorrupt,
    UnsupportedStoreSchema,
    UnsupportedProfileSchema,
    ProfileMalformed,
    ProfileNotFound,
    SelectedProfileMissing,
    ValidationRequired,
    ValidationStale,
    LifecycleForbidden,
    ManagedRuntimeUnavailable,
    OperationConflict,
    DraftMismatch,
    DraftApplying,
    ManagedProfileOwned,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct TargetError {
    pub code: TargetErrorCode,
    pub message: String,
}

impl TargetError {
    fn new(code: TargetErrorCode, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }
}

impl std::fmt::Display for TargetError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "{}", self.message)
    }
}

impl std::error::Error for TargetError {}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct RuntimeIdentity {
    pub python_executable: PathBuf,
    pub python_version: String,
    pub implementation: String,
    pub prefix: PathBuf,
    pub base_prefix: PathBuf,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct FrontendCapability {
    pub available: bool,
    pub launchable: bool,
    pub optional: bool,
    pub code: String,
    pub message: String,
    pub remediation: String,
}

impl Default for FrontendCapability {
    fn default() -> Self {
        Self {
            available: false,
            launchable: false,
            optional: true,
            code: "frontend_unavailable".into(),
            message: "The optional browser interface is not installed.".into(),
            remediation: "Use this installation's own package-management workflow to install the VidXP frontend extra, then revalidate.".into(),
        }
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct TargetProfile {
    pub id: String,
    pub display_name: String,
    pub schema_version: u32,
    pub kind: TargetKind,
    pub lifecycle_ownership: LifecycleOwnership,
    pub executable: PathBuf,
    pub data_root: PathBuf,
    pub repository_root: PathBuf,
    pub observed_vidxp_version: String,
    pub probe_schema_version: u32,
    pub probe_protocol_version: u32,
    pub launch_protocol_version: u32,
    pub runtime: Option<RuntimeIdentity>,
    pub frontend: FrontendCapability,
    pub last_successful_validation_at: Option<u64>,
    pub validation_error: Option<TargetError>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub managed_runtime_profile: Option<String>,
    #[serde(default)]
    pub capabilities: Vec<String>,
    #[serde(default)]
    pub surfaces: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model_directory: Option<PathBuf>,
}

impl TargetProfile {
    pub fn is_ready(&self, now: u64) -> bool {
        self.validation_error.is_none()
            && self.last_successful_validation_at.is_some_and(|validated| {
                now.saturating_sub(validated) <= VALIDATION_MAX_AGE.as_secs()
            })
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct DiscoveredTarget {
    pub executable: PathBuf,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ValidatedTarget {
    pub executable: PathBuf,
    pub product_version: String,
    pub probe_schema_version: u32,
    pub probe_protocol_version: u32,
    pub launch_protocol_version: u32,
    pub runtime: RuntimeIdentity,
    pub data_root: PathBuf,
    pub repository_root: PathBuf,
    pub model_root: PathBuf,
    pub frontend: FrontendCapability,
    pub capabilities: Vec<String>,
    pub surfaces: Vec<String>,
    pub validated_at: u64,
}

#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
pub struct TargetState {
    pub profiles: Vec<TargetProfile>,
    pub selected_profile_id: Option<String>,
    pub issues: Vec<TargetError>,
}

impl TargetState {
    pub fn selected_profile(&self) -> Option<&TargetProfile> {
        let selected = self.selected_profile_id.as_deref()?;
        self.profiles.iter().find(|profile| profile.id == selected)
    }
}

#[derive(Clone, Debug)]
pub struct ManagedRuntimeProjection {
    pub runtime_profile: String,
    pub executable: PathBuf,
    pub data_root: PathBuf,
    pub repository_root: PathBuf,
    pub model_directory: PathBuf,
    pub package_version: String,
    pub capabilities: Vec<String>,
    pub surfaces: Vec<String>,
}

#[derive(Debug, Deserialize, Serialize)]
struct ProbeRuntime {
    python_executable: PathBuf,
    python_version: String,
    implementation: String,
    prefix: PathBuf,
    base_prefix: PathBuf,
}

#[derive(Debug, Deserialize, Serialize)]
struct ProbeLaunchContract {
    protocol_version: u32,
    surface: String,
    command: String,
}

#[derive(Debug, Default, Deserialize, Serialize)]
struct ProbeCapabilities {
    #[serde(default)]
    frontend: FrontendCapability,
}

#[derive(Debug, Deserialize, Serialize)]
struct ProbeDocument {
    product: String,
    product_version: String,
    schema_version: u32,
    protocol_version: u32,
    launch_contract: ProbeLaunchContract,
    request_id: String,
    launcher: PathBuf,
    runtime: ProbeRuntime,
    data_root: PathBuf,
    repository_root: PathBuf,
    model_root: PathBuf,
    #[serde(default)]
    capabilities: ProbeCapabilities,
    search_capabilities: Option<Vec<String>>,
    surfaces: Option<BTreeMap<String, FrontendCapability>>,
}

#[derive(Clone, Debug, Default)]
struct DecodedState {
    profiles: Vec<TargetProfile>,
    selected_profile_id: Option<String>,
    issues: Vec<TargetError>,
    changed: bool,
}

struct ProbeOutput {
    success: bool,
    stdout: Vec<u8>,
    stderr: Vec<u8>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum InspectionState {
    ReadyToUse,
    UpdateRequired,
    CannotStart,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct TargetInspection {
    pub state: InspectionState,
    pub adoptable: bool,
    pub executable: PathBuf,
    pub reported_version: Option<String>,
    pub probe_compatible: bool,
    pub launch_compatible: bool,
    pub validated: Option<ValidatedTarget>,
    pub message: String,
    pub remediation: String,
    pub technical_details: Option<String>,
}

fn unix_timestamp() -> Result<u64, TargetError> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .map_err(|error| {
            TargetError::new(
                TargetErrorCode::ValidationRequired,
                format!("The system clock is invalid: {error}"),
            )
        })
}

fn canonical_executable(path: &Path) -> Result<PathBuf, TargetError> {
    if !path.exists() {
        return Err(TargetError::new(
            TargetErrorCode::ExecutableMissing,
            format!(
                "The selected VidXP executable no longer exists at {}.",
                path.display()
            ),
        ));
    }
    if !path.is_file() {
        return Err(TargetError::new(
            TargetErrorCode::ExecutableInvalid,
            "The selected VidXP path is not an executable file.",
        ));
    }
    fs::canonicalize(path).map_err(|error| {
        TargetError::new(
            TargetErrorCode::ExecutableInvalid,
            format!("The selected VidXP executable could not be resolved: {error}"),
        )
    })
}

fn canonical_reported_launcher(reported: &Path, selected: &Path) -> Result<PathBuf, TargetError> {
    if let Ok(canonical) = canonical_executable(reported) {
        return (canonical == selected).then_some(canonical).ok_or_else(|| {
            TargetError::new(
                TargetErrorCode::LauncherIdentityMismatch,
                "The probe response belongs to a different launcher than the selected executable.",
            )
        });
    }
    #[cfg(windows)]
    if !reported.exists() && reported.extension().is_none() {
        let reported_parent = reported
            .parent()
            .and_then(|parent| fs::canonicalize(parent).ok());
        let selected_parent = selected
            .parent()
            .and_then(|parent| fs::canonicalize(parent).ok());
        if reported_parent == selected_parent && reported.file_name() == selected.file_stem() {
            return Ok(selected.to_path_buf());
        }
    }
    Err(TargetError::new(
        TargetErrorCode::LauncherIdentityMismatch,
        "The probe did not report a usable VidXP launcher identity.",
    ))
}

fn challenge_for(executable: &Path) -> Result<String, TargetError> {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| {
            TargetError::new(
                TargetErrorCode::ValidationRequired,
                format!("The system clock is invalid: {error}"),
            )
        })?
        .as_nanos();
    let seed = format!("{}:{now}:{}", executable.display(), std::process::id());
    Ok(hex::encode(Sha256::digest(seed.as_bytes())))
}

fn collect_command_output(
    executable: &Path,
    arguments: &[&str],
    operation: &str,
    cancellation: Option<&CancellationToken>,
) -> Result<ProbeOutput, TargetError> {
    let mut command = Command::new(executable);
    command.args(arguments);
    collect_prepared_command_output(command, operation, cancellation)
}

fn collect_prepared_command_output(
    command: Command,
    operation: &str,
    cancellation: Option<&CancellationToken>,
) -> Result<ProbeOutput, TargetError> {
    let BackgroundOutput {
        status,
        stdout,
        stderr,
    } = background_process::run(
        command,
        BackgroundPolicy {
            timeout: PROBE_TIMEOUT,
            max_output_bytes: MAX_PROBE_STREAM_BYTES,
        },
        cancellation,
    )
    .map_err(|error| {
        let code = match error.kind {
            BackgroundErrorKind::Start => TargetErrorCode::ProbeCouldNotStart,
            BackgroundErrorKind::Timeout => TargetErrorCode::ProbeTimeout,
            BackgroundErrorKind::OutputTooLarge => TargetErrorCode::ProbeOutputTooLarge,
            _ => TargetErrorCode::ProbeFailed,
        };
        TargetError::new(code, format!("{operation} failed: {}.", error.detail))
    })?;
    Ok(ProbeOutput {
        success: status.success(),
        stdout,
        stderr,
    })
}

pub(crate) fn validate_executable_using(
    path: &Path,
    desktop_version: &str,
    cancellation: Option<&CancellationToken>,
    command_for: impl FnOnce(&Path) -> Command,
) -> Result<ValidatedTarget, TargetError> {
    validate_executable_with(path, desktop_version, |canonical, version, request_id| {
        let mut command = command_for(canonical);
        command.args([
            "desktop-probe",
            "--json",
            "--desktop-version",
            version,
            "--request-id",
            request_id,
        ]);
        collect_prepared_command_output(
            command,
            "The managed VidXP compatibility probe",
            cancellation,
        )
    })
}

fn collect_probe_output(
    executable: &Path,
    desktop_version: &str,
    request_id: &str,
) -> Result<ProbeOutput, TargetError> {
    collect_command_output(
        executable,
        &[
            "desktop-probe",
            "--json",
            "--desktop-version",
            desktop_version,
            "--request-id",
            request_id,
        ],
        "The VidXP compatibility probe",
        None,
    )
}

#[cfg(all(test, windows))]
fn collect_version_output(executable: &Path) -> Result<ProbeOutput, TargetError> {
    collect_command_output(executable, &["--version"], "The VidXP version check", None)
}

fn validate_probe_document(
    canonical: &Path,
    request_id: &str,
    document: ProbeDocument,
    now: u64,
) -> Result<ValidatedTarget, TargetError> {
    if document.product != PRODUCT_ID {
        return Err(TargetError::new(
            TargetErrorCode::NotVidxp,
            "The selected executable did not identify itself as VidXP.",
        ));
    }
    if document.request_id != request_id {
        return Err(TargetError::new(
            TargetErrorCode::ProbeChallengeMismatch,
            "The selected executable did not return the desktop validation challenge.",
        ));
    }
    if document.schema_version != SUPPORTED_PROBE_SCHEMA_VERSION {
        return Err(TargetError::new(
            TargetErrorCode::UnsupportedProbeSchema,
            format!(
                "This executable uses desktop probe schema {}; VidXP desktop supports schema {}.",
                document.schema_version, SUPPORTED_PROBE_SCHEMA_VERSION
            ),
        ));
    }
    if document.protocol_version < SUPPORTED_PROBE_PROTOCOL_VERSION {
        return Err(TargetError::new(
            TargetErrorCode::RuntimeUpdateRequired,
            "This VidXP installation must be updated before this Desktop version can manage its features and services.",
        ));
    }
    if document.protocol_version > SUPPORTED_PROBE_PROTOCOL_VERSION {
        return Err(TargetError::new(
            TargetErrorCode::UnsupportedProbeProtocol,
            "This VidXP installation is newer than this Desktop version. Update VidXP Desktop before connecting it.",
        ));
    }
    if document.launch_contract.protocol_version != SUPPORTED_LAUNCH_PROTOCOL_VERSION {
        return Err(TargetError::new(
            TargetErrorCode::UnsupportedLaunchProtocol,
            format!(
                "This executable uses desktop launch protocol {}; VidXP desktop supports protocol {}.",
                document.launch_contract.protocol_version, SUPPORTED_LAUNCH_PROTOCOL_VERSION
            ),
        ));
    }
    if document.launch_contract.surface != "browser" || document.launch_contract.command != "ui" {
        return Err(TargetError::new(
            TargetErrorCode::UnsupportedLaunchContract,
            "This executable does not provide the supported VidXP browser launch contract.",
        ));
    }
    canonical_reported_launcher(&document.launcher, canonical)?;
    for (label, path) in [
        ("data", &document.data_root),
        ("repository", &document.repository_root),
        ("model", &document.model_root),
        ("Python executable", &document.runtime.python_executable),
        ("Python prefix", &document.runtime.prefix),
        ("Python base prefix", &document.runtime.base_prefix),
    ] {
        if !path.is_absolute() {
            return Err(TargetError::new(
                TargetErrorCode::InvalidDataRoot,
                format!("The probe reported a non-absolute {label} path."),
            ));
        }
    }
    let search_capabilities = document.search_capabilities.ok_or_else(|| {
        TargetError::new(
            TargetErrorCode::RuntimeUpdateRequired,
            "This VidXP installation must be updated before this Desktop version can manage its features and services.",
        )
    })?;
    let surface_capabilities = document.surfaces.ok_or_else(|| {
        TargetError::new(
            TargetErrorCode::RuntimeUpdateRequired,
            "This VidXP installation must be updated before this Desktop version can manage its features and services.",
        )
    })?;
    let surfaces = surface_capabilities
        .iter()
        .filter(|(_, capability)| capability.available)
        .map(|(name, _)| name.clone())
        .collect();
    Ok(ValidatedTarget {
        executable: canonical.to_path_buf(),
        product_version: document.product_version,
        probe_schema_version: document.schema_version,
        probe_protocol_version: document.protocol_version,
        launch_protocol_version: document.launch_contract.protocol_version,
        runtime: RuntimeIdentity {
            python_executable: document.runtime.python_executable,
            python_version: document.runtime.python_version,
            implementation: document.runtime.implementation,
            prefix: document.runtime.prefix,
            base_prefix: document.runtime.base_prefix,
        },
        data_root: document.data_root,
        repository_root: document.repository_root,
        model_root: document.model_root,
        frontend: document.capabilities.frontend,
        capabilities: search_capabilities,
        surfaces,
        validated_at: now,
    })
}

fn validate_executable_with(
    path: &Path,
    desktop_version: &str,
    run_probe: impl FnOnce(&Path, &str, &str) -> Result<ProbeOutput, TargetError>,
) -> Result<ValidatedTarget, TargetError> {
    let canonical = canonical_executable(path)?;
    let request_id = challenge_for(&canonical)?;
    let output = run_probe(&canonical, desktop_version, &request_id)?;
    if !output.success {
        let detail = String::from_utf8_lossy(&output.stderr).trim().to_owned();
        return Err(TargetError::new(
            TargetErrorCode::ProbeFailed,
            if detail.is_empty() {
                "The selected executable rejected the VidXP compatibility probe.".into()
            } else {
                format!("The compatibility probe failed: {detail}")
            },
        ));
    }
    let document: ProbeDocument = serde_json::from_slice(&output.stdout).map_err(|_| {
        TargetError::new(
            TargetErrorCode::MalformedProbe,
            "The selected executable did not return a valid VidXP compatibility response.",
        )
    })?;
    validate_probe_document(&canonical, &request_id, document, unix_timestamp()?)
}

fn inspect_executable_with(
    path: &Path,
    desktop_version: &str,
    run_probe: impl FnOnce(&Path, &str, &str) -> Result<ProbeOutput, TargetError>,
    run_version: impl FnOnce(&Path) -> Result<ProbeOutput, TargetError>,
) -> Result<TargetInspection, TargetError> {
    let canonical = canonical_executable(path)?;
    let request_id = challenge_for(&canonical)?;
    let probe_result = run_probe(&canonical, desktop_version, &request_id).and_then(|output| {
        if !output.success {
            let detail = String::from_utf8_lossy(&output.stderr).trim().to_owned();
            return Err(TargetError::new(
                TargetErrorCode::ProbeFailed,
                if detail.is_empty() {
                    "The selected executable rejected the VidXP compatibility probe.".into()
                } else {
                    format!("The compatibility probe failed: {detail}")
                },
            ));
        }
        let document: ProbeDocument = serde_json::from_slice(&output.stdout).map_err(|_| {
            TargetError::new(
                TargetErrorCode::MalformedProbe,
                "The selected executable did not return a valid VidXP compatibility response.",
            )
        })?;
        validate_probe_document(&canonical, &request_id, document, unix_timestamp()?)
    });
    match probe_result {
        Ok(validated) => Ok(TargetInspection {
            state: InspectionState::ReadyToUse,
            adoptable: true,
            executable: canonical,
            reported_version: Some(validated.product_version.clone()),
            probe_compatible: true,
            launch_compatible: true,
            validated: Some(validated),
            message: "This installation supports the Desktop compatibility and launch contracts."
                .into(),
            remediation: String::new(),
            technical_details: None,
        }),
        Err(probe_error) => {
            let version = run_version(&canonical);
            match version {
                Ok(output) if output.success && !output.stdout.is_empty() => {
                    let reported = String::from_utf8_lossy(&output.stdout).trim().to_owned();
                    Ok(TargetInspection {
                        state: InspectionState::UpdateRequired,
                        adoptable: false,
                        executable: canonical,
                        reported_version: Some(reported.strip_prefix("VidXP ").unwrap_or(&reported).to_owned()),
                        probe_compatible: false,
                        launch_compatible: false,
                        validated: None,
                        message: "This VidXP installation does not provide a compatible Desktop probe and launch contract.".into(),
                        remediation: "Update this external installation with its own package-management workflow, then check it again.".into(),
                        technical_details: Some(probe_error.message),
                    })
                }
                Ok(output) => {
                    let detail = String::from_utf8_lossy(&output.stderr).trim().to_owned();
                    Ok(TargetInspection {
                        state: InspectionState::CannotStart,
                        adoptable: false,
                        executable: canonical,
                        reported_version: None,
                        probe_compatible: false,
                        launch_compatible: false,
                        validated: None,
                        message: "This executable could not start well enough to report its version.".into(),
                        remediation: "Repair this external installation with its own package-management workflow, then check it again.".into(),
                        technical_details: Some(if detail.is_empty() { probe_error.message } else { detail }),
                    })
                }
                Err(version_error) => Ok(TargetInspection {
                    state: InspectionState::CannotStart,
                    adoptable: false,
                    executable: canonical,
                    reported_version: None,
                    probe_compatible: false,
                    launch_compatible: false,
                    validated: None,
                    message: "This executable could not start well enough to report its version.".into(),
                    remediation: "Repair this external installation with its own package-management workflow, then check it again.".into(),
                    technical_details: Some(format!("{} {}", probe_error.message, version_error.message)),
                }),
            }
        }
    }
}

pub fn validate_executable(
    path: &Path,
    desktop_version: &str,
) -> Result<ValidatedTarget, TargetError> {
    validate_executable_with(path, desktop_version, collect_probe_output)
}

#[cfg(all(test, windows))]
fn inspect_executable(path: &Path, desktop_version: &str) -> Result<TargetInspection, TargetError> {
    inspect_executable_with(
        path,
        desktop_version,
        collect_probe_output,
        collect_version_output,
    )
}

pub fn inspect_executable_with_cancellation(
    path: &Path,
    desktop_version: &str,
    cancellation: &CancellationToken,
) -> Result<TargetInspection, TargetError> {
    inspect_executable_with(
        path,
        desktop_version,
        |executable, version, request_id| {
            collect_command_output(
                executable,
                &[
                    "desktop-probe",
                    "--json",
                    "--desktop-version",
                    version,
                    "--request-id",
                    request_id,
                ],
                "The VidXP compatibility probe",
                Some(cancellation),
            )
        },
        |executable| {
            collect_command_output(
                executable,
                &["--version"],
                "The VidXP version check",
                Some(cancellation),
            )
        },
    )
}

pub fn discover_local_targets() -> Vec<DiscoveredTarget> {
    let mut seen = HashSet::new();
    let mut discovered = which::which_all("vidxp")
        .into_iter()
        .flatten()
        .filter_map(|candidate| canonical_executable(&candidate).ok())
        .filter(|candidate| seen.insert(candidate.clone()))
        .map(|executable| DiscoveredTarget { executable })
        .collect::<Vec<_>>();
    discovered.sort_by(|left, right| left.executable.cmp(&right.executable));
    discovered
}

pub fn authorize_lifecycle(
    profile: &TargetProfile,
    action: LifecycleAction,
) -> Result<(), TargetError> {
    let structurally_valid = matches!(
        (&profile.kind, &profile.lifecycle_ownership),
        (TargetKind::ExistingLocal, LifecycleOwnership::External)
            | (TargetKind::Managed, LifecycleOwnership::Desktop)
    );
    if !structurally_valid {
        return Err(TargetError::new(
            TargetErrorCode::ProfileMalformed,
            "The target profile has an invalid lifecycle ownership declaration.",
        ));
    }
    if profile.lifecycle_ownership == LifecycleOwnership::External
        && matches!(
            action,
            LifecycleAction::Install | LifecycleAction::BroadProcessStop
        )
    {
        return Err(TargetError::new(
            TargetErrorCode::LifecycleForbidden,
            "This VidXP installation is externally owned and cannot be changed or broadly stopped by the desktop.",
        ));
    }
    Ok(())
}

pub fn authorize_managed_runtime_action(
    profile: &TargetProfile,
    runtime_profile: &str,
) -> Result<(), TargetError> {
    authorize_lifecycle(profile, LifecycleAction::Install)?;
    if profile.kind != TargetKind::Managed
        || profile.managed_runtime_profile.as_deref() != Some(runtime_profile)
    {
        return Err(TargetError::new(
            TargetErrorCode::ValidationRequired,
            "The selected managed target no longer matches the active Desktop runtime.",
        ));
    }
    Ok(())
}

fn stable_local_profile_id(executable: &Path) -> String {
    let digest = hex::encode(Sha256::digest(executable.to_string_lossy().as_bytes()));
    format!("local-{}", &digest[..24])
}

fn local_profile(validated: ValidatedTarget, display_name: Option<String>) -> TargetProfile {
    let default_name = validated
        .executable
        .file_name()
        .and_then(|name| name.to_str())
        .map_or_else(
            || "Local VidXP".into(),
            |name| format!("Local VidXP ({name})"),
        );
    TargetProfile {
        id: stable_local_profile_id(&validated.executable),
        display_name: display_name
            .map(|name| name.trim().to_owned())
            .filter(|name| !name.is_empty())
            .unwrap_or(default_name),
        schema_version: CURRENT_PROFILE_SCHEMA_VERSION,
        kind: TargetKind::ExistingLocal,
        lifecycle_ownership: LifecycleOwnership::External,
        executable: validated.executable,
        data_root: validated.data_root,
        repository_root: validated.repository_root,
        observed_vidxp_version: validated.product_version,
        probe_schema_version: validated.probe_schema_version,
        probe_protocol_version: validated.probe_protocol_version,
        launch_protocol_version: validated.launch_protocol_version,
        runtime: Some(validated.runtime),
        frontend: validated.frontend,
        last_successful_validation_at: Some(validated.validated_at),
        validation_error: None,
        managed_runtime_profile: None,
        capabilities: validated.capabilities,
        surfaces: validated.surfaces,
        model_directory: None,
    }
}

fn managed_profile(managed: ManagedRuntimeProjection) -> TargetProfile {
    TargetProfile {
        id: format!("managed-{}", managed.runtime_profile),
        display_name: "Desktop-managed VidXP".into(),
        schema_version: CURRENT_PROFILE_SCHEMA_VERSION,
        kind: TargetKind::Managed,
        lifecycle_ownership: LifecycleOwnership::Desktop,
        executable: managed.executable,
        data_root: managed.data_root,
        repository_root: managed.repository_root,
        observed_vidxp_version: managed.package_version,
        probe_schema_version: 0,
        probe_protocol_version: 0,
        launch_protocol_version: 0,
        runtime: None,
        frontend: FrontendCapability {
            available: managed.surfaces.iter().any(|surface| surface == "browser"),
            launchable: false,
            optional: true,
            code: "validation_required".into(),
            message: "The managed runtime must be revalidated before use.".into(),
            remediation: "Complete managed runtime validation before launch.".into(),
        },
        last_successful_validation_at: None,
        validation_error: Some(TargetError::new(
            TargetErrorCode::ValidationRequired,
            "The migrated managed runtime must be revalidated before use.",
        )),
        managed_runtime_profile: Some(managed.runtime_profile),
        capabilities: managed.capabilities,
        surfaces: managed.surfaces,
        model_directory: Some(managed.model_directory),
    }
}

fn reconcile_managed_profile(
    existing: Option<&TargetProfile>,
    managed: ManagedRuntimeProjection,
) -> TargetProfile {
    let mut profile = managed_profile(managed);
    if let Some(existing) = existing {
        profile.display_name = existing.display_name.clone();
    }
    profile
}

fn validate_profile_structure(profile: &TargetProfile) -> Result<(), TargetError> {
    if profile.schema_version != CURRENT_PROFILE_SCHEMA_VERSION {
        return Err(TargetError::new(
            TargetErrorCode::UnsupportedProfileSchema,
            format!(
                "Target profile {} uses unsupported schema version {}.",
                profile.id, profile.schema_version
            ),
        ));
    }
    if profile.id.trim().is_empty() || profile.display_name.trim().is_empty() {
        return Err(TargetError::new(
            TargetErrorCode::ProfileMalformed,
            "A stored target profile is missing its stable identity or display name.",
        ));
    }
    authorize_lifecycle(profile, LifecycleAction::Validate)?;
    if profile.kind == TargetKind::Managed && profile.managed_runtime_profile.is_none() {
        return Err(TargetError::new(
            TargetErrorCode::ProfileMalformed,
            "A managed target profile is missing its runtime identity.",
        ));
    }
    Ok(())
}

fn migrate_profile_value(mut value: Value) -> Result<(TargetProfile, bool), TargetError> {
    let object = value.as_object_mut().ok_or_else(|| {
        TargetError::new(
            TargetErrorCode::ProfileMalformed,
            "A stored target profile is not a JSON object.",
        )
    })?;
    let schema = object
        .get("schema_version")
        .and_then(Value::as_u64)
        .unwrap_or(0);
    let mut changed = false;
    match schema {
        0 => {
            object.insert(
                "schema_version".into(),
                Value::from(CURRENT_PROFILE_SCHEMA_VERSION),
            );
            if !object.contains_key("lifecycle_ownership") {
                let ownership = match object.get("kind").and_then(Value::as_str) {
                    Some("managed") => "desktop",
                    _ => "external",
                };
                object.insert("lifecycle_ownership".into(), Value::from(ownership));
            }
            changed = true;
        }
        value if value == u64::from(CURRENT_PROFILE_SCHEMA_VERSION) => {}
        other => {
            return Err(TargetError::new(
                TargetErrorCode::UnsupportedProfileSchema,
                format!("A stored target profile uses unsupported schema version {other}."),
            ));
        }
    }
    let profile: TargetProfile = serde_json::from_value(value).map_err(|_| {
        TargetError::new(
            TargetErrorCode::ProfileMalformed,
            "A stored target profile is malformed and could not be restored.",
        )
    })?;
    validate_profile_structure(&profile)?;
    Ok((profile, changed))
}

fn decode_state(
    store_schema: Option<Value>,
    profiles: Option<Value>,
    selected: Option<Value>,
) -> Result<DecodedState, TargetError> {
    let schema = store_schema.as_ref().and_then(Value::as_u64).unwrap_or(0);
    if schema > u64::from(CURRENT_STORE_SCHEMA_VERSION) {
        return Err(TargetError::new(
            TargetErrorCode::UnsupportedStoreSchema,
            format!("Target profile storage uses unsupported schema version {schema}."),
        ));
    }
    let mut decoded = DecodedState {
        changed: schema != u64::from(CURRENT_STORE_SCHEMA_VERSION),
        ..DecodedState::default()
    };
    let values = match profiles {
        None => Vec::new(),
        Some(Value::Array(values)) => values,
        Some(_) => {
            return Err(TargetError::new(
                TargetErrorCode::StoreCorrupt,
                "Stored target profiles are malformed.",
            ));
        }
    };
    let mut ids = BTreeSet::new();
    for value in values {
        let (profile, migrated) = migrate_profile_value(value)?;
        if !ids.insert(profile.id.clone()) {
            return Err(TargetError::new(
                TargetErrorCode::StoreCorrupt,
                "Stored target profiles contain duplicate identities.",
            ));
        }
        decoded.changed |= migrated;
        decoded.profiles.push(profile);
    }
    decoded.selected_profile_id = match selected {
        None | Some(Value::Null) => None,
        Some(Value::String(value)) if !value.trim().is_empty() => Some(value),
        Some(_) => {
            decoded.changed = true;
            decoded.issues.push(TargetError::new(
                TargetErrorCode::SelectedProfileMissing,
                "The selected target identity was malformed and has been cleared.",
            ));
            None
        }
    };
    if decoded
        .selected_profile_id
        .as_ref()
        .is_some_and(|selected| !ids.contains(selected))
    {
        decoded.selected_profile_id = None;
        decoded.changed = true;
        decoded.issues.push(TargetError::new(
            TargetErrorCode::SelectedProfileMissing,
            "The selected target no longer exists and has been cleared.",
        ));
    }
    Ok(decoded)
}

type ProfileStore = Arc<Store<Wry>>;

fn open_store(app: &AppHandle) -> Result<(ProfileStore, Option<TargetError>), TargetError> {
    match app.store(STORE_FILE) {
        Ok(store) => Ok((store, None)),
        Err(error) => {
            log::warn!("Recovering malformed target profile store: {error}");
            let store =
                app.store_builder(STORE_FILE)
                    .create_new()
                    .build()
                    .map_err(|recovery_error| {
                        TargetError::new(
                            TargetErrorCode::StoreUnavailable,
                            format!(
                                "Target profile storage could not be recovered: {recovery_error}"
                            ),
                        )
                    })?;
            Ok((
                store,
                Some(TargetError::new(
                    TargetErrorCode::StoreCorrupt,
                    "Target profile storage was corrupt and has been reset. Choose a target again.",
                )),
            ))
        }
    }
}

fn persist_state(store: &ProfileStore, decoded: &DecodedState) -> Result<(), TargetError> {
    store.set(STORE_SCHEMA_KEY, Value::from(CURRENT_STORE_SCHEMA_VERSION));
    store.set(
        PROFILES_KEY,
        serde_json::to_value(&decoded.profiles).map_err(|error| {
            TargetError::new(
                TargetErrorCode::StoreUnavailable,
                format!("Target profiles could not be serialized: {error}"),
            )
        })?,
    );
    match &decoded.selected_profile_id {
        Some(selected) => store.set(SELECTED_PROFILE_KEY, Value::from(selected.clone())),
        None => {
            store.delete(SELECTED_PROFILE_KEY);
        }
    }
    store.save().map_err(|error| {
        TargetError::new(
            TargetErrorCode::StoreUnavailable,
            format!("Target profiles could not be saved: {error}"),
        )
    })
}

fn load_state(app: &AppHandle) -> Result<(ProfileStore, DecodedState), TargetError> {
    let (store, load_issue) = open_store(app)?;
    let mut decoded = match decode_state(
        store.get(STORE_SCHEMA_KEY),
        store.get(PROFILES_KEY),
        store.get(SELECTED_PROFILE_KEY),
    ) {
        Ok(decoded) => decoded,
        Err(error) if error.code != TargetErrorCode::UnsupportedStoreSchema => {
            let mut recovered = DecodedState {
                changed: true,
                ..DecodedState::default()
            };
            recovered.issues.push(error);
            recovered
        }
        Err(error) => return Err(error),
    };
    if let Some(issue) = load_issue {
        decoded.issues.push(issue);
        decoded.changed = true;
    }
    if decoded.changed {
        persist_state(&store, &decoded)?;
        decoded.changed = false;
    }
    Ok((store, decoded))
}

fn state_snapshot(decoded: DecodedState) -> TargetState {
    TargetState {
        profiles: decoded.profiles,
        selected_profile_id: decoded.selected_profile_id,
        issues: decoded.issues,
    }
}

fn upsert_profile(decoded: &mut DecodedState, profile: TargetProfile) {
    if let Some(existing) = decoded
        .profiles
        .iter_mut()
        .find(|existing| existing.id == profile.id)
    {
        *existing = profile;
    } else {
        decoded.profiles.push(profile);
    }
    decoded
        .profiles
        .sort_by(|left, right| left.id.cmp(&right.id));
    decoded.changed = true;
}

pub fn initialize(
    app: &AppHandle,
    managed_runtime: Option<ManagedRuntimeProjection>,
    _desktop_version: &str,
) -> Result<TargetState, TargetError> {
    let (store, mut decoded) = load_state(app)?;
    let selected_was_managed = decoded
        .selected_profile_id
        .as_ref()
        .is_some_and(|selected| {
            decoded
                .profiles
                .iter()
                .any(|profile| profile.id == *selected && profile.kind == TargetKind::Managed)
        });
    if let Some(managed_runtime) = managed_runtime {
        let profile = reconcile_managed_profile(
            decoded
                .profiles
                .iter()
                .find(|existing| existing.kind == TargetKind::Managed),
            managed_runtime,
        );
        let id = profile.id.clone();
        let was_empty = decoded.profiles.is_empty();
        decoded
            .profiles
            .retain(|existing| existing.kind != TargetKind::Managed);
        upsert_profile(&mut decoded, profile);
        if selected_was_managed || (was_empty && decoded.selected_profile_id.is_none()) {
            decoded.selected_profile_id = Some(id);
            decoded.changed = true;
        }
    } else {
        let previous_length = decoded.profiles.len();
        decoded
            .profiles
            .retain(|profile| profile.kind != TargetKind::Managed);
        if decoded.profiles.len() != previous_length {
            decoded.changed = true;
        }
        if selected_was_managed {
            decoded.selected_profile_id = None;
            decoded.changed = true;
        }
    }
    if decoded.changed {
        persist_state(&store, &decoded)?;
    }
    Ok(state_snapshot(decoded))
}

fn apply_validation(profile: &mut TargetProfile, validated: ValidatedTarget) {
    profile.executable = validated.executable;
    profile.data_root = validated.data_root;
    profile.repository_root = validated.repository_root;
    profile.observed_vidxp_version = validated.product_version;
    profile.probe_schema_version = validated.probe_schema_version;
    profile.probe_protocol_version = validated.probe_protocol_version;
    profile.launch_protocol_version = validated.launch_protocol_version;
    profile.runtime = Some(validated.runtime);
    profile.frontend = validated.frontend;
    if profile.kind == TargetKind::ExistingLocal {
        profile.capabilities = validated.capabilities;
    }
    profile.surfaces = validated.surfaces;
    profile.last_successful_validation_at = Some(validated.validated_at);
    profile.validation_error = None;
}

pub fn current_state(app: &AppHandle) -> Result<TargetState, TargetError> {
    let (_, decoded) = load_state(app)?;
    Ok(state_snapshot(decoded))
}

pub fn replace_state(app: &AppHandle, state: TargetState) -> Result<(), TargetError> {
    let (store, _) = load_state(app)?;
    let decoded = DecodedState {
        profiles: state.profiles,
        selected_profile_id: state.selected_profile_id,
        issues: state.issues,
        changed: true,
    };
    persist_state(&store, &decoded)
}

pub fn selected_profile(app: &AppHandle) -> Result<TargetProfile, TargetError> {
    let state = current_state(app)?;
    state.selected_profile().cloned().ok_or_else(|| {
        TargetError::new(
            TargetErrorCode::SelectedProfileMissing,
            "Choose a VidXP target before continuing.",
        )
    })
}

pub fn validated_selected_profile_with_cancellation(
    app: &AppHandle,
    desktop_version: &str,
    cancellation: Option<&CancellationToken>,
) -> Result<TargetProfile, TargetError> {
    let profile = selected_profile(app)?;
    let validated = validate_executable_with(
        &profile.executable,
        desktop_version,
        |path, version, request_id| {
            collect_command_output(
                path,
                &[
                    "desktop-probe",
                    "--json",
                    "--desktop-version",
                    version,
                    "--request-id",
                    request_id,
                ],
                "The VidXP compatibility probe",
                cancellation,
            )
        },
    );
    persist_selected_validation(app, validated)
}

pub(crate) fn persist_selected_validation(
    app: &AppHandle,
    validated: Result<ValidatedTarget, TargetError>,
) -> Result<TargetProfile, TargetError> {
    let (store, mut decoded) = load_state(app)?;
    let selected = decoded.selected_profile_id.clone().ok_or_else(|| {
        TargetError::new(
            TargetErrorCode::SelectedProfileMissing,
            "Choose a VidXP target before continuing.",
        )
    })?;
    let profile = decoded
        .profiles
        .iter_mut()
        .find(|profile| profile.id == selected)
        .ok_or_else(|| {
            TargetError::new(
                TargetErrorCode::ProfileNotFound,
                "The selected VidXP target no longer exists.",
            )
        })?;
    match validated {
        Ok(validated) => {
            apply_validation(profile, validated);
            let result = profile.clone();
            decoded.changed = true;
            persist_state(&store, &decoded)?;
            Ok(result)
        }
        Err(error) => {
            profile.validation_error = Some(error.clone());
            decoded.changed = true;
            persist_state(&store, &decoded)?;
            Err(error)
        }
    }
}

pub fn adopt_validated(
    app: &AppHandle,
    validated: ValidatedTarget,
    display_name: Option<String>,
) -> Result<TargetState, TargetError> {
    let profile = local_profile(validated, display_name);
    let (store, mut decoded) = load_state(app)?;
    upsert_profile(&mut decoded, profile.clone());
    decoded.selected_profile_id = Some(profile.id.clone());
    persist_state(&store, &decoded)?;
    Ok(state_snapshot(decoded))
}

pub fn select_profile(
    app: &AppHandle,
    profile_id: &str,
    desktop_version: &str,
) -> Result<TargetState, TargetError> {
    let profile = current_state(app)?
        .profiles
        .into_iter()
        .find(|profile| profile.id == profile_id)
        .ok_or_else(|| {
            TargetError::new(
                TargetErrorCode::ProfileNotFound,
                "The selected VidXP target no longer exists.",
            )
        })?;
    let validated = validate_executable(&profile.executable, desktop_version)?;
    select_validated_profile(app, profile_id, validated)
}

pub(crate) fn select_validated_profile(
    app: &AppHandle,
    profile_id: &str,
    validated: ValidatedTarget,
) -> Result<TargetState, TargetError> {
    let (store, mut decoded) = load_state(app)?;
    let profile = decoded
        .profiles
        .iter_mut()
        .find(|profile| profile.id == profile_id)
        .ok_or_else(|| {
            TargetError::new(
                TargetErrorCode::ProfileNotFound,
                "The selected VidXP target no longer exists.",
            )
        })?;
    apply_validation(profile, validated);
    decoded.selected_profile_id = Some(profile_id.to_owned());
    decoded.changed = true;
    persist_state(&store, &decoded)?;
    Ok(state_snapshot(decoded))
}

pub fn delete_profile(app: &AppHandle, profile_id: &str) -> Result<TargetState, TargetError> {
    let (store, mut decoded) = load_state(app)?;
    if decoded
        .profiles
        .iter()
        .any(|profile| profile.id == profile_id && profile.kind == TargetKind::Managed)
    {
        return Err(TargetError::new(
            TargetErrorCode::ManagedProfileOwned,
            "The active Desktop-managed target cannot be forgotten. Create or select another target instead.",
        ));
    }
    let original_length = decoded.profiles.len();
    decoded.profiles.retain(|profile| profile.id != profile_id);
    if decoded.profiles.len() == original_length {
        return Err(TargetError::new(
            TargetErrorCode::ProfileNotFound,
            "The target profile no longer exists.",
        ));
    }
    if decoded.selected_profile_id.as_deref() == Some(profile_id) {
        decoded.selected_profile_id = None;
    }
    decoded.changed = true;
    persist_state(&store, &decoded)?;
    Ok(state_snapshot(decoded))
}

pub fn prepare_managed_activation(
    app: &AppHandle,
    managed_runtime: ManagedRuntimeProjection,
    validated: ValidatedTarget,
) -> Result<TargetState, TargetError> {
    let (_, mut decoded) = load_state(app)?;
    prepare_managed_state(&mut decoded, managed_runtime, Ok(validated))
}

fn prepare_managed_state(
    decoded: &mut DecodedState,
    managed_runtime: ManagedRuntimeProjection,
    validated: Result<ValidatedTarget, TargetError>,
) -> Result<TargetState, TargetError> {
    let mut profile = reconcile_managed_profile(
        decoded
            .profiles
            .iter()
            .find(|existing| existing.kind == TargetKind::Managed),
        managed_runtime,
    );
    apply_validation(&mut profile, validated?);
    decoded
        .profiles
        .retain(|existing| existing.kind != TargetKind::Managed);
    upsert_profile(decoded, profile.clone());
    decoded.selected_profile_id = Some(profile.id.clone());
    Ok(state_snapshot(decoded.clone()))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn profile(kind: TargetKind, ownership: LifecycleOwnership) -> TargetProfile {
        TargetProfile {
            id: "profile-1".into(),
            display_name: "Target".into(),
            schema_version: CURRENT_PROFILE_SCHEMA_VERSION,
            kind: kind.clone(),
            lifecycle_ownership: ownership,
            executable: PathBuf::from("/vidxp"),
            data_root: PathBuf::from("/data"),
            repository_root: PathBuf::from("/data/repositories/default"),
            observed_vidxp_version: "0.4.0-b".into(),
            probe_schema_version: 1,
            probe_protocol_version: 1,
            launch_protocol_version: 1,
            runtime: None,
            frontend: FrontendCapability::default(),
            last_successful_validation_at: Some(100),
            validation_error: None,
            managed_runtime_profile: (kind == TargetKind::Managed).then(|| "runtime-1".into()),
            capabilities: Vec::new(),
            surfaces: Vec::new(),
            model_directory: None,
        }
    }

    #[test]
    fn tagged_profiles_round_trip_with_explicit_ownership() {
        for expected in [
            profile(TargetKind::ExistingLocal, LifecycleOwnership::External),
            profile(TargetKind::Managed, LifecycleOwnership::Desktop),
        ] {
            let json = serde_json::to_value(&expected).expect("serialize profile");
            assert_eq!(
                json.get("kind").and_then(Value::as_str),
                Some(match expected.kind {
                    TargetKind::ExistingLocal => "existing_local",
                    TargetKind::Managed => "managed",
                })
            );
            assert_eq!(
                serde_json::from_value::<TargetProfile>(json).expect("deserialize profile"),
                expected
            );
        }
    }

    #[test]
    fn schema_zero_profile_migrates_ownership() {
        let mut value = serde_json::to_value(profile(
            TargetKind::ExistingLocal,
            LifecycleOwnership::External,
        ))
        .expect("serialize");
        let object = value.as_object_mut().expect("object");
        object.remove("schema_version");
        object.remove("lifecycle_ownership");

        let (migrated, changed) = migrate_profile_value(value).expect("migration");

        assert!(changed);
        assert_eq!(migrated.schema_version, CURRENT_PROFILE_SCHEMA_VERSION);
        assert_eq!(migrated.lifecycle_ownership, LifecycleOwnership::External);
    }

    #[test]
    fn corrupted_store_and_duplicate_profiles_are_rejected() {
        assert_eq!(
            decode_state(Some(Value::from(1)), Some(Value::from("bad")), None)
                .expect_err("corrupt")
                .code,
            TargetErrorCode::StoreCorrupt
        );
        let value = serde_json::to_value(profile(
            TargetKind::ExistingLocal,
            LifecycleOwnership::External,
        ))
        .expect("profile");
        assert_eq!(
            decode_state(
                Some(Value::from(1)),
                Some(Value::Array(vec![value.clone(), value])),
                None,
            )
            .expect_err("duplicate")
            .code,
            TargetErrorCode::StoreCorrupt
        );
    }

    #[test]
    fn missing_or_deleted_selected_profile_is_cleared() {
        let decoded = decode_state(
            Some(Value::from(1)),
            Some(Value::Array(Vec::new())),
            Some(Value::from("deleted")),
        )
        .expect("decode");

        assert_eq!(decoded.selected_profile_id, None);
        assert_eq!(
            decoded.issues[0].code,
            TargetErrorCode::SelectedProfileMissing
        );
        assert!(decoded.changed);
    }

    #[test]
    fn lifecycle_guards_block_actual_external_mutation_and_broad_stop() {
        let external = profile(TargetKind::ExistingLocal, LifecycleOwnership::External);
        for action in [LifecycleAction::Install, LifecycleAction::BroadProcessStop] {
            assert_eq!(
                authorize_lifecycle(&external, action)
                    .expect_err("external mutation")
                    .code,
                TargetErrorCode::LifecycleForbidden
            );
        }
        authorize_lifecycle(&external, LifecycleAction::Validate).expect("validation");
        authorize_lifecycle(&external, LifecycleAction::Launch).expect("launch");

        let managed = profile(TargetKind::Managed, LifecycleOwnership::Desktop);
        authorize_lifecycle(&managed, LifecycleAction::Install).expect("managed install");
        authorize_lifecycle(&managed, LifecycleAction::BroadProcessStop).expect("managed stop");
    }

    #[test]
    fn external_selection_cannot_act_on_an_installed_managed_runtime() {
        let external = profile(TargetKind::ExistingLocal, LifecycleOwnership::External);
        let error = authorize_managed_runtime_action(&external, "runtime-1")
            .expect_err("external selection must not control the managed runtime");
        assert_eq!(error.code, TargetErrorCode::LifecycleForbidden);

        let managed = profile(TargetKind::Managed, LifecycleOwnership::Desktop);
        authorize_managed_runtime_action(&managed, "runtime-1")
            .expect("matching managed selection");
        let mismatch = authorize_managed_runtime_action(&managed, "runtime-2")
            .expect_err("a different managed runtime must not be controlled");
        assert_eq!(mismatch.code, TargetErrorCode::ValidationRequired);
    }

    #[test]
    fn mismatched_kind_and_ownership_is_malformed() {
        let invalid = profile(TargetKind::ExistingLocal, LifecycleOwnership::Desktop);
        assert_eq!(
            authorize_lifecycle(&invalid, LifecycleAction::Validate)
                .expect_err("mismatch")
                .code,
            TargetErrorCode::ProfileMalformed
        );
    }

    #[test]
    fn stale_or_failed_validation_is_not_ready() {
        let mut target = profile(TargetKind::ExistingLocal, LifecycleOwnership::External);
        assert!(target.is_ready(100 + VALIDATION_MAX_AGE.as_secs()));
        assert!(!target.is_ready(101 + VALIDATION_MAX_AGE.as_secs()));
        target.validation_error = Some(TargetError::new(
            TargetErrorCode::UnsupportedLaunchProtocol,
            "unsupported launch protocol",
        ));
        assert!(!target.is_ready(100));
    }

    fn document(canonical: &Path, request_id: &str) -> ProbeDocument {
        let root = std::env::current_dir().expect("current directory");
        ProbeDocument {
            product: PRODUCT_ID.into(),
            product_version: "0.4.0-b".into(),
            schema_version: 1,
            protocol_version: SUPPORTED_PROBE_PROTOCOL_VERSION,
            launch_contract: ProbeLaunchContract {
                protocol_version: 2,
                surface: "browser".into(),
                command: "ui".into(),
            },
            request_id: request_id.into(),
            launcher: canonical.into(),
            runtime: ProbeRuntime {
                python_executable: root.join("python"),
                python_version: "3.14.6".into(),
                implementation: "CPython".into(),
                prefix: root.join("prefix"),
                base_prefix: root.join("base-prefix"),
            },
            data_root: root.join("data"),
            repository_root: root.join("data").join("repositories").join("default"),
            model_root: root.join("data").join("models"),
            capabilities: ProbeCapabilities::default(),
            search_capabilities: Some(Vec::new()),
            surfaces: Some(BTreeMap::new()),
        }
    }

    #[test]
    fn valid_probe_accepts_missing_optional_frontend() {
        let executable = std::env::current_exe().expect("current executable");
        let canonical = fs::canonicalize(executable).expect("canonical executable");
        let validated =
            validate_probe_document(&canonical, "nonce", document(&canonical, "nonce"), 100)
                .expect("valid probe");

        assert!(!validated.frontend.available);
        assert!(!validated.frontend.launchable);
    }

    #[test]
    fn probe_projects_installed_product_surfaces() {
        let executable = std::env::current_exe().expect("current executable");
        let canonical = fs::canonicalize(executable).expect("canonical executable");
        let mut probe = document(&canonical, "nonce");
        probe.search_capabilities = Some(vec!["scene".into()]);
        probe.surfaces.as_mut().expect("surfaces").insert(
            "mcp".into(),
            FrontendCapability {
                available: true,
                launchable: false,
                optional: true,
                code: "mcp_available".into(),
                message: "Available".into(),
                remediation: String::new(),
            },
        );
        probe
            .surfaces
            .as_mut()
            .expect("surfaces")
            .insert("server".into(), FrontendCapability::default());

        let validated =
            validate_probe_document(&canonical, "nonce", probe, 100).expect("valid probe");

        assert_eq!(validated.surfaces, ["mcp"]);
        let profile = local_profile(validated, None);
        assert_eq!(profile.capabilities, ["scene"]);
        assert_eq!(profile.surfaces, ["mcp"]);
    }

    #[test]
    fn probe_requires_feature_and_service_inventory() {
        let executable = std::env::current_exe().expect("current executable");
        let canonical = fs::canonicalize(executable).expect("canonical executable");
        let mut probe = document(&canonical, "nonce");
        probe.search_capabilities = None;
        probe.surfaces = None;

        assert_eq!(
            validate_probe_document(&canonical, "nonce", probe, 100)
                .expect_err("older management contract")
                .code,
            TargetErrorCode::RuntimeUpdateRequired
        );
    }

    #[test]
    fn validation_pipeline_reports_missing_malformed_timeout_and_failed_probes() {
        let missing = std::env::temp_dir().join("vidxp-missing-probe-executable");
        assert_eq!(
            validate_executable_with(&missing, "0.4.0-b", |_, _, _| {
                panic!("a missing executable must not be launched")
            })
            .expect_err("missing")
            .code,
            TargetErrorCode::ExecutableMissing
        );

        let executable = std::env::current_exe().expect("current executable");
        assert_eq!(
            validate_executable_with(&executable, "0.4.0-b", |_, _, _| {
                Ok(ProbeOutput {
                    success: true,
                    stdout: b"not json".to_vec(),
                    stderr: Vec::new(),
                })
            })
            .expect_err("malformed")
            .code,
            TargetErrorCode::MalformedProbe
        );
        assert_eq!(
            validate_executable_with(&executable, "0.4.0-b", |_, _, _| {
                Err(TargetError::new(TargetErrorCode::ProbeTimeout, "timed out"))
            })
            .expect_err("timeout")
            .code,
            TargetErrorCode::ProbeTimeout
        );
        let failed = validate_executable_with(&executable, "0.4.0-b", |_, _, _| {
            Ok(ProbeOutput {
                success: false,
                stdout: Vec::new(),
                stderr: b"embedded interpreter path is unavailable".to_vec(),
            })
        })
        .expect_err("failed");
        assert_eq!(failed.code, TargetErrorCode::ProbeFailed);
        assert!(failed.message.contains("embedded interpreter path"));
    }

    #[test]
    fn probe_rejects_identity_and_probe_contract_mismatches() {
        let executable = std::env::current_exe().expect("current executable");
        let canonical = fs::canonicalize(executable).expect("canonical executable");

        let mut non_vidxp = document(&canonical, "nonce");
        non_vidxp.product = "other".into();
        assert_eq!(
            validate_probe_document(&canonical, "nonce", non_vidxp, 100)
                .expect_err("product")
                .code,
            TargetErrorCode::NotVidxp
        );

        let wrong_nonce = document(&canonical, "wrong");
        assert_eq!(
            validate_probe_document(&canonical, "nonce", wrong_nonce, 100)
                .expect_err("nonce")
                .code,
            TargetErrorCode::ProbeChallengeMismatch
        );

        let mut wrong_launcher = document(&canonical, "nonce");
        wrong_launcher.launcher = fs::canonicalize(file!()).expect("source file");
        assert_eq!(
            validate_probe_document(&canonical, "nonce", wrong_launcher, 100)
                .expect_err("launcher")
                .code,
            TargetErrorCode::LauncherIdentityMismatch
        );

        let mut wrong_schema = document(&canonical, "nonce");
        wrong_schema.schema_version = 2;
        assert_eq!(
            validate_probe_document(&canonical, "nonce", wrong_schema, 100)
                .expect_err("schema")
                .code,
            TargetErrorCode::UnsupportedProbeSchema
        );

        let mut wrong_protocol = document(&canonical, "nonce");
        wrong_protocol.protocol_version = SUPPORTED_PROBE_PROTOCOL_VERSION - 1;
        assert_eq!(
            validate_probe_document(&canonical, "nonce", wrong_protocol, 100)
                .expect_err("protocol")
                .code,
            TargetErrorCode::RuntimeUpdateRequired
        );

        let mut newer_protocol = document(&canonical, "nonce");
        newer_protocol.protocol_version = SUPPORTED_PROBE_PROTOCOL_VERSION + 1;
        assert_eq!(
            validate_probe_document(&canonical, "nonce", newer_protocol, 100)
                .expect_err("newer protocol")
                .code,
            TargetErrorCode::UnsupportedProbeProtocol
        );
    }

    #[test]
    fn compatible_probe_accepts_a_different_reported_package_version() {
        let executable = std::env::current_exe().expect("current executable");
        let canonical = fs::canonicalize(executable).expect("canonical executable");
        let mut compatible = document(&canonical, "nonce");
        compatible.product_version = "0.3.0".into();

        let validated = validate_probe_document(&canonical, "nonce", compatible, 100)
            .expect("compatible contract");

        assert_eq!(validated.product_version, "0.3.0");
        assert_eq!(validated.launch_protocol_version, 2);
    }

    #[test]
    fn exact_and_canonical_symlink_launchers_preserve_selected_identity() {
        let executable = std::env::current_exe().expect("current executable");
        let canonical = fs::canonicalize(&executable).expect("canonical executable");
        let exact = document(&canonical, "nonce");
        assert!(validate_probe_document(&canonical, "nonce", exact, 100).is_ok());

        let link = std::env::temp_dir().join(format!(
            "vidxp-launcher-link-{}{}",
            std::process::id(),
            std::env::consts::EXE_SUFFIX
        ));
        #[cfg(windows)]
        let linked = std::os::windows::fs::symlink_file(&canonical, &link);
        #[cfg(unix)]
        let linked = std::os::unix::fs::symlink(&canonical, &link);
        if linked.is_err() {
            return;
        }
        let linked_document = document(&link, "nonce");
        assert!(validate_probe_document(&canonical, "nonce", linked_document, 100).is_ok());
        fs::remove_file(link).expect("remove launcher symlink");
    }

    #[cfg(windows)]
    #[test]
    fn extensionless_windows_console_script_identity_resolves_only_selected_shim() {
        let root =
            std::env::temp_dir().join(format!("vidxp-launcher-identity-{}", std::process::id()));
        fs::create_dir_all(&root).expect("launcher test directory");
        let selected = root.join("vidxp.exe");
        let colliding = root.join("vidxp.com");
        let similar = root.join("vidxp-helper.exe");
        fs::write(&selected, b"shim").expect("selected shim");
        fs::write(&colliding, b"different PATHEXT sibling").expect("colliding shim");
        fs::write(&similar, b"other").expect("similar shim");
        let canonical = fs::canonicalize(&selected).expect("canonical selected shim");

        let mut extensionless = document(&canonical, "nonce");
        extensionless.launcher = root.join("vidxp");
        assert!(validate_probe_document(&canonical, "nonce", extensionless, 100).is_ok());

        let mut unrelated = document(&canonical, "nonce");
        unrelated.launcher = similar;
        assert_eq!(
            validate_probe_document(&canonical, "nonce", unrelated, 100)
                .expect_err("similar sibling")
                .code,
            TargetErrorCode::LauncherIdentityMismatch
        );

        let mut missing = document(&canonical, "nonce");
        missing.launcher = root.join("missing");
        assert_eq!(
            validate_probe_document(&canonical, "nonce", missing, 100)
                .expect_err("missing launcher")
                .code,
            TargetErrorCode::LauncherIdentityMismatch
        );
        fs::remove_dir_all(root).expect("remove launcher test directory");
    }

    #[cfg(not(windows))]
    #[test]
    fn non_windows_launcher_identity_does_not_resolve_executable_suffixes() {
        let root =
            std::env::temp_dir().join(format!("vidxp-launcher-identity-{}", std::process::id()));
        fs::create_dir_all(&root).expect("launcher test directory");
        let selected = root.join("vidxp.exe");
        fs::write(&selected, b"shim").expect("selected shim");
        let canonical = fs::canonicalize(&selected).expect("canonical selected shim");
        let mut extensionless = document(&canonical, "nonce");
        extensionless.launcher = root.join("vidxp");

        assert_eq!(
            validate_probe_document(&canonical, "nonce", extensionless, 100)
                .expect_err("ordinary non-Windows path")
                .code,
            TargetErrorCode::LauncherIdentityMismatch
        );
        fs::remove_dir_all(root).expect("remove launcher test directory");
    }

    #[test]
    fn probe_rejects_an_incompatible_launch_protocol() {
        let executable = std::env::current_exe().expect("current executable");
        let canonical = fs::canonicalize(executable).expect("canonical executable");
        let mut incompatible = document(&canonical, "nonce");
        incompatible.launch_contract.protocol_version = 1;

        assert_eq!(
            validate_probe_document(&canonical, "nonce", incompatible, 100)
                .expect_err("launch protocol")
                .code,
            TargetErrorCode::UnsupportedLaunchProtocol
        );
    }

    #[test]
    fn inspection_accepts_a_compatible_contract_with_a_different_package_version() {
        let executable = std::env::current_exe().expect("current executable");
        let inspected = inspect_executable_with(
            &executable,
            "0.4.0-b",
            |canonical, _, request_id| {
                let mut payload = document(canonical, request_id);
                payload.product_version = "0.3.0".into();
                Ok(ProbeOutput {
                    success: true,
                    stdout: serde_json::to_vec(&payload).expect("probe json"),
                    stderr: Vec::new(),
                })
            },
            |_| panic!("a compatible probe must not fall back to package version"),
        )
        .expect("inspection");

        assert_eq!(inspected.state, InspectionState::ReadyToUse);
        assert!(inspected.adoptable);
        assert_eq!(inspected.reported_version.as_deref(), Some("0.3.0"));
        assert!(inspected.probe_compatible);
        assert!(inspected.launch_compatible);
    }

    #[test]
    fn version_fallback_is_diagnostic_only_and_cannot_make_a_target_adoptable() {
        let executable = std::env::current_exe().expect("current executable");
        let inspected = inspect_executable_with(
            &executable,
            "0.4.0-b",
            |_, _, _| {
                Ok(ProbeOutput {
                    success: false,
                    stdout: Vec::new(),
                    stderr: b"No such command: desktop-probe".to_vec(),
                })
            },
            |_| {
                Ok(ProbeOutput {
                    success: true,
                    stdout: b"VidXP 0.4.0b0\n".to_vec(),
                    stderr: Vec::new(),
                })
            },
        )
        .expect("inspection");

        assert_eq!(inspected.state, InspectionState::UpdateRequired);
        assert!(!inspected.adoptable);
        assert_eq!(inspected.reported_version.as_deref(), Some("0.4.0b0"));
        assert!(!inspected.probe_compatible);
        assert!(inspected.validated.is_none());
    }

    #[test]
    fn executable_that_cannot_report_a_version_is_not_adoptable() {
        let executable = std::env::current_exe().expect("current executable");
        let inspected = inspect_executable_with(
            &executable,
            "0.4.0-b",
            |_, _, _| {
                Ok(ProbeOutput {
                    success: false,
                    stdout: Vec::new(),
                    stderr: b"missing dependency".to_vec(),
                })
            },
            |_| {
                Ok(ProbeOutput {
                    success: false,
                    stdout: Vec::new(),
                    stderr: b"ModuleNotFoundError: SQLAlchemy".to_vec(),
                })
            },
        )
        .expect("inspection");

        assert_eq!(inspected.state, InspectionState::CannotStart);
        assert!(!inspected.adoptable);
        assert!(
            inspected
                .remediation
                .contains("package-management workflow")
        );
    }

    #[cfg(windows)]
    #[test]
    fn real_windows_console_script_passes_the_desktop_inspection_path_when_requested() {
        let Some(executable) = std::env::var_os("VIDXP_DESKTOP_INTEGRATION_EXECUTABLE") else {
            return;
        };
        let inspected =
            inspect_executable(Path::new(&executable), "0.4.0-b").expect("real Desktop inspection");
        let validated = inspected.validated.expect("validated target");

        assert_eq!(inspected.state, InspectionState::ReadyToUse);
        assert!(inspected.adoptable);
        if let Some(expected_version) =
            std::env::var_os("VIDXP_DESKTOP_INTEGRATION_EXPECTED_VERSION")
        {
            assert_eq!(
                validated.product_version,
                expected_version.to_string_lossy()
            );
        } else {
            assert!(!validated.product_version.trim().is_empty());
        }
        assert_eq!(
            validated.probe_protocol_version,
            SUPPORTED_PROBE_PROTOCOL_VERSION
        );
        assert_eq!(validated.launch_protocol_version, 2);
        assert_eq!(validated.runtime.python_version, "3.14.0");
        assert!(validated.frontend.launchable);
        let expected_data_root =
            std::env::var_os("VIDXP_DESKTOP_INTEGRATION_DATA_ROOT").expect("integration data root");
        assert_eq!(validated.data_root, PathBuf::from(expected_data_root));
    }

    #[test]
    fn managed_projection_preserves_current_runtime_identity_without_claiming_validation() {
        let projected = managed_profile(ManagedRuntimeProjection {
            runtime_profile: "abc-123".into(),
            executable: PathBuf::from("/runtime/bin/vidxp"),
            data_root: PathBuf::from("/data"),
            repository_root: PathBuf::from("/data/repositories/default"),
            model_directory: PathBuf::from("/models"),
            package_version: "0.4.0-b".into(),
            capabilities: vec!["scene".into()],
            surfaces: vec!["browser".into()],
        });

        assert_eq!(projected.id, "managed-abc-123");
        assert_eq!(projected.kind, TargetKind::Managed);
        assert_eq!(projected.lifecycle_ownership, LifecycleOwnership::Desktop);
        assert_eq!(
            projected.managed_runtime_profile.as_deref(),
            Some("abc-123")
        );
        assert!(projected.last_successful_validation_at.is_none());
        assert_eq!(
            projected.validation_error.expect("validation error").code,
            TargetErrorCode::ValidationRequired
        );
    }

    #[test]
    fn managed_reconciliation_refreshes_authoritative_fields_and_preserves_name() {
        let mut existing = profile(TargetKind::Managed, LifecycleOwnership::Desktop);
        existing.id = "managed-runtime-2".into();
        existing.display_name = "Editing workstation".into();
        existing.executable = PathBuf::from("/stale/vidxp");
        existing.model_directory = Some(PathBuf::from("/legacy/models"));
        existing.capabilities = vec!["dialogue".into()];

        let reconciled = reconcile_managed_profile(
            Some(&existing),
            ManagedRuntimeProjection {
                runtime_profile: "runtime-2".into(),
                executable: PathBuf::from("/current/vidxp"),
                data_root: PathBuf::from("/current/data"),
                repository_root: PathBuf::from("/current/data/repositories/default"),
                model_directory: PathBuf::from("/current/models"),
                package_version: "0.5.0".into(),
                capabilities: vec!["scene".into()],
                surfaces: vec!["browser".into()],
            },
        );

        assert_eq!(reconciled.display_name, "Editing workstation");
        assert_eq!(reconciled.executable, PathBuf::from("/current/vidxp"));
        assert_eq!(reconciled.data_root, PathBuf::from("/current/data"));
        assert_eq!(
            reconciled.model_directory,
            Some(PathBuf::from("/current/models"))
        );
        assert_eq!(reconciled.capabilities, ["scene"]);
        assert_eq!(reconciled.surfaces, ["browser"]);
        assert_eq!(reconciled.observed_vidxp_version, "0.5.0");
    }

    fn managed_projection_for_test() -> ManagedRuntimeProjection {
        ManagedRuntimeProjection {
            runtime_profile: "runtime-new".into(),
            executable: PathBuf::from("/runtime/bin/vidxp"),
            data_root: PathBuf::from("/data"),
            repository_root: PathBuf::from("/data/repositories/default"),
            model_directory: PathBuf::from("/models"),
            package_version: "0.5.0".into(),
            capabilities: vec!["scene".into()],
            surfaces: vec!["browser".into()],
        }
    }

    fn validated_managed_target() -> ValidatedTarget {
        ValidatedTarget {
            executable: PathBuf::from("/runtime/bin/vidxp"),
            product_version: "0.5.0".into(),
            probe_schema_version: 1,
            probe_protocol_version: SUPPORTED_PROBE_PROTOCOL_VERSION,
            launch_protocol_version: 2,
            runtime: RuntimeIdentity {
                python_executable: PathBuf::from("/runtime/bin/python"),
                python_version: "3.14.6".into(),
                implementation: "CPython".into(),
                prefix: PathBuf::from("/runtime"),
                base_prefix: PathBuf::from("/python"),
            },
            data_root: PathBuf::from("/data"),
            repository_root: PathBuf::from("/data/repositories/default"),
            model_root: PathBuf::from("/models"),
            frontend: FrontendCapability {
                available: true,
                launchable: true,
                optional: true,
                code: "frontend_available".into(),
                message: "Available".into(),
                remediation: String::new(),
            },
            capabilities: vec!["scene".into()],
            surfaces: vec!["browser".into(), "mcp".into(), "server".into()],
            validated_at: 200,
        }
    }

    #[test]
    fn failed_managed_candidate_probe_cannot_replace_target_state() {
        let old = profile(TargetKind::Managed, LifecycleOwnership::Desktop);
        let mut decoded = DecodedState {
            profiles: vec![old.clone()],
            selected_profile_id: Some(old.id.clone()),
            ..DecodedState::default()
        };

        let error = prepare_managed_state(
            &mut decoded,
            managed_projection_for_test(),
            Err(TargetError::new(
                TargetErrorCode::UnsupportedLaunchProtocol,
                "candidate launch contract failed",
            )),
        )
        .expect_err("candidate must not activate");

        assert_eq!(error.code, TargetErrorCode::UnsupportedLaunchProtocol);
        assert_eq!(decoded.profiles.as_slice(), std::slice::from_ref(&old));
        assert_eq!(
            decoded.selected_profile_id.as_deref(),
            Some(old.id.as_str())
        );
    }

    #[test]
    fn managed_activation_replaces_all_stale_managed_profiles() {
        let external = profile(TargetKind::ExistingLocal, LifecycleOwnership::External);
        let mut old = profile(TargetKind::Managed, LifecycleOwnership::Desktop);
        old.id = "managed-old".into();
        old.display_name = "Editing workstation".into();
        let mut stale = old.clone();
        stale.id = "managed-stale".into();
        let mut decoded = DecodedState {
            profiles: vec![external.clone(), old, stale],
            selected_profile_id: Some("managed-old".into()),
            ..DecodedState::default()
        };

        let state = prepare_managed_state(
            &mut decoded,
            managed_projection_for_test(),
            Ok(validated_managed_target()),
        )
        .expect("managed candidate");

        let managed: Vec<_> = state
            .profiles
            .iter()
            .filter(|profile| profile.kind == TargetKind::Managed)
            .collect();
        assert_eq!(managed.len(), 1);
        assert_eq!(managed[0].id, "managed-runtime-new");
        assert_eq!(managed[0].display_name, "Editing workstation");
        assert_eq!(
            state.selected_profile_id.as_deref(),
            Some("managed-runtime-new")
        );
        assert!(state.profiles.contains(&external));
    }
}
