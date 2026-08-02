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

#[derive(Default)]
pub(crate) struct ActiveOperations {
    count: Mutex<usize>,
    idle: Condvar,
}

impl ActiveOperations {
    pub(crate) fn register(self: &Arc<Self>) -> Result<ActiveOperationGuard, String> {
        let mut count = self
            .count
            .lock()
            .map_err(|_| "The background operation tracker is unavailable.".to_string())?;
        *count += 1;
        drop(count);
        Ok(ActiveOperationGuard {
            operations: self.clone(),
        })
    }

    pub(crate) fn wait_until_idle(&self, deadline: Instant) -> bool {
        let Ok(mut count) = self.count.lock() else {
            return false;
        };
        while *count > 0 {
            let now = Instant::now();
            if now >= deadline {
                return false;
            }
            let Ok((next, timeout)) = self.idle.wait_timeout(count, deadline - now) else {
                return false;
            };
            count = next;
            if timeout.timed_out() && *count > 0 {
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
        if let Ok(mut count) = self.operations.count.lock() {
            *count = count.saturating_sub(1);
            if *count == 0 {
                self.operations.idle.notify_all();
            }
        }
    }
}
