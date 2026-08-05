use std::{
    fs,
    io::{Read, Write},
    net::{SocketAddr, TcpStream},
    path::Path,
    thread,
    time::{Duration, Instant},
};

use serde::Deserialize;

use crate::background_process::{CancellationToken, OwnedChild};

const READINESS_PRODUCT: &str = "dev.grayhat.vidxp";
const READINESS_PROTOCOL_VERSION: u32 = 1;

#[derive(Debug, Deserialize)]
struct ReadinessMarker {
    product: String,
    protocol_version: u32,
    nonce: String,
    port: u16,
    #[serde(rename = "pid")]
    _pid: u32,
    #[serde(default)]
    network_url: Option<String>,
}

fn marker_matches(contents: &[u8], nonce: &str, port: u16) -> bool {
    serde_json::from_slice::<ReadinessMarker>(contents).is_ok_and(|marker| {
        marker.product == READINESS_PRODUCT
            && marker.protocol_version == READINESS_PROTOCOL_VERSION
            && marker.nonce == nonce
            && marker.port == port
    })
}

fn streamlit_health_is_ready(address: SocketAddr) -> bool {
    let Ok(mut stream) = TcpStream::connect_timeout(&address, Duration::from_millis(150)) else {
        return false;
    };
    let timeout = Some(Duration::from_millis(250));
    if stream.set_read_timeout(timeout).is_err() || stream.set_write_timeout(timeout).is_err() {
        return false;
    }
    if stream
        .write_all(b"GET /_stcore/health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        .is_err()
    {
        return false;
    }
    let mut response = Vec::new();
    if stream.take(8192).read_to_end(&mut response).is_err() {
        return false;
    }
    let response = String::from_utf8_lossy(&response);
    let Some((headers, body)) = response.split_once("\r\n\r\n") else {
        return false;
    };
    (headers.starts_with("HTTP/1.1 200") || headers.starts_with("HTTP/1.0 200"))
        && body.trim() == "ok"
}

pub fn wait_for_browser_readiness(
    process: &mut OwnedChild,
    marker_path: &Path,
    nonce: &str,
    port: u16,
    deadline: Instant,
    cancellation: &CancellationToken,
) -> Result<Option<String>, String> {
    let address = SocketAddr::from(([127, 0, 0, 1], port));
    while Instant::now() < deadline {
        if cancellation.is_cancelled() {
            process.terminate_and_reap();
            let _ = fs::remove_file(marker_path);
            return Err("VidXP interface startup was cancelled.".into());
        }
        if let Some(status) = process
            .try_wait()
            .map_err(|error| format!("Could not inspect the interface process: {error}"))?
        {
            let _ = fs::remove_file(marker_path);
            return Err(format!(
                "The VidXP interface exited during startup ({status})."
            ));
        }
        if let Ok(contents) = fs::read(marker_path)
            && marker_matches(&contents, nonce, port)
            && streamlit_health_is_ready(address)
        {
            let marker = serde_json::from_slice::<ReadinessMarker>(&contents)
                .map_err(|error| format!("The interface readiness marker is invalid: {error}"))?;
            let _ = fs::remove_file(marker_path);
            return Ok(marker.network_url);
        }
        thread::sleep(Duration::from_millis(50));
    }
    process.terminate_and_reap();
    let _ = fs::remove_file(marker_path);
    Err("The VidXP interface did not publish its launch identity and become ready in 30 seconds. Another process may have captured the reserved port.".into())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{net::TcpListener, process::Command};

    fn sleeping_command() -> Command {
        if cfg!(windows) {
            let mut command = Command::new("cmd");
            command.args(["/c", "ping 127.0.0.1 -n 20 > nul"]);
            command
        } else {
            let mut command = Command::new("sh");
            command.args(["-c", "sleep 20"]);
            command
        }
    }

    fn short_command() -> Command {
        if cfg!(windows) {
            let mut command = Command::new("cmd");
            command.args(["/c", "exit 0"]);
            command
        } else {
            let mut command = Command::new("sh");
            command.args(["-c", "exit 0"]);
            command
        }
    }

    fn temporary_marker(name: &str) -> std::path::PathBuf {
        std::env::temp_dir().join(format!(
            "vidxp-readiness-{name}-{}-{}.json",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .expect("clock")
                .as_nanos()
        ))
    }

    fn health_server(listener: TcpListener) -> thread::JoinHandle<()> {
        thread::spawn(move || {
            if let Ok((mut stream, _)) = listener.accept() {
                let mut request = [0_u8; 1024];
                let _ = stream.read(&mut request);
                let _ = stream.write_all(
                    b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok",
                );
            }
        })
    }

    #[test]
    fn readiness_marker_rejects_stale_service_and_wrong_nonce() {
        let correct = br#"{"product":"dev.grayhat.vidxp","protocol_version":1,"nonce":"new","port":43123,"pid":99}"#;
        assert!(marker_matches(correct, "new", 43123));
        assert!(!marker_matches(correct, "old", 43123));
        assert!(!marker_matches(correct, "new", 43124));
        assert!(!marker_matches(b"not-json", "new", 43123));
    }

    #[test]
    fn readiness_requires_the_expected_marker_even_when_a_captured_port_is_healthy() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).expect("listener");
        let port = listener.local_addr().expect("address").port();
        listener
            .set_nonblocking(true)
            .expect("nonblocking listener");
        let server = thread::spawn(move || {
            let deadline = Instant::now() + Duration::from_secs(1);
            while Instant::now() < deadline {
                match listener.accept() {
                    Ok((mut stream, _)) => {
                        let _ = stream.write_all(
                            b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok",
                        );
                        return;
                    }
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(10));
                    }
                    Err(_) => return,
                }
            }
        });
        let mut process =
            crate::background_process::spawn_service(sleeping_command()).expect("sleeping process");
        let marker = temporary_marker("captured");
        let result = wait_for_browser_readiness(
            &mut process,
            &marker,
            "launch",
            port,
            Instant::now() + Duration::from_millis(300),
            &CancellationToken::default(),
        );
        assert!(result.is_err());
        server.join().expect("server");
    }

    #[test]
    fn readiness_fails_when_the_supervised_child_exits() {
        let mut process =
            crate::background_process::spawn_service(short_command()).expect("short process");
        let result = wait_for_browser_readiness(
            &mut process,
            &temporary_marker("exit"),
            "launch",
            9,
            Instant::now() + Duration::from_secs(2),
            &CancellationToken::default(),
        );
        assert!(
            result
                .expect_err("child exit")
                .contains("exited during startup")
        );
    }

    #[test]
    fn readiness_accepts_a_marker_pid_different_from_the_supervised_launcher() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).expect("listener");
        let port = listener.local_addr().expect("address").port();
        let server = health_server(listener);
        let mut process =
            crate::background_process::spawn_service(sleeping_command()).expect("sleeping process");
        let marker = temporary_marker("success");
        fs::write(
            &marker,
            serde_json::json!({
                "product": READINESS_PRODUCT,
                "protocol_version": READINESS_PROTOCOL_VERSION,
                "nonce": "launch",
                "port": port,
                "pid": process.id() + 1,
                "network_url": format!("http://192.168.1.20:{port}"),
            })
            .to_string(),
        )
        .expect("marker");
        let network_url = wait_for_browser_readiness(
            &mut process,
            &marker,
            "launch",
            port,
            Instant::now() + Duration::from_secs(2),
            &CancellationToken::default(),
        )
        .expect("ready");
        assert_eq!(network_url, Some(format!("http://192.168.1.20:{port}")));
        assert!(!marker.exists());
        server.join().expect("server");
    }

    #[test]
    fn readiness_cancellation_is_bounded() {
        let cancelled = CancellationToken::default();
        cancelled.cancel();
        let mut process =
            crate::background_process::spawn_service(sleeping_command()).expect("sleeping process");
        let started = Instant::now();
        let result = wait_for_browser_readiness(
            &mut process,
            &temporary_marker("cancel"),
            "launch",
            9,
            Instant::now() + Duration::from_secs(20),
            &cancelled,
        );
        assert!(result.expect_err("cancelled").contains("cancelled"));
        assert!(started.elapsed() < Duration::from_secs(2));
    }
}
