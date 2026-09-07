use std::{
    collections::BTreeMap,
    env, fs,
    path::{Path, PathBuf},
};

use serde::{Deserialize, Serialize};

pub(crate) const OLLAMA_HOST: &str = "127.0.0.1:11434";
const MANAGED_RUNTIME_DIRECTORY: &str = "query-runtimes";

pub(crate) fn cleanup_managed_installation_staging(private_data: &Path) -> Result<(), String> {
    let root = private_data.join(MANAGED_RUNTIME_DIRECTORY);
    if !root.exists() {
        return Ok(());
    }
    let entries = fs::read_dir(&root)
        .map_err(|error| format!("Could not inspect {}: {error}", root.display()))?;
    for entry in entries {
        let entry = entry.map_err(|error| {
            format!("Could not inspect an entry under {}: {error}", root.display())
        })?;
        let path = entry.path();
        let name = entry.file_name();
        let name = name.to_string_lossy();
        if !name.starts_with(".ollama-") {
            continue;
        }
        if name.ends_with(".download") && path.is_file() {
            fs::remove_file(&path).map_err(|error| {
                format!("Could not remove {}: {error}", path.display())
            })?;
        } else if name.ends_with(".partial") && path.is_dir() {
            fs::remove_dir_all(&path).map_err(|error| {
                format!("Could not remove {}: {error}", path.display())
            })?;
        }
    }
    Ok(())
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub(crate) struct ManagedRuntimeSpec {
    pub(crate) version: String,
    pub(crate) maximum_download_size_bytes: u64,
    pub(crate) artifacts: BTreeMap<String, ManagedRuntimeArtifactSpec>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub(crate) struct ManagedRuntimeArtifactSpec {
    pub(crate) url: String,
    pub(crate) sha256: String,
    pub(crate) download_size_bytes: u64,
    pub(crate) archive: ManagedRuntimeArchive,
    pub(crate) executable: PathBuf,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum ManagedRuntimeArchive {
    Zip,
    TarGz,
}

pub(crate) fn executable_candidates() -> Vec<PathBuf> {
    let mut candidates = Vec::new();
    if cfg!(windows) {
        if let Some(local) = env::var_os("LOCALAPPDATA") {
            candidates.push(
                PathBuf::from(local)
                    .join("Programs")
                    .join("Ollama")
                    .join("ollama.exe"),
            );
        }
    }
    if cfg!(target_os = "macos") {
        candidates.push(
            PathBuf::from("/Applications")
                .join("Ollama.app")
                .join("Contents")
                .join("Resources")
                .join("ollama"),
        );
    }
    candidates
}

#[cfg(windows)]
pub(crate) fn resolve_winget_ollama_executable() -> Option<PathBuf> {
    let root = PathBuf::from(env::var_os("LOCALAPPDATA")?)
        .join("Microsoft")
        .join("WinGet")
        .join("Packages");
    let mut matches = fs::read_dir(root)
        .ok()?
        .flatten()
        .map(|entry| entry.path())
        .filter(|package| {
            package.is_dir()
                && package.file_name().is_some_and(|name| {
                    name.to_string_lossy().starts_with("Ollama.Ollama_")
                })
                && package.join("ollama.exe").is_file()
        })
        .map(|package| package.join("ollama.exe"))
        .collect::<Vec<_>>();
    matches.sort();
    matches
        .into_iter()
        .next()
        .and_then(|candidate| fs::canonicalize(&candidate).ok().or(Some(candidate)))
}

#[cfg(all(windows, target_arch = "x86_64"))]
fn current_platform_key() -> Option<&'static str> {
    Some("windows-x86_64")
}

#[cfg(all(target_os = "macos", target_arch = "aarch64"))]
fn current_platform_key() -> Option<&'static str> {
    Some("macos-aarch64")
}

#[cfg(not(any(
    all(windows, target_arch = "x86_64"),
    all(target_os = "macos", target_arch = "aarch64")
)))]
fn current_platform_key() -> Option<&'static str> {
    None
}

pub(crate) fn managed_executable(
    private_data: &Path,
    spec: &ManagedRuntimeSpec,
) -> Option<PathBuf> {
    let artifact = spec.artifacts.get(current_platform_key()?)?;
    let candidate = private_data
        .join(MANAGED_RUNTIME_DIRECTORY)
        .join(format!("ollama-{}", spec.version))
        .join(&artifact.executable);
    candidate
        .is_file()
        .then(|| fs::canonicalize(&candidate).unwrap_or(candidate))
}
