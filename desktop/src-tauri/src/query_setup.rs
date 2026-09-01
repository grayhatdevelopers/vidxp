use std::{
    collections::BTreeMap,
    env, fs,
    fs::File,
    io::{self, BufReader, Read, Write},
    path::{Path, PathBuf},
    time::{Duration, SystemTime, UNIX_EPOCH},
};

use flate2::read::GzDecoder;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::background_process::CancellationToken;

pub(crate) const OLLAMA_HOST: &str = "127.0.0.1:11434";
const MANAGED_RUNTIME_DIRECTORY: &str = "query-runtimes";
const RUNTIME_DOWNLOAD_TIMEOUT: Duration = Duration::from_secs(2 * 60 * 60);
const DOWNLOAD_BUFFER_BYTES: usize = 1024 * 1024;

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

pub(crate) fn current_artifact(spec: &ManagedRuntimeSpec) -> Option<&ManagedRuntimeArtifactSpec> {
    spec.artifacts.get(current_platform_key()?)
}

fn managed_runtime_root(private_data: &Path) -> PathBuf {
    private_data.join(MANAGED_RUNTIME_DIRECTORY)
}

fn managed_runtime_directory(
    private_data: &Path,
    spec: &ManagedRuntimeSpec,
) -> Result<PathBuf, String> {
    if spec.version.is_empty()
        || !spec.version.chars().all(|character| {
            character.is_ascii_alphanumeric() || matches!(character, '.' | '-' | '_')
        })
    {
        return Err("The managed Ollama runtime version is invalid.".into());
    }
    Ok(managed_runtime_root(private_data).join(format!("ollama-{}", spec.version)))
}

pub(crate) fn managed_executable(
    private_data: &Path,
    spec: &ManagedRuntimeSpec,
) -> Option<PathBuf> {
    let artifact = current_artifact(spec)?;
    let candidate = managed_runtime_directory(private_data, spec)
        .ok()?
        .join(&artifact.executable);
    candidate
        .is_file()
        .then(|| fs::canonicalize(&candidate).unwrap_or(candidate))
}

fn runtime_download_client() -> Result<reqwest::blocking::Client, String> {
    reqwest::blocking::Client::builder()
        .connect_timeout(Duration::from_secs(15))
        .timeout(RUNTIME_DOWNLOAD_TIMEOUT)
        .build()
        .map_err(|error| format!("Could not configure the Ollama runtime download: {error}"))
}

fn download_archive(
    artifact: &ManagedRuntimeArtifactSpec,
    destination: &Path,
    cancellation: &CancellationToken,
    mut progress: impl FnMut(u64, u64),
) -> Result<(), String> {
    let mut response = runtime_download_client()?
        .get(&artifact.url)
        .header(reqwest::header::USER_AGENT, "VidXP-Desktop")
        .send()
        .and_then(reqwest::blocking::Response::error_for_status)
        .map_err(|error| format!("Could not download the managed Ollama runtime: {error}"))?;
    let response_total = response
        .content_length()
        .unwrap_or(artifact.download_size_bytes);
    let mut output = File::create(destination).map_err(|error| {
        format!(
            "Could not create the temporary Ollama runtime archive at {}: {error}",
            destination.display()
        )
    })?;
    let mut hasher = Sha256::new();
    let mut buffer = vec![0_u8; DOWNLOAD_BUFFER_BYTES];
    let mut downloaded = 0_u64;
    progress(0, response_total);
    loop {
        if cancellation.is_cancelled() {
            return Err("the managed Ollama runtime download was cancelled".into());
        }
        let count = response
            .read(&mut buffer)
            .map_err(|error| format!("The managed Ollama runtime download failed: {error}"))?;
        if count == 0 {
            break;
        }
        output.write_all(&buffer[..count]).map_err(|error| {
            format!("Could not write the managed Ollama runtime archive: {error}")
        })?;
        hasher.update(&buffer[..count]);
        downloaded += count as u64;
        progress(downloaded, response_total);
    }
    output
        .sync_all()
        .map_err(|error| format!("Could not finish the managed Ollama runtime archive: {error}"))?;
    if downloaded != artifact.download_size_bytes {
        return Err(format!(
            "The managed Ollama runtime download contained {downloaded} bytes; expected {}.",
            artifact.download_size_bytes
        ));
    }
    let actual_sha256 = hex::encode(hasher.finalize());
    if !actual_sha256.eq_ignore_ascii_case(&artifact.sha256) {
        return Err(format!(
            "The managed Ollama runtime failed checksum verification: expected {}, received {actual_sha256}.",
            artifact.sha256
        ));
    }
    Ok(())
}

