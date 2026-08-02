use std::{
    io::Read,
    process::{Command, ExitStatus, Stdio},
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    thread,
    time::{Duration, Instant},
};

use process_wrap::std::{ChildWrapper, CommandWrap};

#[cfg(unix)]
use process_wrap::std::ProcessGroup;
#[cfg(windows)]
use process_wrap::std::{CreationFlags, JobObject};

#[derive(Clone, Copy)]
pub struct BackgroundPolicy {
    pub timeout: Duration,
    pub max_output_bytes: usize,
}

#[derive(Clone, Default)]
pub struct CancellationToken(Arc<AtomicBool>);

impl CancellationToken {
    pub fn cancel(&self) {
        self.0.store(true, Ordering::Release);
    }

    pub fn is_cancelled(&self) -> bool {
        self.0.load(Ordering::Acquire)
    }

    pub fn same(&self, other: &Self) -> bool {
        Arc::ptr_eq(&self.0, &other.0)
    }
}

#[derive(Debug)]
pub struct BackgroundOutput {
    pub status: ExitStatus,
    pub stdout: Vec<u8>,
    pub stderr: Vec<u8>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
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
    exceeded: Arc<AtomicBool>,
) -> thread::JoinHandle<std::io::Result<Vec<u8>>> {
    thread::spawn(move || {
        let mut bytes = Vec::new();
        stream.take(limit as u64 + 1).read_to_end(&mut bytes)?;
        if bytes.len() > limit {
            exceeded.store(true, Ordering::Release);
        }
        Ok(bytes)
    })
}

fn wrapped(command: Command) -> CommandWrap {
    let mut wrapped = CommandWrap::from(command);
    #[cfg(windows)]
    {
        use windows::Win32::System::Threading::CREATE_NO_WINDOW;
        wrapped.wrap(CreationFlags(CREATE_NO_WINDOW));
        wrapped.wrap(JobObject);
    }
    #[cfg(unix)]
    wrapped.wrap(ProcessGroup::leader());
    wrapped
}

pub struct OwnedChild(Box<dyn ChildWrapper>);

impl OwnedChild {
    pub fn try_wait(&mut self) -> std::io::Result<Option<ExitStatus>> {
        self.0.try_wait()
    }

