use std::{
    io::Read,
    process::{Command, ExitStatus, Stdio},
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
        mpsc::{self, Receiver, TryRecvError},
    },
    thread,
    time::{Duration, Instant},
};

use process_wrap::std::{ChildWrapper, CommandWrap};

#[cfg(windows)]
use process_wrap::std::JobObject;
#[cfg(unix)]
use process_wrap::std::ProcessGroup;

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

struct OutputReader {
    receiver: Receiver<std::io::Result<Vec<u8>>>,
    handle: Option<thread::JoinHandle<()>>,
    result: Option<std::io::Result<Vec<u8>>>,
}

impl OutputReader {
    fn poll(&mut self) -> Result<bool, BackgroundError> {
        if self.result.is_some() {
            return Ok(true);
        }
        match self.receiver.try_recv() {
            Ok(result) => {
                self.result = Some(result);
                Ok(true)
            }
            Err(TryRecvError::Empty) => Ok(false),
            Err(TryRecvError::Disconnected) => Err(BackgroundError {
                kind: BackgroundErrorKind::Output,
                detail: "an output reader stopped without returning its result".into(),
            }),
        }
    }

    fn finish(mut self) -> Result<Vec<u8>, BackgroundError> {
        let result = self.result.take().ok_or_else(|| BackgroundError {
            kind: BackgroundErrorKind::Output,
            detail: "an output reader was collected before it finished".into(),
        })?;
        self.handle
            .take()
            .expect("reader handle is retained until collection")
            .join()
            .map_err(|_| BackgroundError {
                kind: BackgroundErrorKind::Output,
                detail: "an output reader panicked".into(),
            })?;
        result.map_err(|error| BackgroundError {
            kind: BackgroundErrorKind::Output,
            detail: error.to_string(),
        })
    }
}

fn read_bounded(stream: impl Read + Send + 'static, limit: usize) -> OutputReader {
    let (sender, receiver) = mpsc::sync_channel(1);
    let handle = thread::spawn(move || {
        let mut bytes = Vec::new();
        let result = stream
            .take(limit as u64 + 1)
            .read_to_end(&mut bytes)
            .map(|_| bytes);
        let _ = sender.send(result);
    });
    OutputReader {
        receiver,
        handle: Some(handle),
        result: None,
    }
}

fn wrapped(command: Command) -> CommandWrap {
    let mut wrapped = CommandWrap::from(command);
    #[cfg(windows)]
    wrapped.wrap(JobObject);
    #[cfg(unix)]
    wrapped.wrap(ProcessGroup::leader());
    wrapped
}

fn spawn_owned(command: Command) -> std::io::Result<Box<dyn ChildWrapper>> {
    let mut wrapped = wrapped(command);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        use windows::Win32::System::Threading::{CREATE_NO_WINDOW, CREATE_SUSPENDED};

        // JobObject starts the child suspended so it can establish whole-tree ownership before
        // allowing it to run. Apply both flags at the final spawn boundary: every desktop command
        // stays console-free without weakening job supervision.
        wrapped.spawn_with(|command| {
            command.creation_flags((CREATE_NO_WINDOW | CREATE_SUSPENDED).0);
            command.spawn()
        })
    }
    #[cfg(not(windows))]
    wrapped.spawn()
}

pub struct OwnedChild(Box<dyn ChildWrapper>);

impl OwnedChild {
    #[cfg(test)]
    pub fn id(&self) -> u32 {
        self.0.id()
    }

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
    spawn_owned(command)
        .map(OwnedChild)
        .map_err(|error| BackgroundError {
            kind: BackgroundErrorKind::Start,
            detail: error.to_string(),
        })
}

