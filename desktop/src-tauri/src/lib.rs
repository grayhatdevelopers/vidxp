use std::{
    borrow::Cow,
    collections::{BTreeMap, BTreeSet},
    env, fs,
    io::{self, Write},
    net::{SocketAddr, TcpListener, TcpStream},
    path::{Path, PathBuf},
    process::{Child, Command, Output, Stdio},
    sync::{
        Mutex,
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
use tauri_plugin_shell::{
    ShellExt,
    process::{Command as ShellCommand, CommandChild, CommandEvent},
};
use wait_timeout::ChildExt;

const RUNTIME_MANIFEST_BYTES: &[u8] = include_bytes!("../../runtime-manifest.json");
const RUNTIME_CONSTRAINTS_BYTES: &[u8] = include_bytes!("../../runtime-constraints.txt");
const PRODUCT_DATA_DIRECTORY_NAME: &str = "VidXP";

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
struct RuntimeStatus {
    ready: bool,
    package_version: String,
    capabilities: Vec<String>,
    surfaces: Vec<String>,
    model_directory: String,
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

#[derive(Clone, Deserialize, Serialize)]
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
}

struct ManagedUi {
    process: Child,
    url: String,
}

struct DesktopState {
    ui_process: Mutex<Option<ManagedUi>>,
    operation_process: Mutex<Option<CommandChild>>,
    operation_worker_runtime: Mutex<Option<PathBuf>>,
    operation_active: AtomicBool,
    shutdown_started: AtomicBool,
}

impl Default for DesktopState {
    fn default() -> Self {
        Self {
            ui_process: Mutex::new(None),
            operation_process: Mutex::new(None),
            operation_worker_runtime: Mutex::new(None),
            operation_active: AtomicBool::new(false),
            shutdown_started: AtomicBool::new(false),
        }
    }
}

struct OperationGuard<'a> {
    active: &'a AtomicBool,
}

impl Drop for OperationGuard<'_> {
    fn drop(&mut self) {
        self.active.store(false, Ordering::Release);
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
    if cfg!(windows) {
        if let Some(local) = env::var_os("LOCALAPPDATA") {
            directories.push(
                PathBuf::from(local)
                    .join("Microsoft")
                    .join("WinGet")
                    .join("Links"),
            );
        }
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

fn combined_output(output: &Output) -> String {
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
    hide_child_console(&mut command);
    command.env_clear();
    command.envs(clean_environment(paths));
    command
}

#[cfg(windows)]
fn hide_child_console(command: &mut Command) {
    use std::os::windows::process::CommandExt;

    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    command.creation_flags(CREATE_NO_WINDOW);
}

#[cfg(not(windows))]
fn hide_child_console(_command: &mut Command) {}

fn checked_output(mut command: Command, operation: &str) -> Result<Output, String> {
    hide_child_console(&mut command);
    let output = command
        .output()
        .map_err(|error| format!("{operation} could not start: {error}"))?;
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

async fn supervised_output(
    state: &DesktopState,
    command: ShellCommand,
    operation: &str,
) -> Result<(Vec<u8>, Vec<u8>), String> {
    if state.shutdown_started.load(Ordering::Acquire) {
        return Err(format!(
            "{operation} was cancelled because VidXP is closing."
        ));
    }
    let (mut events, child) = command
        .spawn()
        .map_err(|error| format!("{operation} could not start: {error}"))?;
    {
        let mut active_child = state
            .operation_process
            .lock()
            .map_err(|_| "The setup process supervisor is unavailable.".to_string())?;
        if active_child.is_some() {
            drop(active_child);
            let _ = child.kill();
            return Err("Another desktop setup process is already running.".into());
        }
        *active_child = Some(child);
    }

    let mut stdout = Vec::new();
    let mut stderr = Vec::new();
    let mut exit_code = None;
    while let Some(event) = events.recv().await {
        match event {
            CommandEvent::Stdout(bytes) => stdout.extend(bytes),
            CommandEvent::Stderr(bytes) => stderr.extend(bytes),
            CommandEvent::Error(error) => stderr.extend(error.as_bytes()),
            CommandEvent::Terminated(status) => {
                exit_code = status.code;
                break;
            }
            _ => {}
        }
    }
    if let Ok(mut active_child) = state.operation_process.lock() {
        active_child.take();
    }
    if exit_code == Some(0) {
        return Ok((stdout, stderr));
    }
    let detail = String::from_utf8_lossy(&stderr).trim().to_owned();
    Err(format!(
        "{operation} failed{}: {detail}",
        exit_code.map_or_else(String::new, |code| format!(" with exit code {code}"))
    ))
}

async fn uv_output(
    app: &AppHandle,
    state: &DesktopState,
    paths: &DesktopPaths,
    arguments: Vec<String>,
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
    supervised_output(state, command, operation).await?;
    Ok(())
}

fn run_vidxp(
    runtime: &Path,
    paths: &DesktopPaths,
    arguments: &[String],
    operation: &str,
) -> Result<Output, String> {
    let mut command = configured_command(&executable(runtime, "vidxp"), paths);
    command
        .arg("--index-dir")
        .arg(&paths.repository)
        .args(arguments);
    checked_output(command, operation)
}

async fn run_vidxp_supervised(
    app: &AppHandle,
    state: &DesktopState,
    runtime: &Path,
    paths: &DesktopPaths,
    arguments: &[String],
    operation: &str,
) -> Result<(), String> {
    let mut command = app
        .shell()
        .command(executable(runtime, "vidxp"))
        .env_clear();
    for (key, value) in clean_environment(paths) {
        command = command.env(key, value);
    }
    command = command
        .arg("--index-dir")
        .arg(&paths.repository)
        .args(arguments);
    supervised_output(state, command, operation).await?;
    Ok(())
}

#[tauri::command]
fn runtime_manifest() -> Result<RuntimeManifest, String> {
    manifest()
}

#[tauri::command]
fn media_runtime_status() -> MediaRuntimeStatus {
    inspect_media_runtime()
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
) -> Result<MediaRuntimeStatus, String> {
    let current = inspect_media_runtime();
    if current.ready {
        return Ok(current);
    }
    if state
        .operation_active
        .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
        .is_err()
    {
        return Err("Another install or model-preparation operation is active.".into());
    }
    let _operation_guard = OperationGuard {
        active: &state.operation_active,
    };
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
    supervised_output(
        &state,
        command,
        &format!("{} FFmpeg installation", plan.manager),
    )
    .await?;
    let status = inspect_media_runtime();
    if !status.ready {
        return Err(format!(
            "FFmpeg installation finished but verification failed: {}",
            status.errors.join(" ")
        ));
    }
    Ok(status)
}

#[tauri::command]
fn runtime_status(app: AppHandle) -> Result<RuntimeStatus, String> {
    let manifest = manifest()?;
    let mut paths = desktop_paths(&app)?;
    let default_model_directory = paths.models.to_string_lossy().into_owned();
    if let Err(detail) = verified_media_runtime() {
        return Ok(RuntimeStatus {
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
async fn install_runtime(
    app: AppHandle,
    state: tauri::State<'_, DesktopState>,
    request: InstallRequest,
) -> Result<InstallResult, String> {
    if state
        .operation_active
        .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
        .is_err()
    {
        return Err("Another install or model-preparation operation is active.".into());
    }
    let _operation_guard = OperationGuard {
        active: &state.operation_active,
    };
    let manifest = manifest()?;
    let capabilities = selected_capabilities(&manifest, &request.capabilities)?;
    let surfaces = selected_surfaces(&manifest, &request.surfaces)?;
    let media_runtime = verified_media_runtime()?;
    let mut paths = desktop_paths(&app)?;
    paths.models = model_directory(&paths, request.model_directory.as_deref())?;
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
            &state,
            &paths,
            vec![
                "venv".into(),
                staging.to_string_lossy().into_owned(),
                "--python".into(),
                manifest.python_version.clone(),
                "--managed-python".into(),
                "--no-config".into(),
            ],
            "Managed Python setup",
        )
        .await?;

        fs::write(&constraints, normalized_runtime_constraints().as_ref())
            .map_err(|error| format!("Could not write runtime constraints: {error}"))?;

        uv_output(
            &app,
            &state,
            &paths,
            package_acquisition_arguments(&manifest, &executable(&staging, "python")),
            "VidXP package acquisition",
        )
        .await?;

        uv_output(
            &app,
            &state,
            &paths,
            dependency_installation_arguments(
                &manifest,
                &capabilities,
                &surfaces,
                &executable(&staging, "python"),
                &constraints,
                !cfg!(target_os = "macos"),
            ),
            "VidXP package installation",
        )
        .await?;

        run_vidxp_supervised(
            &app,
            &state,
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
            "FFmpeg configuration",
        )
        .await?;

        let doctor_arguments = capability_command_arguments(&manifest, "doctor", &capabilities);
        run_vidxp_supervised(
            &app,
            &state,
            &staging,
            &paths,
            &doctor_arguments,
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
                &app,
                &state,
                &staging,
                &paths,
                &prepare_arguments,
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
        let cleanup_error = if staging.exists() {
            fs::remove_dir_all(&staging).err()
        } else {
            None
        };
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
    fs::rename(&staging, &runtime)
        .map_err(|error| format!("Could not finalize the validated runtime: {error}"))?;
    let active = ActiveRuntime {
        schema_version: 2,
        manifest_sha256: manifest_digest(),
        profile,
        package_version: manifest.package_version.clone(),
        capabilities: capabilities.clone(),
        surfaces: surfaces.clone(),
        model_directory: paths.models.clone(),
    };
    write_active_runtime(&paths, &active)?;

    Ok(InstallResult {
        package_version: manifest.package_version,
        capabilities,
        surfaces,
        model_directory: paths.models.to_string_lossy().into_owned(),
        prepared: request.prepare_models,
    })
}

fn start_ui(app: &AppHandle, state: &DesktopState) -> Result<String, String> {
    let mut paths = desktop_paths(&app)?;
    let active = active_runtime(&paths)?;
    if !active.surfaces.iter().any(|surface| surface == "browser") {
        return Err(
            "The browser interface is not installed. Reconfigure VidXP and select Browser interface."
                .into(),
        );
    }
    paths.models = active.model_directory.clone();
    let runtime = runtime_directory(&paths, &active);
    let mut active_process = state
        .ui_process
        .lock()
        .map_err(|_| "The desktop process supervisor is unavailable.".to_string())?;
    if let Some(ui) = active_process.as_mut() {
        if ui
            .process
            .try_wait()
            .map_err(|error| format!("Could not inspect the interface process: {error}"))?
            .is_none()
        {
            return Ok(ui.url.clone());
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

    let mut command = configured_command(&executable(&runtime, "vidxp"), &paths);
    command
        .arg("--index-dir")
        .arg(&paths.repository)
        .args(["ui", "--host", "127.0.0.1", "--port", &port.to_string()])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    let mut process = command
        .spawn()
        .map_err(|error| format!("Could not start the VidXP interface: {error}"))?;

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
            });
            return Ok(url);
        }
        thread::sleep(Duration::from_millis(100));
    }
    let _ = process.kill();
    let _ = process.wait();
    Err("The VidXP interface did not become ready in 30 seconds.".into())
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
    desktop_paths(app)
        .and_then(|paths| active_runtime(&paths))
        .is_ok()
}

fn browser_surface_configured(app: &AppHandle) -> bool {
    desktop_paths(app)
        .and_then(|paths| active_runtime(&paths))
        .is_ok_and(|active| active.surfaces.iter().any(|surface| surface == "browser"))
}

fn open_ui_in_browser(app: &AppHandle, state: &DesktopState) -> Result<(), String> {
    let url = start_ui(app, state)?;
    app.opener()
        .open_url(&url, None::<&str>)
        .map_err(|error| format!("Could not open VidXP in the default browser: {error}"))?;
    hide_main_window(app)
}

fn open_or_show(app: &AppHandle) {
    if !browser_surface_configured(app) {
        show_main_window(app);
        return;
    }
    let app = app.clone();
    thread::spawn(move || {
        let state = app.state::<DesktopState>();
        if let Err(error) = open_ui_in_browser(&app, &state) {
            show_main_window(&app);
            app.dialog()
                .message(error)
                .title("VidXP could not open")
                .kind(MessageDialogKind::Error)
                .blocking_show();
        }
    });
}

fn begin_shutdown(app: &AppHandle) {
    if app
        .state::<DesktopState>()
        .shutdown_started
        .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
        .is_err()
    {
        return;
    }
    log::info!("VidXP supervised shutdown requested");
    shutdown(app);
    log::info!("VidXP supervised shutdown completed");
    std::process::exit(0);
}

#[tauri::command]
fn launch_ui(app: AppHandle, state: tauri::State<'_, DesktopState>) -> Result<(), String> {
    open_ui_in_browser(&app, &state)
}

#[tauri::command]
fn hide_to_tray(app: AppHandle) -> Result<(), String> {
    if !configured_runtime(&app) {
        return Err("Local video processing has not been configured yet.".into());
    }
    hide_main_window(&app)
}

fn create_tray(app: &tauri::App) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", "Open VidXP", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit VidXP", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open, &quit])?;
    let mut tray = TrayIconBuilder::with_id("vidxp")
        .tooltip("VidXP")
        .menu(&menu)
        .show_menu_on_left_click(true)
        .on_menu_event(|app, event| match event.id().as_ref() {
            "open" => open_or_show(app),
            "quit" => begin_shutdown(app),
            _ => {}
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
        .args(["jobs", "stop-worker"])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    let Ok(mut process) = command.spawn() else {
        return;
    };
    match process.wait_timeout(Duration::from_secs(5)) {
        Ok(Some(_)) => {}
        _ => {
            let _ = process.kill();
            let _ = process.wait_timeout(Duration::from_secs(1));
        }
    }
}

fn shutdown(app: &AppHandle) {
    log::info!("Stopping active VidXP processes");
    let state = app.state::<DesktopState>();
    if let Ok(mut active_operation) = state.operation_process.lock() {
        if let Some(process) = active_operation.take() {
            let _ = process.kill();
        }
    }
    if let Ok(mut active_process) = state.ui_process.lock() {
        if let Some(mut ui) = active_process.take() {
            let _ = ui.process.kill();
            match ui.process.wait_timeout(Duration::from_secs(5)) {
                Ok(Some(_)) => {}
                _ => {
                    let _ = ui.process.kill();
                    let _ = ui.process.wait_timeout(Duration::from_secs(1));
                }
            }
        }
    }
    let Ok(mut paths) = desktop_paths(app) else {
        log::warn!("Could not resolve desktop paths during shutdown");
        return;
    };
    if let Ok(mut operation_worker) = state.operation_worker_runtime.lock() {
        if let Some(runtime) = operation_worker.take() {
            stop_worker(&runtime, &paths);
        }
    }
    let Ok(active) = active_runtime(&paths) else {
        log::info!("No active VidXP runtime needs worker shutdown");
        return;
    };
    paths.models = active.model_directory.clone();
    let runtime = runtime_directory(&paths, &active);
    stop_worker(&runtime, &paths);
    log::info!("Active VidXP worker shutdown finished");
}

pub fn run() {
    let builder = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            open_or_show(app);
        }))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_log::Builder::new().build())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_shell::init())
        .manage(DesktopState::default())
        .setup(|app| {
            migrate_legacy_shared_data(app.handle()).map_err(io::Error::other)?;
            create_tray(app)?;
            if !configured_runtime(app.handle()) {
                show_main_window(app.handle());
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            runtime_manifest,
            media_runtime_status,
            choose_model_directory,
            install_media_runtime,
            runtime_status,
            install_runtime,
            launch_ui,
            hide_to_tray
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
            if app_handle
                .state::<DesktopState>()
                .shutdown_started
                .load(Ordering::Acquire)
            {
                return;
            }
            api.prevent_close();
            if configured_runtime(app_handle) {
                let _ = hide_main_window(app_handle);
            } else {
                begin_shutdown(app_handle);
            }
        }
        RunEvent::ExitRequested { api, .. }
            if !app_handle
                .state::<DesktopState>()
                .shutdown_started
                .load(Ordering::Acquire) =>
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
        base_package_specification, capability_command_arguments,
        dependency_installation_arguments, desktop_paths_from_roots, display_command, manifest,
        normalize_line_endings, normalized_runtime_constraints, package_acquisition_arguments,
        package_index, package_specification, required_encoder_missing, selected_capabilities,
        selected_surfaces,
    };
    use std::path::{Path, PathBuf};

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

        assert_eq!(base_package_specification(&manifest), "vidxp==0.2.1-b.1");
        assert_eq!(
            package_specification(
                &manifest,
                &["scene".into(), "dialogue".into()],
                &["browser".into()],
            ),
            "vidxp[dialogue,frontend,scene]==0.2.1-b.1"
        );
        assert_eq!(
            package_specification(&manifest, &["scene".into()], &[]),
            "vidxp[scene]==0.2.1-b.1"
        );
        assert_eq!(
            selected_surfaces(&manifest, &["browser".into(), "browser".into()])
                .expect("surface selection"),
            ["browser"]
        );
        assert!(selected_surfaces(&manifest, &["unknown".into()]).is_err());
    }

    #[test]
    fn prerelease_package_and_dependencies_use_separate_indexes() {
        let manifest = manifest().expect("manifest");
        let python = Path::new("managed-python");
        let constraints = Path::new("runtime-constraints.txt");
        let acquisition = package_acquisition_arguments(&manifest, python);
        let dependencies = dependency_installation_arguments(
            &manifest,
            &["scene".into()],
            &[],
            python,
            constraints,
            true,
        );

        assert_eq!(
            package_index(&manifest.package_version),
            "https://test.pypi.org/simple"
        );
        assert_eq!(package_index("0.3.0"), "https://pypi.org/simple");
        assert_eq!(manifest.dependency_index, "https://pypi.org/simple");
        assert!(acquisition.iter().any(|item| item == "--no-deps"));
        assert!(
            acquisition
                .iter()
                .any(|item| item == "https://test.pypi.org/simple")
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
}
