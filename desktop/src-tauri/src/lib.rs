use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    io::Write,
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
use tauri::{AppHandle, Manager, RunEvent};
use tauri_plugin_shell::{
    ShellExt,
    process::{Command as ShellCommand, CommandChild, CommandEvent},
};
use wait_timeout::ChildExt;

const RUNTIME_MANIFEST_BYTES: &[u8] = include_bytes!("../../runtime-manifest.json");

#[derive(Clone, Deserialize, Serialize)]
struct CapabilitySpec {
    extra: String,
    modality: String,
    label: String,
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
    package_index: String,
    python_version: String,
    uv_version: String,
    always_install_extras: Vec<String>,
    capabilities: BTreeMap<String, CapabilitySpec>,
    media_runtime: MediaRuntimeSpec,
}

#[derive(Deserialize)]
struct InstallRequest {
    capabilities: Vec<String>,
    prepare_models: bool,
}

#[derive(Serialize)]
struct InstallResult {
    package_version: String,
    capabilities: Vec<String>,
    prepared: bool,
}

#[derive(Serialize)]
struct RuntimeStatus {
    ready: bool,
    package_version: String,
    capabilities: Vec<String>,
    detail: String,
}

#[derive(Clone, Deserialize, Serialize)]
struct ActiveRuntime {
    schema_version: u32,
    manifest_sha256: String,
    profile: String,
    package_version: String,
    capabilities: Vec<String>,
}

struct DesktopPaths {
    data: PathBuf,
    cache: PathBuf,
    repository: PathBuf,
    runtimes: PathBuf,
    python: PathBuf,
    models: PathBuf,
    active_runtime: PathBuf,
}

struct DesktopState {
    ui_process: Mutex<Option<Child>>,
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
    serde_json::from_slice(RUNTIME_MANIFEST_BYTES)
        .map_err(|error| format!("The embedded runtime manifest is invalid: {error}"))
}

fn manifest_digest() -> String {
    hex::encode(Sha256::digest(RUNTIME_MANIFEST_BYTES))
}

