use std::{
    collections::BTreeSet,
    path::{Path, PathBuf},
    process::Command,
};

#[cfg(windows)]
use std::env;
#[cfg(target_os = "macos")]
use std::fs;

use serde::{Deserialize, Serialize};

const CEP_ID: &str = "org.grayhat.vidxp-premiere.cep.search";
const UXP_ID: &str = "org.grayhat.vidxp-premiere";
const CEP_PACKAGE: &str = "vidxp-premiere-cep.zxp";
const UXP_PACKAGE: &str = "vidxp-premiere-uxp.ccx";

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum PremiereHostKind {
    Cep,
    Uxp,
    Unsupported,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct PremiereInstallation {
    pub display_name: String,
    pub version: String,
    pub executable: String,
    pub host_kind: PremiereHostKind,
    pub compatible: bool,
}

#[derive(Clone, Debug, Serialize)]
pub struct PremiereIntegrationState {
    pub installations: Vec<PremiereInstallation>,
    pub platform_supported: bool,
    pub installer_available: bool,
    pub cep_package_available: bool,
    pub uxp_package_available: bool,
    pub cep_installed: bool,
    pub uxp_installed: bool,
    pub detail: String,
}

#[derive(Clone, Debug, Serialize)]
pub struct PremiereInstallResult {
    pub installed_hosts: Vec<PremiereHostKind>,
    pub opened_packages: Vec<String>,
    pub detail: String,
}

#[derive(Deserialize)]
struct WindowsInstallation {
    #[serde(rename = "Name")]
    name: String,
    #[serde(rename = "Version")]
    version: String,
    #[serde(rename = "Executable")]
    executable: String,
}

pub fn state(resource_dir: &Path) -> PremiereIntegrationState {
    let installations = discover_installations();
    let installer = unified_plugin_installer();
    let installed = installer
        .as_ref()
        .and_then(|path| installer_list(path).ok())
        .unwrap_or_default();
    let cep_package_available = package_path(resource_dir, PremiereHostKind::Cep).is_file();
    let uxp_package_available = package_path(resource_dir, PremiereHostKind::Uxp).is_file();
    let compatible_count = installations.iter().filter(|item| item.compatible).count();
    let detail = if compatible_count == 0 {
        "No compatible Premiere installation was found in its standard application folder. Both packages remain available for custom Adobe installations.".into()
    } else {
        format!(
            "Found {compatible_count} compatible Premiere installation{}.",
            if compatible_count == 1 { "" } else { "s" }
        )
    };
    PremiereIntegrationState {
        installations,
        platform_supported: cfg!(any(windows, target_os = "macos")),
        installer_available: installer.is_some(),
        cep_package_available,
        uxp_package_available,
        cep_installed: installed.contains(CEP_ID),
        uxp_installed: installed.contains(UXP_ID),
        detail,
    }
}

pub fn install(resource_dir: &Path) -> Result<(PremiereInstallResult, Vec<PathBuf>), String> {
    if !cfg!(any(windows, target_os = "macos")) {
        return Err(
            "Premiere extension installation is available on Windows and macOS only.".into(),
        );
    }
    let installations = discover_installations();
    let mut kinds = installations
        .iter()
        .filter(|item| item.compatible)
        .map(|item| item.host_kind.clone())
        .collect::<BTreeSet<_>>();
    if kinds.is_empty() {
        if !installations.is_empty() {
            return Err("The detected Premiere installations are not supported by this VidXP extension build.".into());
        }
        kinds.insert(PremiereHostKind::Cep);
        if cfg!(windows) {
            kinds.insert(PremiereHostKind::Uxp);
        }
    }
    let installer = unified_plugin_installer();
    let mut installed_hosts = Vec::new();
    let mut opened = Vec::new();
    for kind in kinds {
        let package = package_path(resource_dir, kind.clone());
        if !package.is_file() {
            return Err(format!(
                "The bundled {} package is missing. Reinstall or update VidXP Desktop.",
                package.display()
            ));
        }
        if installer
            .as_ref()
            .is_some_and(|path| installer_install(path, &package).is_ok())
        {
            installed_hosts.push(kind);
        } else {
            opened.push(package);
        }
    }
    let result = PremiereInstallResult {
        installed_hosts,
        opened_packages: opened
            .iter()
            .map(|path| path.display().to_string())
            .collect(),
        detail: if opened.is_empty() {
            "The VidXP extension was installed. Restart Premiere, then open VidXP Search from the Window menu.".into()
        } else {
            "Adobe's background installer was unavailable or required interaction. Complete the Creative Cloud installation window, then restart Premiere.".into()
        },
    };
    Ok((result, opened))
}

pub fn uninstall() -> Result<(), String> {
    let installer = unified_plugin_installer()
        .ok_or_else(|| "Adobe Creative Cloud's plugin installer was not found.".to_string())?;
    let mut failures = Vec::new();
    for id in [CEP_ID, UXP_ID] {
        if let Err(error) = installer_remove(&installer, id) {
            failures.push(error);
        }
    }
    if failures.len() == 2 {
        Err(failures.join(" "))
    } else {
        Ok(())
    }
}

fn package_path(resource_dir: &Path, kind: PremiereHostKind) -> PathBuf {
    let name = match kind {
        PremiereHostKind::Cep => CEP_PACKAGE,
        PremiereHostKind::Uxp => UXP_PACKAGE,
        PremiereHostKind::Unsupported => unreachable!("unsupported hosts do not have packages"),
    };
    resource_dir.join("premiere").join(name)
}

fn host_kind(version: &str) -> PremiereHostKind {
    let mut parts = version
        .split('.')
        .filter_map(|part| part.parse::<u32>().ok());
    let major = parts.next().unwrap_or_default();
    let minor = parts.next().unwrap_or_default();
    if major > 25 || (major == 25 && minor >= 6) {
        PremiereHostKind::Uxp
    } else if major >= 23 {
        PremiereHostKind::Cep
    } else {
        PremiereHostKind::Unsupported
    }
}

fn discover_installations() -> Vec<PremiereInstallation> {
    #[cfg(windows)]
    return discover_windows_installations();
    #[cfg(target_os = "macos")]
    return discover_macos_installations();
    #[cfg(not(any(windows, target_os = "macos")))]
    Vec::new()
}

#[cfg(windows)]
fn discover_windows_installations() -> Vec<PremiereInstallation> {
    let script = r#"
$items = @()
$roots = @($env:ProgramFiles, ${env:ProgramFiles(x86)}) | Where-Object { $_ } | Select-Object -Unique
foreach ($root in $roots) {
  $adobe = Join-Path $root 'Adobe'
  if (-not (Test-Path -LiteralPath $adobe)) { continue }
  Get-ChildItem -LiteralPath $adobe -Directory -Filter 'Adobe Premiere Pro*' -ErrorAction SilentlyContinue | ForEach-Object {
    $exe = Join-Path $_.FullName 'Adobe Premiere Pro.exe'
    if (Test-Path -LiteralPath $exe) {
      $info = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($exe)
      $items += [pscustomobject]@{ Name = $_.Name; Version = $info.ProductVersion; Executable = $exe }
    }
  }
}
$items | ConvertTo-Json -Compress
"#;
    let output = Command::new("powershell.exe")
        .args(["-NoProfile", "-NonInteractive", "-Command", script])
        .output();
    let Ok(output) = output else {
        return Vec::new();
    };
    if !output.status.success() {
        return Vec::new();
    }
    parse_windows_installations(&output.stdout)
}

#[cfg(windows)]
fn parse_windows_installations(payload: &[u8]) -> Vec<PremiereInstallation> {
    let value: serde_json::Value = match serde_json::from_slice(payload) {
        Ok(value) => value,
        Err(_) => return Vec::new(),
    };
    let values = match value {
        serde_json::Value::Array(values) => values,
        serde_json::Value::Object(_) => vec![value],
        _ => Vec::new(),
    };
    values
        .into_iter()
        .filter_map(|value| {
            let item: WindowsInstallation = serde_json::from_value(value).ok()?;
            let kind = host_kind(&item.version);
            let compatible = kind != PremiereHostKind::Unsupported
                && !(cfg!(target_os = "macos") && kind == PremiereHostKind::Uxp);
            Some(PremiereInstallation {
                display_name: item.name,
                version: item.version,
                executable: item.executable,
                compatible,
                host_kind: kind,
            })
        })
        .collect()
}

#[cfg(target_os = "macos")]
fn discover_macos_installations() -> Vec<PremiereInstallation> {
    let Ok(entries) = fs::read_dir("/Applications") else {
        return Vec::new();
    };
    entries
        .flatten()
        .filter_map(|entry| {
            let path = entry.path();
            let name = path.file_name()?.to_str()?;
            if !name.starts_with("Adobe Premiere Pro") || path.extension()?.to_str()? != "app" {
                return None;
            }
            let plist = fs::read_to_string(path.join("Contents/Info.plist")).ok()?;
            let version = plist_value(&plist, "CFBundleShortVersionString")?;
            let kind = host_kind(&version);
            let compatible = kind != PremiereHostKind::Unsupported
                && !(cfg!(target_os = "macos") && kind == PremiereHostKind::Uxp);
            Some(PremiereInstallation {
                display_name: name.trim_end_matches(".app").into(),
                version,
                executable: path.display().to_string(),
                compatible,
                host_kind: kind,
            })
        })
        .collect()
}

#[cfg(target_os = "macos")]
fn plist_value(plist: &str, key: &str) -> Option<String> {
    let marker = format!("<key>{key}</key>");
    let remainder = plist.split_once(&marker)?.1;
    let value = remainder
        .split_once("<string>")?
        .1
        .split_once("</string>")?
        .0;
    Some(value.trim().into())
}

fn unified_plugin_installer() -> Option<PathBuf> {
    #[cfg(windows)]
    {
        let program_files = env::var_os("ProgramFiles")?;
        let path = PathBuf::from(program_files).join("Common Files/Adobe/Adobe Desktop Common/RemoteComponents/UPI/UnifiedPluginInstallerAgent/UnifiedPluginInstallerAgent.exe");
        path.is_file().then_some(path)
    }
    #[cfg(target_os = "macos")]
    {
        let path = PathBuf::from(
            "/Library/Application Support/Adobe/Adobe Desktop Common/RemoteComponents/UPI/UnifiedPluginInstallerAgent/UnifiedPluginInstallerAgent.app/Contents/MacOS/UnifiedPluginInstallerAgent",
        );
        return path.is_file().then_some(path);
    }
    #[cfg(not(any(windows, target_os = "macos")))]
    None
}

fn installer_list(installer: &Path) -> Result<String, String> {
    let argument = if cfg!(windows) { "/list" } else { "--list" };
    checked_installer(installer, [argument, "all"])
}

fn installer_install(installer: &Path, package: &Path) -> Result<String, String> {
    let argument = if cfg!(windows) {
        "/install"
    } else {
        "--install"
    };
    checked_installer(installer, [argument, package.to_string_lossy().as_ref()])
}

fn installer_remove(installer: &Path, id: &str) -> Result<String, String> {
    let argument = if cfg!(windows) { "/remove" } else { "--remove" };
    checked_installer(installer, [argument, id])
}

fn checked_installer<'a>(
    installer: &Path,
    arguments: impl IntoIterator<Item = &'a str>,
) -> Result<String, String> {
    let output = Command::new(installer)
        .args(arguments)
        .output()
        .map_err(|error| format!("Could not start Adobe's plugin installer: {error}"))?;
    let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
    if output.status.success() {
        Ok(stdout)
    } else {
        Err(format!(
            "Adobe's plugin installer failed{}.",
            if stderr.is_empty() {
                String::new()
            } else {
                format!(": {stderr}")
            }
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn chooses_non_overlapping_host_generations() {
        assert_eq!(host_kind("23.2.0.69"), PremiereHostKind::Cep);
        assert_eq!(host_kind("25.5.0"), PremiereHostKind::Cep);
        assert_eq!(host_kind("25.6.0"), PremiereHostKind::Uxp);
        assert_eq!(host_kind("26.3.0"), PremiereHostKind::Uxp);
        assert_eq!(host_kind("22.6.0"), PremiereHostKind::Unsupported);
    }
}
