fn main() {
    use sha2::{Digest, Sha256};
    use std::fmt::Write;
    use std::path::{Path, PathBuf};

    let mut manifest: serde_json::Value =
        serde_json::from_slice(include_bytes!("../runtime-manifest.json"))
            .expect("desktop/runtime-manifest.json must be valid JSON");
    let capability_catalog: serde_json::Value =
        serde_json::from_slice(include_bytes!("../capability-catalog.json"))
            .expect("desktop/capability-catalog.json must be valid JSON");
    assert_eq!(
        capability_catalog["schema_version"].as_u64(),
        Some(1),
        "desktop capability catalog uses an unsupported schema version"
    );
    let capabilities = capability_catalog["capabilities"]
        .as_object()
        .filter(|capabilities| !capabilities.is_empty())
        .expect("desktop capability catalog must contain capabilities")
        .clone();
    manifest["capabilities"] = serde_json::Value::Object(capabilities);
    let expected = manifest["uv_version"]
        .as_str()
        .expect("runtime manifest must contain uv_version");
    let package_name = manifest["package_name"]
        .as_str()
        .expect("runtime manifest must contain package_name");
    let package_version = manifest["package_version"]
        .as_str()
        .expect("runtime manifest must contain package_version");
    let wheel_version = package_version.replace("-b.", "b").replace("-b", "b");
    let wheel_prefix = format!("{}-{wheel_version}-", package_name.replace('-', "_"));
    let distribution_directory = Path::new("../..").join("dist");
    let wheels = std::fs::read_dir(&distribution_directory)
        .unwrap_or_else(|error| {
            panic!(
                "{} is unavailable; build the Python distribution before Desktop: {error}",
                distribution_directory.display()
            )
        })
        .map(|entry| {
            entry
                .expect("the distribution directory must be readable")
                .path()
        })
        .filter(|path| {
            path.file_name()
                .and_then(|name| name.to_str())
                .is_some_and(|name| name.starts_with(&wheel_prefix) && name.ends_with(".whl"))
        })
        .collect::<Vec<_>>();
    assert_eq!(
        wheels.len(),
        1,
        "Desktop requires exactly one {package_name} {package_version} wheel in {}; found {}",
        distribution_directory.display(),
        wheels.len()
    );
    let wheel = &wheels[0];
    let wheel_name = wheel
        .file_name()
        .and_then(|name| name.to_str())
        .expect("the runtime wheel name must be valid UTF-8");
    let wheel_bytes = std::fs::read(wheel).expect("the runtime wheel must be readable");
    let wheel_digest =
        Sha256::digest(&wheel_bytes)
            .iter()
            .fold(String::with_capacity(64), |mut encoded, byte| {
                write!(&mut encoded, "{byte:02x}").expect("writing to a string cannot fail");
                encoded
            });
    let target = std::env::var("TARGET").expect("Cargo must provide TARGET");
    let suffix = if target.contains("windows") {
        ".exe"
    } else {
        ""
    };
    let sidecar = PathBuf::from("binaries").join(format!("uv-{target}{suffix}"));
    let output = std::process::Command::new(&sidecar)
        .arg("--version")
        .output()
        .unwrap_or_else(|error| {
            panic!(
                "{} is missing or unusable; run the target sidecar fetch script: {error}",
                sidecar.display()
            )
        });
    let actual = String::from_utf8_lossy(&output.stdout);
    assert!(
        output.status.success() && actual.starts_with(&format!("uv {expected}")),
        "{} reports {:?}; expected uv {}",
        sidecar.display(),
        actual.trim(),
        expected
    );

    let output_directory =
        PathBuf::from(std::env::var_os("OUT_DIR").expect("Cargo must provide OUT_DIR"));
    let constraints = output_directory.join("runtime-constraints.txt");
    std::fs::write(output_directory.join("runtime-package.whl"), &wheel_bytes)
        .expect("Cargo must be able to embed the runtime wheel");
    std::fs::write(
        output_directory.join("runtime-package-name.txt"),
        wheel_name,
    )
    .expect("Cargo must be able to embed the runtime wheel name");
    std::fs::write(
        output_directory.join("runtime-package-sha256.txt"),
        &wheel_digest,
    )
    .expect("Cargo must be able to embed the runtime wheel digest");
    manifest["package_wheel_sha256"] = serde_json::Value::String(wheel_digest);
    let project = Path::new("../..");
    let export = std::process::Command::new(&sidecar)
        .args([
            "export",
            "--frozen",
            "--extra",
            "local-worker",
            "--extra",
            "frontend",
            "--extra",
            "server",
            "--no-dev",
            "--no-emit-project",
            "--no-hashes",
            "--format",
            "requirements-txt",
            "--project",
        ])
        .arg(project)
        .output()
        .unwrap_or_else(|error| {
            panic!(
                "{} could not export the desktop runtime constraints: {error}",
                sidecar.display()
            )
        });
    assert!(
        export.status.success(),
        "{} failed to export the desktop runtime constraints: {}",
        sidecar.display(),
        String::from_utf8_lossy(&export.stderr).trim()
    );
    let normalized = String::from_utf8(export.stdout)
        .expect("the desktop runtime constraints must be UTF-8")
        .replace("\r\n", "\n");
    std::fs::write(&constraints, normalized.as_bytes())
        .expect("Cargo must be able to normalize the desktop runtime constraints");
    let digest = Sha256::digest(normalized.as_bytes()).iter().fold(
        String::with_capacity(64),
        |mut encoded, byte| {
            write!(&mut encoded, "{byte:02x}").expect("writing to a string cannot fail");
            encoded
        },
    );
    manifest["dependency_constraints_sha256"] = serde_json::Value::String(digest);
    let embedded_manifest = constraints.with_file_name("runtime-manifest.json");
    let mut serialized = serde_json::to_vec_pretty(&manifest)
        .expect("desktop/runtime-manifest.json must be serializable");
    serialized.push(b'\n');
    std::fs::write(&embedded_manifest, serialized)
        .expect("Cargo must be able to write the embedded runtime manifest");
    println!("cargo:rerun-if-changed=../../pyproject.toml");
    println!("cargo:rerun-if-changed=../../uv.lock");
    println!("cargo:rerun-if-changed=../../dist");
    println!("cargo:rerun-if-changed=../runtime-manifest.json");
    println!("cargo:rerun-if-changed=../capability-catalog.json");

    let attributes = tauri_build::Attributes::new();
    #[cfg(windows)]
    let attributes = {
        add_windows_manifest();
        attributes.windows_attributes(tauri_build::WindowsAttributes::new_without_app_manifest())
    };
    tauri_build::try_build(attributes).expect("Tauri build configuration must be valid")
}

#[cfg(windows)]
fn add_windows_manifest() {
    let manifest = std::path::PathBuf::from(
        std::env::var_os("CARGO_MANIFEST_DIR").expect("Cargo must provide CARGO_MANIFEST_DIR"),
    )
    .join("windows-app-manifest.xml");
    println!("cargo:rerun-if-changed={}", manifest.display());
    println!("cargo:rustc-link-arg=/MANIFEST:EMBED");
    println!("cargo:rustc-link-arg=/MANIFESTINPUT:{}", manifest.display());
}