fn desktop_paths(app: &AppHandle) -> Result<DesktopPaths, String> {
    let data = app
        .path()
        .app_local_data_dir()
        .map_err(|error| format!("Could not resolve the application data directory: {error}"))?;
    let cache = app
        .path()
        .app_cache_dir()
        .map_err(|error| format!("Could not resolve the application cache directory: {error}"))?;
    Ok(DesktopPaths {
        repository: data.join("repositories").join("default"),
        runtimes: data.join("runtimes"),
        python: data.join("python"),
        active_runtime: data.join("active-runtime.json"),
        models: cache.join("models"),
        data,
        cache,
    })
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

fn package_specification(manifest: &RuntimeManifest, capabilities: &[String]) -> String {
    let extras: BTreeSet<_> = manifest
        .always_install_extras
        .iter()
        .cloned()
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

fn checked_output(mut command: Command, operation: &str) -> Result<Output, String> {
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
        .map_err(|_| "VidXP has not been installed by this desktop app.".to_string())?;
    let active: ActiveRuntime = serde_json::from_slice(&contents)
        .map_err(|error| format!("The active runtime pointer is invalid: {error}"))?;
    if active.schema_version != 1 || active.manifest_sha256 != manifest_digest() {
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
fn runtime_status(app: AppHandle) -> Result<RuntimeStatus, String> {
    let manifest = manifest()?;
    let paths = desktop_paths(&app)?;
    let active = match active_runtime(&paths) {
        Ok(active) => active,
        Err(detail) => {
            return Ok(RuntimeStatus {
                ready: false,
                package_version: manifest.package_version,
                capabilities: Vec::new(),
                detail,
            });
        }
    };
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
        detail: version
            .err()
            .unwrap_or_else(|| "The desktop runtime is ready.".into()),
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
    let paths = desktop_paths(&app)?;
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
        "{}:{}:{}:{}",
        manifest_digest(),
        std::env::consts::OS,
        std::env::consts::ARCH,
        capabilities.join(",")
    );
    let profile_hash = hex::encode(Sha256::digest(profile_seed.as_bytes()));
    let timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| format!("The system clock is invalid: {error}"))?
        .as_secs();
    let staging_name = format!(".staging-{profile_hash}-{timestamp}-{}", std::process::id());
    let staging = paths.runtimes.join(&staging_name);
    fs::create_dir(&staging)
        .map_err(|error| format!("Could not create the staged runtime: {error}"))?;

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

        let mut install_arguments = vec![
            "pip".into(),
            "install".into(),
            "--python".into(),
            executable(&staging, "python")
                .to_string_lossy()
                .into_owned(),
            "--no-config".into(),
            "--default-index".into(),
            manifest.package_index.clone(),
            "--index-strategy".into(),
            "first-index".into(),
        ];
        if !cfg!(target_os = "macos") {
            install_arguments.extend(["--torch-backend".into(), "cpu".into()]);
        }
        install_arguments.push(package_specification(&manifest, &capabilities));
        uv_output(
            &app,
            &state,
            &paths,
            install_arguments,
            "VidXP package installation",
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
        return Err(format!(
            "{error}. The previous active runtime was not changed; staged files remain at {} for diagnosis.",
            staging.display()
        ));
    }

    let profile = format!("{profile_hash}-{timestamp}");
    let runtime = paths.runtimes.join(&profile);
    fs::rename(&staging, &runtime)
        .map_err(|error| format!("Could not finalize the validated runtime: {error}"))?;
    let active = ActiveRuntime {
        schema_version: 1,
        manifest_sha256: manifest_digest(),
        profile,
        package_version: manifest.package_version.clone(),
        capabilities: capabilities.clone(),
    };
    write_active_runtime(&paths, &active)?;

    Ok(InstallResult {
        package_version: manifest.package_version,
        capabilities,
        prepared: request.prepare_models,
    })
}

#[tauri::command]
fn launch_ui(app: AppHandle, state: tauri::State<'_, DesktopState>) -> Result<String, String> {
    let paths = desktop_paths(&app)?;
    let active = active_runtime(&paths)?;
    let runtime = runtime_directory(&paths, &active);
    let mut active_process = state
        .ui_process
        .lock()
        .map_err(|_| "The desktop process supervisor is unavailable.".to_string())?;
    if let Some(process) = active_process.as_mut() {
        if process
            .try_wait()
            .map_err(|error| format!("Could not inspect the interface process: {error}"))?
            .is_none()
        {
            return Err("The VidXP interface is already running.".into());
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
            *active_process = Some(process);
            return Ok(format!("http://127.0.0.1:{port}"));
        }
        thread::sleep(Duration::from_millis(100));
    }
    let _ = process.kill();
    let _ = process.wait();
    Err("The VidXP interface did not become ready in 30 seconds.".into())
}

fn shutdown(app: &AppHandle) {
    let state = app.state::<DesktopState>();
    if let Ok(mut active_operation) = state.operation_process.lock()
        && let Some(process) = active_operation.take()
    {
        let _ = process.kill();
    }
    if let Ok(mut active_process) = state.ui_process.lock()
        && let Some(mut process) = active_process.take()
    {
        let _ = process.kill();
        match process.wait_timeout(Duration::from_secs(5)) {
            Ok(Some(_)) => {}
            _ => {
                let _ = process.kill();
                let _ = process.wait_timeout(Duration::from_secs(1));
            }
        }
    }
    let Ok(paths) = desktop_paths(app) else {
        return;
    };
    if let Ok(mut operation_worker) = state.operation_worker_runtime.lock()
        && let Some(runtime) = operation_worker.take()
    {
        let _ = run_vidxp(
            &runtime,
            &paths,
            &["jobs".into(), "stop-worker".into()],
            "VidXP preparation worker shutdown",
        );
    }
    let Ok(active) = active_runtime(&paths) else {
        return;
    };
    let runtime = runtime_directory(&paths, &active);
    let _ = run_vidxp(
        &runtime,
        &paths,
        &["jobs".into(), "stop-worker".into()],
        "VidXP worker shutdown",
    );
}

pub fn run() {
    let builder = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_log::Builder::new().build())
        .plugin(tauri_plugin_shell::init())
        .manage(DesktopState::default())
        .invoke_handler(tauri::generate_handler![
            runtime_manifest,
            runtime_status,
            install_runtime,
            launch_ui
        ]);
    let app = builder
        .build(tauri::generate_context!())
        .expect("could not initialize VidXP desktop");
    app.run(|app_handle, event| {
        if let RunEvent::ExitRequested { api, .. } = event
            && app_handle
                .state::<DesktopState>()
                .shutdown_started
                .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
                .is_ok()
        {
            api.prevent_exit();
            let cleanup_handle = app_handle.clone();
            std::thread::spawn(move || {
                shutdown(&cleanup_handle);
                cleanup_handle.exit(0);
            });
        }
    });
}

#[cfg(test)]
mod tests {
    use super::{
        capability_command_arguments, manifest, package_specification, selected_capabilities,
    };

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
    fn package_specification_has_one_sorted_extra_set() {
        let manifest = manifest().expect("manifest");

        assert_eq!(
            package_specification(&manifest, &["scene".into(), "dialogue".into()]),
            "vidxp[dialogue,frontend,scene]==0.2.1b1"
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
}
