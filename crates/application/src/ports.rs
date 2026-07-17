use async_trait::async_trait;
use liqi_protocol::{CreateProbeRequest, EventEnvelope, RequestContext};
use serde_json::Value;
use std::{fmt, time::Duration};
use time::OffsetDateTime;
use uuid::Uuid;

use crate::PersistenceError;

pub const OUTBOX_CLAIM_BATCH_MAX: usize = 50;
pub const OUTBOX_LEASE: Duration = Duration::from_secs(30);
pub const OUTBOX_MAX_ATTEMPTS: u32 = 8;
pub const REALTIME_READ_BATCH_MAX: usize = 50;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PersistenceReadiness {
    pub database_reachable: bool,
    pub migration_ready: bool,
}

impl PersistenceReadiness {
    #[must_use]
    pub const fn ready(self) -> bool {
        self.database_reachable && self.migration_ready
    }
}

#[derive(Debug, Clone)]
pub struct ProbeCommit {
    pub probe_id: Uuid,
    pub event_id: Uuid,
    pub request: CreateProbeRequest,
    pub request_context: RequestContext,
    pub committed_at: OffsetDateTime,
}

#[derive(Debug, Clone)]
pub struct CommittedProbe {
    pub probe_id: Uuid,
    pub event_id: Uuid,
    pub committed_at: OffsetDateTime,
}

#[derive(Clone, PartialEq, Eq, Hash)]
pub struct OutboxClaimToken(String);

impl OutboxClaimToken {
    /// Creates an opaque lease token while enforcing the bounded token contract.
    ///
    /// # Errors
    ///
    /// Returns an error when the token is empty or exceeds 512 bytes.
    pub fn from_opaque(value: impl Into<String>) -> Result<Self, PersistenceError> {
        let value = value.into();
        if value.is_empty() || value.len() > 512 {
            return Err(PersistenceError::InvalidOperation);
        }
        Ok(Self(value))
    }

    #[must_use]
    pub fn as_opaque(&self) -> &str {
        &self.0
    }
}

impl fmt::Debug for OutboxClaimToken {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("OutboxClaimToken([OPAQUE])")
    }
}

#[derive(Debug, Clone)]
pub struct OutboxClaimRequest {
    pub consumer: String,
    pub event_types: Vec<String>,
    pub batch_size: usize,
    pub lease_for: Duration,
}

impl OutboxClaimRequest {
    /// Validates an outbox claim request against the V0 consumer bounds.
    ///
    /// # Errors
    ///
    /// Returns an error when the consumer, event types, batch size, or lease is invalid.
    pub fn validate(&self) -> Result<(), PersistenceError> {
        if self.consumer.is_empty()
            || self.consumer.len() > 128
            || self.event_types.is_empty()
            || self.event_types.len() > 16
            || self.batch_size == 0
            || self.batch_size > OUTBOX_CLAIM_BATCH_MAX
            || self.lease_for != OUTBOX_LEASE
        {
            return Err(PersistenceError::InvalidOperation);
        }
        Ok(())
    }
}

#[derive(Debug, Clone)]
pub struct OutboxDelivery {
    pub claim_token: OutboxClaimToken,
    pub attempt: u32,
    pub event: EventEnvelope<Value>,
}

#[derive(Debug, Clone)]
pub struct OutboxRetry {
    pub attempt: u32,
    pub max_attempts: u32,
    pub delay: Duration,
    pub reason_code: &'static str,
}