fn finish_error(
    child: &mut OwnedChild,
    mut stdout: OutputReader,
    mut stderr: OutputReader,
    kind: BackgroundErrorKind,
    detail: String,
) -> BackgroundError {
    child.terminate_and_reap();
    let cleanup_deadline = Instant::now() + Duration::from_secs(2);
    while Instant::now() < cleanup_deadline {
        let stdout_done = stdout.poll().unwrap_or(true);
        let stderr_done = stderr.poll().unwrap_or(true);
        if stdout_done && stderr_done {
            let _ = stdout.finish();
            let _ = stderr.finish();
            return BackgroundError { kind, detail };
        }
        thread::sleep(Duration::from_millis(10));
    }
    BackgroundError {
        kind: BackgroundErrorKind::Output,
        detail: format!(
            "{detail}; owned process output pipes did not close after whole-tree termination"
        ),
    }
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
    let mut child = spawn_owned(command)
        .map(OwnedChild)
        .map_err(|error| BackgroundError {
            kind: BackgroundErrorKind::Start,
            detail: error.to_string(),
        })?;
    let mut stdout = read_bounded(
        child.0.stdout().take().expect("background stdout is piped"),
        policy.max_output_bytes,
    );
    let mut stderr = read_bounded(
        child.0.stderr().take().expect("background stderr is piped"),
        policy.max_output_bytes,
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
        if stdout.result.as_ref().is_some_and(|result| {
            result
                .as_ref()
                .is_ok_and(|bytes| bytes.len() > policy.max_output_bytes)
        }) || stderr.result.as_ref().is_some_and(|result| {
            result
                .as_ref()
                .is_ok_and(|bytes| bytes.len() > policy.max_output_bytes)
        }) {
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
        if let Err(error) = stdout.poll().and_then(|_| stderr.poll()) {
            return Err(finish_error(
                &mut child,
                stdout,
                stderr,
                error.kind,
                error.detail,
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
    while !(stdout.poll()? && stderr.poll()?) {
        if cancellation.is_some_and(CancellationToken::is_cancelled) {
            return Err(finish_error(
                &mut child,
                stdout,
                stderr,
                BackgroundErrorKind::Cancelled,
                "the operation was cancelled while collecting process output".into(),
            ));
        }
        if Instant::now() >= deadline {
            return Err(finish_error(
                &mut child,
                stdout,
                stderr,
                BackgroundErrorKind::Timeout,
                format!(
                    "the operation exceeded {} seconds while descendants retained its output pipes",
                    policy.timeout.as_secs()
                ),
            ));
        }
        thread::sleep(Duration::from_millis(10));
    }
    let stdout = stdout.finish()?;
    let stderr = stderr.finish()?;
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

    #[cfg(windows)]
    #[test]
    fn windows_console_probe() {
        if std::env::var_os("VIDXP_WINDOWS_CONSOLE_PROBE").is_none() {
            return;
        }

        unsafe extern "system" {
            fn GetConsoleWindow() -> *mut std::ffi::c_void;
        }

        assert!(unsafe { GetConsoleWindow() }.is_null());
    }

    #[cfg(windows)]
    fn console_probe_command() -> Command {
        let mut command = Command::new(std::env::current_exe().expect("current test executable"));
        command
            .args([
                "--exact",
                "background_process::tests::windows_console_probe",
            ])
            .env("VIDXP_WINDOWS_CONSOLE_PROBE", "1");
        command
    }

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

    #[cfg(windows)]
    #[test]
    fn captured_commands_do_not_create_a_windows_console() {
        let result = run(
            console_probe_command(),
            BackgroundPolicy {
                timeout: Duration::from_secs(5),
                max_output_bytes: 4096,
            },
            None,
        )
        .expect("console probe");
        assert!(
            result.status.success(),
            "console probe failed: {}",
            String::from_utf8_lossy(&result.stderr)
        );
    }

    #[cfg(windows)]
    #[test]
    fn service_commands_do_not_create_a_windows_console() {
        let mut child = spawn_service(console_probe_command()).expect("console probe service");
        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            if let Some(status) = child.try_wait().expect("console probe status") {
                assert!(status.success(), "console probe failed");
                break;
            }
            assert!(Instant::now() < deadline, "console probe timed out");
            thread::sleep(Duration::from_millis(10));
        }
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

    #[test]
    fn inherited_output_pipe_cannot_outlive_the_operation_deadline() {
        let script = if cfg!(windows) {
            "start \"\" /b cmd /c \"ping 127.0.0.1 -n 10 ^> nul\" & echo root-finished"
        } else {
            "(sleep 10) & echo root-finished"
        };
        let started = Instant::now();
        let error = run(
            shell_command(script),
            BackgroundPolicy {
                timeout: Duration::from_millis(150),
                max_output_bytes: 1024,
            },
            None,
        )
        .expect_err("inherited pipe timeout");
        assert_eq!(error.kind, BackgroundErrorKind::Timeout);
        assert!(started.elapsed() < Duration::from_secs(3));
    }
}