fn extract_zip(
    archive_path: &Path,
    destination: &Path,
    cancellation: &CancellationToken,
) -> Result<(), String> {
    let archive_file = File::open(archive_path)
        .map_err(|error| format!("Could not open the managed Ollama archive: {error}"))?;
    let mut archive = zip::ZipArchive::new(BufReader::new(archive_file))
        .map_err(|error| format!("Could not read the managed Ollama ZIP archive: {error}"))?;
    for index in 0..archive.len() {
        if cancellation.is_cancelled() {
            return Err("the managed Ollama runtime extraction was cancelled".into());
        }
        let mut entry = archive
            .by_index(index)
            .map_err(|error| format!("Could not inspect the managed Ollama archive: {error}"))?;
        let relative = entry
            .enclosed_name()
            .ok_or("The managed Ollama archive contains an unsafe path.")?;
        if entry
            .unix_mode()
            .is_some_and(|mode| mode & 0o170000 == 0o120000)
        {
            return Err("The managed Ollama ZIP archive contains an unsupported link.".into());
        }
        let output = destination.join(relative);
        if entry.is_dir() {
            fs::create_dir_all(&output)
                .map_err(|error| format!("Could not create an Ollama runtime folder: {error}"))?;
        } else if entry.is_file() {
            if let Some(parent) = output.parent() {
                fs::create_dir_all(parent).map_err(|error| {
                    format!("Could not create an Ollama runtime folder: {error}")
                })?;
            }
            let mut file = File::create(&output)
                .map_err(|error| format!("Could not extract an Ollama runtime file: {error}"))?;
            io::copy(&mut entry, &mut file)
                .map_err(|error| format!("Could not extract an Ollama runtime file: {error}"))?;
        } else {
            return Err("The managed Ollama ZIP archive contains an unsupported entry.".into());
        }
    }
    Ok(())
}

fn extract_tar_gz(
    archive_path: &Path,
    destination: &Path,
    cancellation: &CancellationToken,
) -> Result<(), String> {
    let archive_file = File::open(archive_path)
        .map_err(|error| format!("Could not open the managed Ollama archive: {error}"))?;
    let decoder = GzDecoder::new(BufReader::new(archive_file));
    let mut archive = tar::Archive::new(decoder);
    let entries = archive
        .entries()
        .map_err(|error| format!("Could not read the managed Ollama archive: {error}"))?;
    for entry in entries {
        if cancellation.is_cancelled() {
            return Err("the managed Ollama runtime extraction was cancelled".into());
        }
        let mut entry = entry
            .map_err(|error| format!("Could not inspect the managed Ollama archive: {error}"))?;
        let entry_type = entry.header().entry_type();
        if !entry_type.is_file() && !entry_type.is_dir() {
            return Err("The managed Ollama archive contains an unsupported entry.".into());
        }
        if !entry
            .unpack_in(destination)
            .map_err(|error| format!("Could not extract the managed Ollama archive: {error}"))?
        {
            return Err("The managed Ollama archive contains an unsafe path.".into());
        }
    }
    Ok(())
}

fn extract_archive(
    archive_path: &Path,
    destination: &Path,
    archive: ManagedRuntimeArchive,
    cancellation: &CancellationToken,
) -> Result<(), String> {
    fs::create_dir_all(destination).map_err(|error| {
        format!(
            "Could not create the managed Ollama runtime folder at {}: {error}",
            destination.display()
        )
    })?;
    match archive {
        ManagedRuntimeArchive::Zip => extract_zip(archive_path, destination, cancellation),
        ManagedRuntimeArchive::TarGz => extract_tar_gz(archive_path, destination, cancellation),
    }
}

