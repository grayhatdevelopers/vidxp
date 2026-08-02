use std::path::PathBuf;

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
