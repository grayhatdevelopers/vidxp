use std::{
    sync::{Arc, Condvar, Mutex},
    time::Instant,
};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum UiProcessAction {
    Reuse,
    Replace,
    Start,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum DesktopAction {
    Manage,
    OpenBrowser,
    Quit,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum DesktopCloseAction {
    HideToTray,
    Quit,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum DesktopActivation<'a> {
    Startup,
    SingleInstance,
    Tray(&'a str),
}

pub(crate) fn action_for_activation(activation: DesktopActivation<'_>) -> Option<DesktopAction> {
    match activation {
        DesktopActivation::Startup | DesktopActivation::SingleInstance => {
            Some(DesktopAction::Manage)
        }
        DesktopActivation::Tray("manage") => Some(DesktopAction::Manage),
        DesktopActivation::Tray("open") => Some(DesktopAction::OpenBrowser),
        DesktopActivation::Tray("quit") => Some(DesktopAction::Quit),
        DesktopActivation::Tray(_) => None,
    }
}

pub(crate) fn close_action(configured: bool) -> DesktopCloseAction {
    if configured {
        DesktopCloseAction::HideToTray
    } else {
        DesktopCloseAction::Quit
    }
}

pub(crate) fn ui_process_action(
    running: bool,
    active_profile_id: &str,
    requested_profile_id: &str,
) -> UiProcessAction {
    if !running {
        UiProcessAction::Start
    } else if active_profile_id == requested_profile_id {
        UiProcessAction::Reuse
    } else {
        UiProcessAction::Replace
    }
}

struct OperationState {
    count: usize,
    accepting: bool,
}

impl Default for OperationState {
    fn default() -> Self {
        Self {
            count: 0,
            accepting: true,
        }
    }
}

#[derive(Default)]
pub(crate) struct ActiveOperations {
    state: Mutex<OperationState>,
    idle: Condvar,
}

impl ActiveOperations {
    pub(crate) fn register(self: &Arc<Self>) -> Result<ActiveOperationGuard, String> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| "The background operation tracker is unavailable.".to_string())?;
        if !state.accepting {
            return Err("VidXP Desktop is shutting down.".into());
        }
        state.count += 1;
        drop(state);
        Ok(ActiveOperationGuard {
            operations: self.clone(),
        })
    }

    pub(crate) fn close(&self) -> Result<(), String> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| "The background operation tracker is unavailable.".to_string())?;
        state.accepting = false;
        if state.count == 0 {
            self.idle.notify_all();
        }
        Ok(())
    }

    pub(crate) fn wait_until_idle(&self, deadline: Instant) -> bool {
        let Ok(mut state) = self.state.lock() else {
            return false;
        };
        while state.count > 0 {
            let now = Instant::now();
            if now >= deadline {
                return false;
            }
            let Ok((next, timeout)) = self.idle.wait_timeout(state, deadline - now) else {
                return false;
            };
            state = next;
            if timeout.timed_out() && state.count > 0 {
                return false;
            }
        }
        true
    }
}

pub(crate) struct ActiveOperationGuard {
    operations: Arc<ActiveOperations>,
}

impl Drop for ActiveOperationGuard {
    fn drop(&mut self) {
        if let Ok(mut state) = self.operations.state.lock() {
            state.count = state.count.saturating_sub(1);
            if state.count == 0 {
                self.operations.idle.notify_all();
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{sync::Barrier, thread, time::Duration};

    #[test]
    fn registration_succeeds_before_close_and_is_rejected_afterward() {
        let operations = Arc::new(ActiveOperations::default());
        let active = operations.register().expect("registration before close");
        operations.close().expect("close");
        assert_eq!(
            operations.register().err().as_deref(),
            Some("VidXP Desktop is shutting down.")
        );
        drop(active);
        assert!(operations.wait_until_idle(Instant::now() + Duration::from_secs(1)));
    }

    #[test]
    fn idle_observation_after_close_cannot_race_with_a_new_registration() {
        let operations = Arc::new(ActiveOperations::default());
        operations.close().expect("close");
        assert!(operations.wait_until_idle(Instant::now() + Duration::from_secs(1)));

        let contender = operations.clone();
        let result = thread::spawn(move || contender.register().map(drop))
            .join()
            .expect("registration thread");
        assert_eq!(result.unwrap_err(), "VidXP Desktop is shutting down.");
        assert!(operations.wait_until_idle(Instant::now() + Duration::from_secs(1)));
    }

    #[test]
    fn close_waits_for_an_already_registered_operation() {
        let operations = Arc::new(ActiveOperations::default());
        let active = operations.register().expect("registration");
        operations.close().expect("close");
        let started = Arc::new(Barrier::new(2));
        let waiter_operations = operations.clone();
        let waiter_started = started.clone();
        let waiter = thread::spawn(move || {
            waiter_started.wait();
            waiter_operations.wait_until_idle(Instant::now() + Duration::from_secs(2))
        });
        started.wait();
        thread::sleep(Duration::from_millis(20));
        assert!(!waiter.is_finished());
        drop(active);
        assert!(waiter.join().expect("waiter"));
    }

    #[test]
    fn repeated_close_is_idempotent() {
        let operations = ActiveOperations::default();
        operations.close().expect("first close");
        operations.close().expect("second close");
        assert!(operations.wait_until_idle(Instant::now() + Duration::from_secs(1)));
    }
}
