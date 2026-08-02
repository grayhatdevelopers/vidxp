use std::{
    io::Read,
    process::{Command, ExitStatus, Stdio},
    sync::atomic::{AtomicBool, Ordering},
    thread,
    time::{Duration, Instant},
};

use wait_timeout::ChildExt;

#[derive(Clone, Copy)]
pub struct BackgroundPolicy {
    pub timeout: Duration,
    pub max_output_bytes: usize,
}

#[derive(Debug)]
pub struct BackgroundOutput {
    pub status: ExitStatus,
    pub stdout: Vec<u8>,
    pub stderr: Vec<u8>,
}

#[derive(Debug, Eq, PartialEq)]
pub enum BackgroundErrorKind {
    Start,
    Monitor,
    Timeout,
    Cancelled,
    Output,
    OutputTooLarge,
}

#[derive(Debug)]
pub struct BackgroundError {
    pub kind: BackgroundErrorKind,
    pub detail: String,
}

fn read_bounded(
    stream: impl Read + Send + 'static,
    limit: usize,
) -> thread::JoinHandle<std::io::Result<Vec<u8>>> {
    thread::spawn(move || {
        let mut bytes = Vec::new();
        stream.take(limit as u64 + 1).read_to_end(&mut bytes)?;
        Ok(bytes)
    })
}

fn terminate_and_reap(child: &mut std::process::Child) {
    let _ = child.kill();
    let _ = child.wait();
}

pub fn hidden(command: &mut Command) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }
}

pub fn run(
    command: &mut Command,
    policy: BackgroundPolicy,
    cancellation: Option<&AtomicBool>,
) -> Result<BackgroundOutput, BackgroundError> {
    hidden(command);
    command
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = command.spawn().map_err(|error| BackgroundError {
        kind: BackgroundErrorKind::Start,
        detail: error.to_string(),
    })?;
    let stdout = read_bounded(
        child.stdout.take().expect("background stdout is piped"),
        policy.max_output_bytes,
    );
    let stderr = read_bounded(
        child.stderr.take().expect("background stderr is piped"),
        policy.max_output_bytes,
    );
    let deadline = Instant::now() + policy.timeout;
    let status = loop {
        if cancellation.is_some_and(|flag| flag.load(Ordering::Acquire)) {
            terminate_and_reap(&mut child);
            let _ = stdout.join();
            let _ = stderr.join();
            return Err(BackgroundError {
                kind: BackgroundErrorKind::Cancelled,
                detail: "the operation was cancelled".into(),
            });
        }
        if Instant::now() >= deadline {
            terminate_and_reap(&mut child);
            let _ = stdout.join();
            let _ = stderr.join();
            return Err(BackgroundError {
                kind: BackgroundErrorKind::Timeout,
                detail: format!(
                    "the operation exceeded {} seconds",
                    policy.timeout.as_secs()
                ),
            });
        }
        match child
            .wait_timeout(Duration::from_millis(50))
            .map_err(|error| BackgroundError {
                kind: BackgroundErrorKind::Monitor,
                detail: error.to_string(),
            })? {
            Some(status) => break status,
            None => continue,
        }
    };
    let join = |reader: thread::JoinHandle<std::io::Result<Vec<u8>>>| {
        reader
            .join()
            .map_err(|_| BackgroundError {
                kind: BackgroundErrorKind::Output,
                detail: "an output reader stopped unexpectedly".into(),
            })?
            .map_err(|error| BackgroundError {
                kind: BackgroundErrorKind::Output,
                detail: error.to_string(),
            })
    };
    let stdout = join(stdout)?;
    let stderr = join(stderr)?;
    if stdout.len() > policy.max_output_bytes || stderr.len() > policy.max_output_bytes {
        return Err(BackgroundError {
            kind: BackgroundErrorKind::OutputTooLarge,
            detail: format!(
                "the operation returned more than {} bytes per output stream",
                policy.max_output_bytes
            ),
        });
    }
    Ok(BackgroundOutput {
        status,
        stdout,
        stderr,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bounded_runner_captures_output() {
        let mut command = if cfg!(windows) {
            let mut command = Command::new("cmd");
            command.args(["/c", "echo runner"]);
            command
        } else {
            let mut command = Command::new("sh");
            command.args(["-c", "printf runner"]);
            command
        };
        let result = run(
            &mut command,
            BackgroundPolicy {
                timeout: Duration::from_secs(2),
                max_output_bytes: 1024,
            },
            None,
        )
        .expect("background command");
        assert!(result.status.success());
        assert!(String::from_utf8_lossy(&result.stdout).contains("runner"));
    }
}
