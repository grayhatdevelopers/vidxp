use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum ActivationStage {
    Prepared,
    ProfileWritten,
    Committed,
    RollingBack,
    RolledBack,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum ActivationRecovery {
    RollBack,
    Complete,
}

pub(crate) fn activation_recovery(
    stage: &ActivationStage,
    candidate_authorities_match: bool,
) -> ActivationRecovery {
    match stage {
        ActivationStage::RollingBack | ActivationStage::RolledBack => ActivationRecovery::RollBack,
        ActivationStage::Committed => ActivationRecovery::Complete,
        ActivationStage::Prepared | ActivationStage::ProfileWritten
            if candidate_authorities_match =>
        {
            ActivationRecovery::Complete
        }
        ActivationStage::Prepared | ActivationStage::ProfileWritten => ActivationRecovery::RollBack,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[derive(Clone, Copy, Debug, Eq, PartialEq)]
    enum StartupResult {
        Previous,
        Candidate,
    }

    #[derive(Clone, Copy, Debug)]
    enum FailureBoundary {
        FinalizeRename,
        PreparedJournalWrite,
        ProfileStoreSave,
        ProfileWrittenJournalWrite,
        ActivePointerWrite,
        CommittedJournalWrite,
        CommittedJournalRemoval,
        RollbackMarkerWrite,
        RollbackPointerWrite,
        RollbackStoreSave,
        RolledBackMarkerWrite,
        RolledBackJournalRemoval,
    }

    fn startup_after(boundary: FailureBoundary) -> StartupResult {
        use FailureBoundary::*;
        let (journal, authorities_match) = match boundary {
            FinalizeRename | PreparedJournalWrite => return StartupResult::Previous,
            ProfileStoreSave => (ActivationStage::Prepared, false),
            ProfileWrittenJournalWrite => (ActivationStage::Prepared, false),
            ActivePointerWrite => (ActivationStage::ProfileWritten, false),
            CommittedJournalWrite => (ActivationStage::ProfileWritten, true),
            CommittedJournalRemoval => (ActivationStage::Committed, true),
            RollbackMarkerWrite => (ActivationStage::ProfileWritten, false),
            RollbackPointerWrite | RollbackStoreSave | RolledBackMarkerWrite => {
                (ActivationStage::RollingBack, true)
            }
            RolledBackJournalRemoval => (ActivationStage::RolledBack, true),
        };
        match activation_recovery(&journal, authorities_match) {
            ActivationRecovery::RollBack => StartupResult::Previous,
            ActivationRecovery::Complete => StartupResult::Candidate,
        }
    }

    #[test]
    fn every_activation_failure_boundary_has_an_unambiguous_startup_result() {
        use FailureBoundary::*;
        for boundary in [
            FinalizeRename,
            PreparedJournalWrite,
            ProfileStoreSave,
            ProfileWrittenJournalWrite,
            ActivePointerWrite,
            RollbackMarkerWrite,
            RollbackPointerWrite,
            RollbackStoreSave,
            RolledBackMarkerWrite,
            RolledBackJournalRemoval,
        ] {
            assert_eq!(
                startup_after(boundary),
                StartupResult::Previous,
                "{boundary:?}"
            );
        }
        for boundary in [CommittedJournalWrite, CommittedJournalRemoval] {
            assert_eq!(
                startup_after(boundary),
                StartupResult::Candidate,
                "{boundary:?}"
            );
        }
    }

    #[test]
    fn rollback_markers_permanently_prevent_candidate_recommit() {
        for stage in [ActivationStage::RollingBack, ActivationStage::RolledBack] {
            assert_eq!(
                activation_recovery(&stage, true),
                ActivationRecovery::RollBack
            );
        }
    }
}