pub(crate) fn install_managed_runtime(
    private_data: &Path,
    spec: &ManagedRuntimeSpec,
    cancellation: &CancellationToken,
    progress: impl FnMut(u64, u64),
) -> Result<PathBuf, String> {
    if let Some(executable) = managed_executable(private_data, spec) {
        return Ok(executable);
    }
    let artifact = current_artifact(spec).ok_or_else(|| {
        "VidXP does not publish a managed Ollama runtime for this operating system and architecture."
            .to_string()
    })?;
    let runtime_root = managed_runtime_root(private_data);
    fs::create_dir_all(&runtime_root).map_err(|error| {
        format!(
            "Could not create the managed query runtime folder at {}: {error}",
            runtime_root.display()
        )
    })?;
    let target = managed_runtime_directory(private_data, spec)?;
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| format!("The system clock is invalid: {error}"))?
        .as_nanos();
    let temporary_name = format!("ollama-{}-{}-{nonce}", spec.version, std::process::id());
    let archive_path = runtime_root.join(format!(".{temporary_name}.download"));
    let staging = runtime_root.join(format!(".{temporary_name}.partial"));
    let result = (|| {
        download_archive(artifact, &archive_path, cancellation, progress)?;
        if cancellation.is_cancelled() {
            return Err("the managed Ollama runtime setup was cancelled".into());
        }
        extract_archive(&archive_path, &staging, artifact.archive, cancellation)?;
        let staged_executable = staging.join(&artifact.executable);
        if !staged_executable.is_file() {
            return Err(format!(
                "The managed Ollama archive did not contain {}.",
                artifact.executable.display()
            ));
        }
        if target.exists() {
            fs::remove_dir_all(&target).map_err(|error| {
                format!("Could not replace the incomplete managed Ollama runtime: {error}")
            })?;
        }
        fs::rename(&staging, &target)
            .map_err(|error| format!("Could not activate the managed Ollama runtime: {error}"))?;
        let executable = target.join(&artifact.executable);
        Ok(fs::canonicalize(&executable).unwrap_or(executable))
    })();
    let _ = fs::remove_file(&archive_path);
    if staging.exists() {
        let _ = fs::remove_dir_all(&staging);
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temporary_root(label: &str) -> PathBuf {
        std::env::temp_dir().join(format!(
            "vidxp-{label}-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("clock")
                .as_nanos()
        ))
    }

    fn runtime_spec() -> ManagedRuntimeSpec {
        ManagedRuntimeSpec {
            version: "0.32.5".into(),
            maximum_download_size_bytes: 10,
            artifacts: BTreeMap::from([(
                current_platform_key().unwrap_or("unsupported").into(),
                ManagedRuntimeArtifactSpec {
                    url: "https://example.invalid/ollama.zip".into(),
                    sha256: "00".repeat(32),
                    download_size_bytes: 10,
                    archive: ManagedRuntimeArchive::Zip,
                    executable: PathBuf::from(if cfg!(windows) {
                        "ollama.exe"
                    } else {
                        "ollama"
                    }),
                },
            )]),
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

    #[test]
    fn managed_runtime_version_cannot_escape_its_owned_root() {
        let mut spec = runtime_spec();
        spec.version = "../escape".into();
        assert!(managed_runtime_directory(Path::new("runtime-root"), &spec).is_err());
    }

    #[test]
    fn managed_executable_requires_the_expected_file() {
        let root = temporary_root("managed-ollama-path");
        let spec = runtime_spec();
        assert_eq!(managed_executable(&root, &spec), None);
        fs::remove_dir_all(root).ok();
    }

    #[test]
    fn zip_runtime_archive_extracts_only_expected_files() {
        let root = temporary_root("managed-ollama-zip");
        let archive_path = root.join("runtime.zip");
        let destination = root.join("extracted");
        fs::create_dir_all(&root).expect("temporary root");
        let archive_file = File::create(&archive_path).expect("archive file");
        let mut archive = zip::ZipWriter::new(archive_file);
        archive
            .start_file(
                "ollama.exe",
                zip::write::SimpleFileOptions::default()
                    .compression_method(zip::CompressionMethod::Deflated),
            )
            .expect("archive entry");
        archive.write_all(b"headless-runtime").expect("entry data");
        archive.finish().expect("finished archive");

        extract_zip(&archive_path, &destination, &CancellationToken::default())
            .expect("extracted archive");

        assert_eq!(
            fs::read(destination.join("ollama.exe")).expect("extracted executable"),
            b"headless-runtime"
        );
        fs::remove_dir_all(root).expect("temporary cleanup");
    }

    #[test]
    fn tar_runtime_archive_extracts_only_expected_files() {
        let root = temporary_root("managed-ollama-tar");
        let archive_path = root.join("runtime.tgz");
        let destination = root.join("extracted");
        fs::create_dir_all(&root).expect("temporary root");
        let archive_file = File::create(&archive_path).expect("archive file");
        let encoder = flate2::write::GzEncoder::new(archive_file, flate2::Compression::default());
        let mut archive = tar::Builder::new(encoder);
        let contents = b"headless-runtime";
        let mut header = tar::Header::new_gnu();
        header.set_size(contents.len() as u64);
        header.set_mode(0o755);
        header.set_cksum();
        archive
            .append_data(&mut header, "ollama", &contents[..])
            .expect("archive entry");
        archive
            .into_inner()
            .expect("archive encoder")
            .finish()
            .expect("finished archive");
        fs::create_dir_all(&destination).expect("extraction destination");

        extract_tar_gz(&archive_path, &destination, &CancellationToken::default())
            .expect("extracted archive");

        assert_eq!(
            fs::read(destination.join("ollama")).expect("extracted executable"),
            contents
        );
        fs::remove_dir_all(root).expect("temporary cleanup");
    }
}