    pub fn terminate_and_reap(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

impl Drop for OwnedChild {
    fn drop(&mut self) {
        self.terminate_and_reap();
    }
}

pub fn spawn_service(mut command: Command) -> Result<OwnedChild, BackgroundError> {
    command
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    wrapped(command)
        .spawn()
        .map(OwnedChild)
        .map_err(|error| BackgroundError {
            kind: BackgroundErrorKind::Start,
            detail: error.to_string(),
        })
}

fn join_reader(
    reader: thread::JoinHandle<std::io::Result<Vec<u8>>>,
) -> Result<Vec<u8>, BackgroundError> {
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
}

fn finish_error(
    child: &mut OwnedChild,
    stdout: thread::JoinHandle<std::io::Result<Vec<u8>>>,
    stderr: thread::JoinHandle<std::io::Result<Vec<u8>>>,
    kind: BackgroundErrorKind,
    detail: String,
) -> BackgroundError {
    child.terminate_and_reap();
    let _ = stdout.join();
    let _ = stderr.join();
    BackgroundError { kind, detail }
}

fn run_with_monitor<F>(
    mut command: Command,
    policy: BackgroundPolicy,
    cancellation: Option<&CancellationToken>,
    mut monitor: F,
) -> Result<BackgroundOutput, BackgroundError>
where
    F: FnMut(&mut OwnedChild) -> std::io::Result<Option<ExitStatus>>,
{
    command
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = wrapped(command)
        .spawn()
        .map(OwnedChild)
        .map_err(|error| BackgroundError {
            kind: BackgroundErrorKind::Start,
            detail: error.to_string(),
        })?;
    let exceeded = Arc::new(AtomicBool::new(false));
    let stdout = read_bounded(
        child.0.stdout().take().expect("background stdout is piped"),
        policy.max_output_bytes,
        exceeded.clone(),
    );
    let stderr = read_bounded(
        child.0.stderr().take().expect("background stderr is piped"),
        policy.max_output_bytes,
        exceeded.clone(),
    );
    let deadline = Instant::now() + policy.timeout;
    let status = loop {
        if cancellation.is_some_and(CancellationToken::is_cancelled) {
            return Err(finish_error(
                &mut child,
                stdout,
                stderr,
                BackgroundErrorKind::Cancelled,
                "the operation was cancelled".into(),
            ));
        }
        if exceeded.load(Ordering::Acquire) {
            return Err(finish_error(
                &mut child,
                stdout,
                stderr,
                BackgroundErrorKind::OutputTooLarge,
                format!(
                    "the operation returned more than {} bytes per output stream",
                    policy.max_output_bytes
                ),
            ));
        }
        if Instant::now() >= deadline {
            return Err(finish_error(
                &mut child,
                stdout,
                stderr,
                BackgroundErrorKind::Timeout,
                format!(
                    "the operation exceeded {} seconds",
                    policy.timeout.as_secs()
                ),
            ));
        }
        match monitor(&mut child) {
            Ok(Some(status)) => break status,
            Ok(None) => thread::sleep(Duration::from_millis(50)),
            Err(error) => {
                return Err(finish_error(
                    &mut child,
                    stdout,
                    stderr,
                    BackgroundErrorKind::Monitor,
                    error.to_string(),
                ));
            }
        }
    };
    let stdout = join_reader(stdout)?;
    let stderr = join_reader(stderr)?;
    Ok(BackgroundOutput {
        status,
        stdout,
        stderr,
    })
}

pub fn run(
    command: Command,
    policy: BackgroundPolicy,
    cancellation: Option<&CancellationToken>,
) -> Result<BackgroundOutput, BackgroundError> {
    run_with_monitor(command, policy, cancellation, |child| child.try_wait())
}

pub async fn run_async(
    command: Command,
    policy: BackgroundPolicy,
    cancellation: CancellationToken,
) -> Result<BackgroundOutput, BackgroundError> {
    tauri::async_runtime::spawn_blocking(move || run(command, policy, Some(&cancellation)))
        .await
        .map_err(|error| BackgroundError {
            kind: BackgroundErrorKind::Monitor,
            detail: format!("the background monitor stopped unexpectedly: {error}"),
        })?
}

#[cfg(test)]
mod tests {
    use super::*;

    fn shell_command(script: &str) -> Command {
        if cfg!(windows) {
            let mut command = Command::new("cmd");
            command.args(["/c", script]);
            command
        } else {
            let mut command = Command::new("sh");
            command.args(["-c", script]);
            command
        }
    }

    #[test]
    fn bounded_runner_captures_output() {
        let result = run(
            shell_command("echo runner"),
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

    #[test]
    fn timeout_terminates_the_owned_process_tree() {
        let script = if cfg!(windows) {
            "ping 127.0.0.1 -n 10 > nul"
        } else {
            "sleep 10"
        };
        let error = run(
            shell_command(script),
            BackgroundPolicy {
                timeout: Duration::from_millis(100),
                max_output_bytes: 1024,
            },
            None,
        )
        .expect_err("timeout");
        assert_eq!(error.kind, BackgroundErrorKind::Timeout);
    }

    #[test]
    fn cancellation_terminates_the_owned_process_tree() {
        let cancellation = CancellationToken::default();
        cancellation.cancel();
        let error = run(
            shell_command("echo never"),
            BackgroundPolicy {
                timeout: Duration::from_secs(2),
                max_output_bytes: 1024,
            },
            Some(&cancellation),
        )
        .expect_err("cancelled");
        assert_eq!(error.kind, BackgroundErrorKind::Cancelled);
    }

    #[test]
    fn monitor_failure_terminates_and_reaps_the_owned_process_tree() {
        let script = if cfg!(windows) {
            "ping 127.0.0.1 -n 10 > nul"
        } else {
            "sleep 10"
        };
        let error = run_with_monitor(
            shell_command(script),
            BackgroundPolicy {
                timeout: Duration::from_secs(2),
                max_output_bytes: 1024,
            },
            None,
            |_| Err(std::io::Error::other("injected monitor failure")),
        )
        .expect_err("monitor failure");
        assert_eq!(error.kind, BackgroundErrorKind::Monitor);
    }
}
