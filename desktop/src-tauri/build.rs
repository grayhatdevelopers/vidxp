fn main() {
    let manifest: serde_json::Value =
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
    let sidecar = std::path::PathBuf::from("binaries").join(format!("uv-{target}{suffix}"));
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
    tauri_build::build()
}