impl OutboxRetry {
    /// Validates a bounded retry decision.
    ///
    /// # Errors
    ///
    /// Returns an error when attempts, delay, or reason code violate the V0 retry contract.
    pub fn validate(&self) -> Result<(), PersistenceError> {
        if self.attempt == 0
            || self.max_attempts != OUTBOX_MAX_ATTEMPTS
            || self.attempt > self.max_attempts
            || self.delay > Duration::from_mins(1)
            || self.reason_code.is_empty()
            || self.reason_code.len() > 64
        {
            return Err(PersistenceError::InvalidOperation);
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProbeEffectAckOutcome {
    AppliedAndAcknowledged,
    AlreadyAcknowledged,
    LeaseLost,
    TerminalWithoutEffect,
}

#[derive(Clone, PartialEq, Eq)]
pub struct RealtimeCursor(String);

impl RealtimeCursor {
    /// Creates an opaque realtime cursor while enforcing the bounded cursor contract.
    ///
    /// # Errors
    ///
    /// Returns an error when the cursor is empty or exceeds 512 bytes.
    pub fn from_opaque(value: impl Into<String>) -> Result<Self, PersistenceError> {
        let value = value.into();
        if value.is_empty() || value.len() > 512 {
            return Err(PersistenceError::InvalidOperation);
        }
        Ok(Self(value))
    }

    #[must_use]
    pub fn as_opaque(&self) -> &str {
        &self.0
    }
}

impl fmt::Debug for RealtimeCursor {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("RealtimeCursor([OPAQUE])")
    }
}

#[derive(Debug, Clone)]
pub struct RealtimeReadRequest {
    pub topics: Vec<String>,
    pub after: Option<RealtimeCursor>,
    pub batch_size: usize,
}

impl RealtimeReadRequest {
    /// Validates a realtime read request against the V0 topic and batch bounds.
    ///
    /// # Errors
    ///
    /// Returns an error when topics are empty or excessive, or the batch size is invalid.
    pub fn validate(&self) -> Result<(), PersistenceError> {
        if self.topics.is_empty()
            || self.topics.len() > 64
            || self.batch_size == 0
            || self.batch_size > REALTIME_READ_BATCH_MAX
        {
            return Err(PersistenceError::InvalidOperation);
        }
        Ok(())
    }
}

#[derive(Debug, Clone)]
pub struct RealtimeDelivery {
    pub cursor: RealtimeCursor,
    pub event: EventEnvelope<Value>,
}

#[derive(Debug, Clone)]
pub struct RealtimeEventBatch {
    pub deliveries: Vec<RealtimeDelivery>,
    pub next_cursor: Option<RealtimeCursor>,
}

#[async_trait]
pub trait PlatformPersistence: Send + Sync {
    async fn readiness(&self) -> Result<PersistenceReadiness, PersistenceError>;

    /// Drains and closes provider-owned resources. Implementations must be
    /// idempotent; callers enforce the configured shutdown deadline.
    async fn close(&self) {}

    /// Must atomically commit the platform probe and its durable outbox event.
    /// Returning success before commit violates the V0 contract.
    async fn commit_probe(&self, commit: ProbeCommit) -> Result<CommittedProbe, PersistenceError>;

    /// Atomically records the terminal platform-probe effect and acknowledges
    /// the durable lease. The event ID is the idempotency key. Implementations
    /// must never expose a state where the effect committed but acknowledgement did not.
    async fn apply_probe_effect_and_ack(
        &self,
        event_id: Uuid,
        claim: &OutboxClaimToken,
        consumer_id: &str,
    ) -> Result<ProbeEffectAckOutcome, PersistenceError>;
}

#[async_trait]
pub trait DurableOutboxConsumer: Send + Sync {
    /// Implementations must return only events visible after producer commit.
    /// Claim tokens are unique per lease and stale tokens cannot mutate a newer lease.
    async fn claim(
        &self,
        request: OutboxClaimRequest,
    ) -> Result<Vec<OutboxDelivery>, PersistenceError>;

    async fn acknowledge(
        &self,
        event_id: Uuid,
        claim: &OutboxClaimToken,
        consumer_id: &str,
    ) -> Result<(), PersistenceError>;

    /// Records a bounded failure/retry decision. On the configured final
    /// attempt the database provider retains the event in dead-letter state.
    async fn retry(
        &self,
        event_id: Uuid,
        claim: &OutboxClaimToken,
        consumer_id: &str,
        retry: OutboxRetry,
    ) -> Result<(), PersistenceError>;
}

#[async_trait]
pub trait CommittedRealtimeReader: Send + Sync {
    /// Reads committed realtime handoff data through the provider-approved
    /// realtime interface. This is intentionally not an outbox claim API.
    async fn read_committed(
        &self,
        request: RealtimeReadRequest,
    ) -> Result<RealtimeEventBatch, PersistenceError>;
}
