#![forbid(unsafe_code)]

mod error;
mod health;
mod ports;
mod probe;
mod runtime;

pub use error::{ApplicationError, PersistenceError};
pub use health::HealthRegistry;
pub use ports::{
    CommittedProbe, CommittedRealtimeReader, DurableOutboxConsumer, OUTBOX_CLAIM_BATCH_MAX,
    OUTBOX_LEASE, OUTBOX_MAX_ATTEMPTS, OutboxClaimRequest, OutboxClaimToken, OutboxDelivery,
    OutboxRetry, PersistenceReadiness, PlatformPersistence, ProbeCommit, ProbeEffectAckOutcome,
    REALTIME_READ_BATCH_MAX, RealtimeCursor, RealtimeDelivery, RealtimeEventBatch,
    RealtimeReadRequest,
};
pub use probe::{PlatformProbeApplication, PlatformProbeWorker, ProbeWorkerOutcome};
pub use runtime::{
    BoundedBlockingExecutor, BoundedExecutor, BoundedExecutorError, RuntimeControl,
    run_with_deadline,
};
