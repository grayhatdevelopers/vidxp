fn main() {
    use sha2::{Digest, Sha256};
    use std::fmt::Write;
    use std::path::{Path, PathBuf};

    let mut manifest: serde_json::Value =
        serde_json::from_slice(include_bytes!("../runtime-manifest.json"))
            .expect("desktop/runtime-manifest.json must be valid JSON");
    let expected = manifest["uv_version"]
        .as_str()
        .expect("runtime manifest must contain uv_version");
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

    let constraints =
        PathBuf::from(std::env::var_os("OUT_DIR").expect("Cargo must provide OUT_DIR"))
            .join("runtime-constraints.txt");
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
    println!("cargo:rerun-if-changed=../runtime-manifest.json");

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
