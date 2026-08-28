use std::path::PathBuf;

#[cfg(windows)]
use std::{env, fs, path::Path};

pub(crate) struct SystemInstallPlan {
    pub(crate) manager: String,
    pub(crate) command: Vec<String>,
    pub(crate) automatic: bool,
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
                "Gyan.FFmpeg".into(),
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
                "ffmpeg".into(),
            ],
            automatic: true,
        });
    }
    if resolve("apt-get").is_some() {
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
    if resolve("dnf").is_some() {
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

pub(crate) fn display_command(arguments: &[String]) -> String {
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

pub(crate) fn required_encoder_missing(output: &str, encoder: &str) -> bool {
    !output
        .lines()
        .flat_map(|line| line.split_whitespace())
        .any(|token| token == encoder)
}

#[cfg(windows)]
fn find_winget_package_executable_in(root: &Path, name: &str) -> Option<PathBuf> {
    fn visit(directory: &Path, name: &str, remaining_depth: u8, matches: &mut Vec<PathBuf>) {
        if remaining_depth == 0 {
            return;
        }
        let Ok(entries) = fs::read_dir(directory) else {
            return;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_file()
                && path
                    .file_name()
                    .is_some_and(|candidate| candidate.to_string_lossy().eq_ignore_ascii_case(name))
            {
                matches.push(path);
            } else if path.is_dir() {
                visit(&path, name, remaining_depth - 1, matches);
            }
        }
    }

    let entries = fs::read_dir(root).ok()?;
    let mut matches = Vec::new();
    for entry in entries.flatten() {
        let package = entry.path();
        if package.is_dir()
            && package
                .file_name()
                .is_some_and(|candidate| candidate.to_string_lossy().starts_with("Gyan.FFmpeg_"))
        {
            visit(&package, name, 6, &mut matches);
        }
    }
    matches.sort();
    matches.into_iter().next()
}

#[cfg(windows)]
pub(crate) fn resolve_winget_ffmpeg_executable(name: &str) -> Option<PathBuf> {
    let mut roots = Vec::new();
    if let Some(local) = env::var_os("LOCALAPPDATA") {
        roots.push(
            PathBuf::from(local)
                .join("Microsoft")
                .join("WinGet")
                .join("Packages"),
        );
    }
    for variable in ["PROGRAMFILES", "PROGRAMFILES(X86)", "ProgramW6432"] {
        if let Some(program_files) = env::var_os(variable) {
            roots.push(PathBuf::from(program_files).join("WinGet").join("Packages"));
        }
    }
    roots.into_iter().find_map(|root| {
        find_winget_package_executable_in(&root, name)
            .and_then(|candidate| fs::canonicalize(&candidate).ok().or(Some(candidate)))
    })
}

#[cfg(all(test, windows))]
mod tests {
    use super::*;

    #[test]
    fn resolves_ffmpeg_from_a_winget_package_when_its_alias_is_missing() {
        let root = std::env::temp_dir().join(format!(
            "vidxp-winget-ffmpeg-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .expect("clock")
                .as_nanos()
        ));
        let executable = root
            .join("Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe")
            .join("ffmpeg-build")
            .join("bin")
            .join("ffmpeg.exe");
        fs::create_dir_all(executable.parent().expect("executable parent")).expect("package");
        fs::write(&executable, b"fixture").expect("executable");

        assert_eq!(
            find_winget_package_executable_in(&root, "ffmpeg.exe"),
            Some(executable)
        );
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn ffmpeg_install_disables_hidden_package_manager_prompts() {
        let plan =
            system_install_plan(|name| (name == "winget").then(|| PathBuf::from("winget.exe")))
                .expect("install plan");

        assert!(plan.command.iter().any(|argument| argument == "--silent"));
        assert!(
            plan.command
                .iter()
                .any(|argument| argument == "--disable-interactivity")
        );
    }
}
