// Release Please writes `X.Y.Z-b` and `X.Y.Z-b.N`; wheel names carry the PEP 440 form.
pub(crate) fn wheel_version(version: &str) -> Option<String> {
    let Some((release, prerelease)) = version.split_once("-b") else {
        return Some(version.to_owned());
    };
    if prerelease.is_empty() {
        return Some(format!("{release}b0"));
    }
    let number = prerelease.strip_prefix('.')?;
    if number.is_empty() || !number.bytes().all(|byte| byte.is_ascii_digit()) {
        return None;
    }
    Some(format!("{release}b{}", number.parse::<u64>().ok()?))
}

#[cfg(test)]
mod tests {
    use super::wheel_version;

    #[test]
    fn release_please_versions_match_python_wheel_names() {
        for (version, expected) in [
            ("0.4.0", "0.4.0"),
            ("1.0.0-b", "1.0.0b0"),
            ("0.4.0-b.3", "0.4.0b3"),
            ("1.0.0-b.12", "1.0.0b12"),
        ] {
            assert_eq!(wheel_version(version).as_deref(), Some(expected), "{version}");
        }
    }

    #[test]
    fn unexpected_prerelease_shapes_are_rejected() {
        for version in ["1.0.0-b.", "1.0.0-beta", "1.0.0-b.x", "1.0.0-b1"] {
            assert_eq!(wheel_version(version), None, "{version}");
        }
    }
}
