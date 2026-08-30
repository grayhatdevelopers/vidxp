use std::{env, path::PathBuf};

#[cfg(windows)]
use std::{fs, path::Path};

use crate::media_setup::SystemInstallPlan;

pub(crate) const OLLAMA_HOST: &str = "127.0.0.1:11434";

pub(crate) fn version_meets_minimum(output: &str, minimum: (u32, u32, u32)) -> Option<bool> {
    let version = output
        .split(|character: char| !(character.is_ascii_digit() || character == '.'))
        .find(|candidate| candidate.contains('.'))?;
    let parts = version
        .split('.')
        .take(3)
        .map(str::parse::<u32>)
        .collect::<Result<Vec<_>, _>>()
        .ok()?;
    if parts.len() < 2 {
        return None;
    }
    let actual = (parts[0], parts[1], parts.get(2).copied().unwrap_or(0));
    Some(actual >= minimum)
}

pub(crate) fn system_install_plan(
    mut resolve: impl FnMut(&str) -> Option<PathBuf>,
) -> Option<SystemInstallPlan> {
    if cfg!(windows) {
        resolve("winget")?;
        return Some(SystemInstallPlan {
            manager: "Windows Package Manager".into(),
            command: vec![
                "winget".into(),
                "install".into(),
                "--id".into(),
                "Ollama.Ollama".into(),
                "--exact".into(),
                "--source".into(),
                "winget".into(),
                "--silent".into(),
                "--disable-interactivity".into(),
                "--accept-package-agreements".into(),
                "--accept-source-agreements".into(),
            ],
            automatic: true,
        });
    }
    if cfg!(target_os = "macos") {
        let brew = resolve("brew")?;
        return Some(SystemInstallPlan {
            manager: "Homebrew".into(),
            command: vec![
                brew.to_string_lossy().into_owned(),
                "install".into(),
                "--cask".into(),
                "ollama-app".into(),
            ],
            automatic: true,
        });
    }
    None
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
fn find_winget_ollama_in(root: &Path) -> Option<PathBuf> {
    let entries = fs::read_dir(root).ok()?;
    let mut matches = Vec::new();
    for entry in entries.flatten() {
        let package = entry.path();
        if !package.is_dir()
            || !package
                .file_name()
                .is_some_and(|name| name.to_string_lossy().starts_with("Ollama.Ollama_"))
        {
            continue;
        }
        let executable = package.join("ollama.exe");
        if executable.is_file() {
            matches.push(executable);
        }
    }
    matches.sort();
    matches.into_iter().next()
}

#[cfg(windows)]
pub(crate) fn resolve_winget_ollama_executable() -> Option<PathBuf> {
    let local = env::var_os("LOCALAPPDATA")?;
    let root = PathBuf::from(local)
        .join("Microsoft")
        .join("WinGet")
        .join("Packages");
    find_winget_ollama_in(&root)
        .and_then(|candidate| fs::canonicalize(&candidate).ok().or(Some(candidate)))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn supported_install_plans_never_run_an_unattended_shell_script() {
        if let Some(plan) = system_install_plan(|name| Some(PathBuf::from(name))) {
            let command = plan.command.join(" ").to_ascii_lowercase();
            assert!(!command.contains("curl"));
            assert!(!command.contains("powershell"));
            assert!(!command.contains("sh -"));
        }
    }

    #[test]
    fn platform_versions_are_compared_as_numeric_triples() {
        assert_eq!(
            version_meets_minimum("Microsoft Windows [Version 10.0.19045.1]", (10, 0, 19045)),
            Some(true)
        );
        assert_eq!(
            version_meets_minimum("Microsoft Windows [Version 10.0.19044.1]", (10, 0, 19045)),
            Some(false)
        );
        assert_eq!(version_meets_minimum("14.0.0", (14, 0, 0)), Some(true));
        assert_eq!(version_meets_minimum("14.0", (14, 0, 0)), Some(true));
        assert_eq!(version_meets_minimum("13.6.9", (14, 0, 0)), Some(false));
        assert_eq!(version_meets_minimum("unknown", (14, 0, 0)), None);
    }

    #[cfg(windows)]
    #[test]
    fn windows_install_is_explicit_and_non_interactive() {
        let plan =
            system_install_plan(|name| (name == "winget").then(|| PathBuf::from("winget.exe")))
                .expect("install plan");
        assert!(
            plan.command
                .windows(2)
                .any(|pair| pair == ["--id", "Ollama.Ollama"])
        );
        assert!(
            plan.command
                .iter()
                .any(|value| value == "--disable-interactivity")
        );
    }
}
