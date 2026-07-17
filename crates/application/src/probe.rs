use futures_util::{StreamExt as _, stream};
use liqi_protocol::{CreateProbeRequest, CreateProbeResponse, ProbeStatus, RequestContext};
use serde_json::Value;
use std::{sync::Arc, time::Duration};
use time::OffsetDateTime;
use tokio_util::sync::CancellationToken;
use tracing::{info, warn};
use uuid::Uuid;

use crate::{
    ApplicationError, DurableOutboxConsumer, OUTBOX_LEASE, OUTBOX_MAX_ATTEMPTS, OutboxClaimRequest,
    OutboxRetry, PersistenceError, PlatformPersistence, ProbeCommit, ProbeEffectAckOutcome,
    run_with_deadline,
};

const PLATFORM_PROBE_EVENT_NAMESPACE: Uuid = Uuid::from_bytes([
    0x62, 0x67, 0x11, 0x6f, 0xb6, 0xb2, 0x5a, 0xf5, 0x95, 0xa7, 0xaf, 0x18, 0x78, 0x38, 0x5a, 0x31,
]);
const PROBE_EVENT_TYPE: &str = "platform.probe.requested.v0";
const PROBE_CONSUMER: &str = "liqi-platform-probe-worker-v0";

#[derive(Clone)]
pub struct PlatformProbeApplication {
    persistence: Arc<dyn PlatformPersistence>,
    request_timeout: Duration,
}

impl std::fmt::Debug for PlatformProbeApplication {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("PlatformProbeApplication")
            .field("request_timeout", &self.request_timeout)
            .finish_non_exhaustive()
    }
}

impl PlatformProbeApplication {
    #[must_use]
    pub fn new(persistence: Arc<dyn PlatformPersistence>, request_timeout: Duration) -> Self {
        Self {
            persistence,
            request_timeout,
        }
    }

    pub async fn create(
        &self,
        request: CreateProbeRequest,
        context: RequestContext,
        cancellation: CancellationToken,
    ) -> Result<CreateProbeResponse, ApplicationError> {
        let probe_id = request.client_probe_id;
        let event_id = Uuid::new_v5(&PLATFORM_PROBE_EVENT_NAMESPACE, probe_id.as_bytes());
        let commit = ProbeCommit {
            probe_id,
            event_id,
            request,
            request_context: context,
            committed_at: OffsetDateTime::now_utc(),
        };
        let persistence = Arc::clone(&self.persistence);
        let committed = run_with_deadline(self.request_timeout, cancellation, async move {
            persistence
                .commit_probe(commit)
                .await
                .map_err(ApplicationError::from)
        })
        .await?;
        Ok(CreateProbeResponse {
            probe_id: committed.probe_id,
            event_id: committed.event_id,
            status: ProbeStatus::Accepted,
        })
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ProbeWorkerOutcome {
    pub claimed: usize,
    pub acknowledged: usize,
    pub retried: usize,
    pub applied: usize,
    pub duplicates: usize,
    pub lease_lost: usize,
    pub terminal_without_effect: usize,
}

#[derive(Clone)]
pub struct PlatformProbeWorker {
    persistence: Arc<dyn PlatformPersistence>,
    outbox: Arc<dyn DurableOutboxConsumer>,
    claim_batch: usize,
    lease_for: Duration,
    retry_base: Duration,
    retry_max: Duration,
    concurrency: usize,
    consumer_id: String,
}

impl std::fmt::Debug for PlatformProbeWorker {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("PlatformProbeWorker")
            .field("claim_batch", &self.claim_batch)
            .field("lease_for", &self.lease_for)
            .field("retry_base", &self.retry_base)
            .field("retry_max", &self.retry_max)
            .field("concurrency", &self.concurrency)
            .field("consumer_id", &self.consumer_id)
            .finish_non_exhaustive()
    }
}

impl PlatformProbeWorker {
    #[must_use]
    pub fn new(
        persistence: Arc<dyn PlatformPersistence>,
        outbox: Arc<dyn DurableOutboxConsumer>,
        claim_batch: usize,
        retry_base: Duration,
        retry_max: Duration,
        concurrency: usize,
    ) -> Self {
        Self::build(
            persistence,
            outbox,
            claim_batch,
            retry_base,
            retry_max,
            concurrency,
            PROBE_CONSUMER.to_owned(),
        )
    }

    pub fn try_with_consumer_id(
        persistence: Arc<dyn PlatformPersistence>,
        outbox: Arc<dyn DurableOutboxConsumer>,
        claim_batch: usize,
        retry_base: Duration,
        retry_max: Duration,
        concurrency: usize,
        consumer_id: impl Into<String>,
    ) -> Result<Self, ApplicationError> {
        let consumer_id = consumer_id.into();
        if consumer_id.is_empty() || consumer_id.len() > 128 {
            return Err(ApplicationError::InvalidRequest(Vec::new()));
        }
        Ok(Self::build(
            persistence,
            outbox,
            claim_batch,
            retry_base,
            retry_max,
            concurrency,
            consumer_id,
        ))
    }

    fn build(
        persistence: Arc<dyn PlatformPersistence>,
        outbox: Arc<dyn DurableOutboxConsumer>,
        claim_batch: usize,
        retry_base: Duration,
        retry_max: Duration,
        concurrency: usize,
        consumer_id: String,
    ) -> Self {
        Self {
            persistence,
            outbox,
            claim_batch: claim_batch.clamp(1, 50),
            lease_for: OUTBOX_LEASE,
            retry_base,
            retry_max,
            concurrency: concurrency.max(1),
            consumer_id,
        }
    }

    pub async fn run_once(&self) -> Result<ProbeWorkerOutcome, ApplicationError> {
        let deliveries = self
            .outbox
            .claim(OutboxClaimRequest {
                consumer: self.consumer_id.clone(),
                event_types: vec![PROBE_EVENT_TYPE.to_owned()],
                batch_size: self.claim_batch,
                lease_for: self.lease_for,
            })
            .await
            .map_err(ApplicationError::from)?;
        let claimed = deliveries.len();
        let outcomes = stream::iter(deliveries)
            .map(|delivery| self.process_delivery(delivery))
            .buffer_unordered(self.concurrency)
            .collect::<Vec<_>>()
            .await;
        let mut outcome = ProbeWorkerOutcome {
            claimed,
            acknowledged: 0,
            retried: 0,
            applied: 0,
            duplicates: 0,
            lease_lost: 0,
            terminal_without_effect: 0,
        };
        for result in outcomes {
            let delivery = result?;
            outcome.acknowledged += if delivery.acknowledged { 1 } else { 0 };
            outcome.retried += if delivery.retried { 1 } else { 0 };
            outcome.applied += if delivery.applied { 1 } else { 0 };
            outcome.duplicates += if delivery.duplicate { 1 } else { 0 };
            outcome.lease_lost += if delivery.lease_lost { 1 } else { 0 };
            outcome.terminal_without_effect += if delivery.terminal { 1 } else { 0 };
        }
        Ok(outcome)
    }

    async fn process_delivery(
        &self,
        delivery: crate::OutboxDelivery,
    ) -> Result<DeliveryOutcome, ApplicationError> {
        let Some(probe_id) = extract_probe_id(&delivery.event.payload) else {
            warn!(event_id = %delivery.event.event_id, "probe event payload is invalid; scheduling retry");
            self.outbox
                .retry(
                    delivery.event.event_id,
                    &delivery.claim_token,
                    &self.consumer_id,
                    OutboxRetry {
                        attempt: delivery.attempt,
                        max_attempts: OUTBOX_MAX_ATTEMPTS,
                        delay: retry_delay(
                            self.retry_base,
                            self.retry_max,
                            delivery.attempt,
                            delivery.event.event_id,
                        ),
                        reason_code: "invalid_probe_payload",
                    },
                )
                .await
                .map_err(ApplicationError::from)?;
            return Ok(DeliveryOutcome::retried());
        };
        let outcome = match self
            .persistence
            .apply_probe_effect_and_ack(
                delivery.event.event_id,
                &delivery.claim_token,
                &self.consumer_id,
            )
            .await
        {
            Ok(outcome) => outcome,
            Err(PersistenceError::Conflict) => {
                warn!(event_id = %delivery.event.event_id, "platform probe lease was lost before terminal commit");
                return Ok(DeliveryOutcome::lease_lost());
            }
            Err(error) => {
                retry_delivery(
                    &*self.outbox,
                    delivery.event.event_id,
                    &delivery.claim_token,
                    self.retry_base,
                    self.retry_max,
                    delivery.attempt,
                    &self.consumer_id,
                    error,
                )
                .await?;
                return Ok(DeliveryOutcome::retried());
            }
        };
        info!(event_id = %delivery.event.event_id, probe_id = %probe_id, "platform probe terminal transaction completed");
        Ok(match outcome {
            ProbeEffectAckOutcome::AppliedAndAcknowledged => DeliveryOutcome::applied(),
            ProbeEffectAckOutcome::AlreadyAcknowledged => DeliveryOutcome::duplicate(),
            ProbeEffectAckOutcome::LeaseLost => DeliveryOutcome::lease_lost(),
            ProbeEffectAckOutcome::TerminalWithoutEffect => DeliveryOutcome::terminal(),
        })
    }
}

#[derive(Debug, Clone, Copy)]
struct DeliveryOutcome {
    acknowledged: bool,
    retried: bool,
    applied: bool,
    duplicate: bool,
    lease_lost: bool,
    terminal: bool,
}

impl DeliveryOutcome {
    const fn retried() -> Self {
        Self {
            acknowledged: false,
            retried: true,
            applied: false,
            duplicate: false,
            lease_lost: false,
            terminal: false,
        }
    }

    const fn applied() -> Self {
        Self {
            acknowledged: true,
            retried: false,
            applied: true,
            duplicate: false,
            lease_lost: false,
            terminal: false,
        }
    }

    const fn duplicate() -> Self {
        Self {
            acknowledged: true,
            retried: false,
            applied: false,
            duplicate: true,
            lease_lost: false,
            terminal: false,
        }
    }

    const fn lease_lost() -> Self {
        Self {
            acknowledged: false,
            retried: false,
            applied: false,
            duplicate: false,
            lease_lost: true,
            terminal: false,
        }
    }

    const fn terminal() -> Self {
        Self {
            acknowledged: false,
            retried: false,
            applied: false,
            duplicate: false,
            lease_lost: false,
            terminal: true,
        }
    }
}

fn extract_probe_id(payload: &Value) -> Option<Uuid> {
    payload
        .get("probeId")
        .and_then(Value::as_str)
        .and_then(|value| Uuid::parse_str(value).ok())
}

async fn retry_delivery(
    outbox: &dyn DurableOutboxConsumer,
    event_id: Uuid,
    claim: &crate::OutboxClaimToken,
    retry_base: Duration,
    retry_max: Duration,
    attempt: u32,
    consumer_id: &str,
    _error: PersistenceError,
) -> Result<(), ApplicationError> {
    outbox
        .retry(
            event_id,
            claim,
            consumer_id,
            OutboxRetry {
                attempt,
                max_attempts: OUTBOX_MAX_ATTEMPTS,
                delay: retry_delay(retry_base, retry_max, attempt, event_id),
                reason_code: "probe_effect_failed",
            },
        )
        .await
        .map_err(ApplicationError::from)
}

fn retry_delay(base: Duration, maximum: Duration, attempt: u32, event_id: Uuid) -> Duration {
    let exponent = attempt.saturating_sub(1).min(16);
    let multiplier = 1_u32.checked_shl(exponent).unwrap_or(u32::MAX);
    let exponential = base.saturating_mul(multiplier);
    let capped = exponential.min(maximum);
    let jitter_window_ms = (capped.as_millis() / 5).max(1);
    let bytes = event_id.as_bytes();
    let seed = u16::from_be_bytes([bytes[0], bytes[1]]);
    let jitter_ms = u128::from(seed) % jitter_window_ms;
    let jitter = Duration::from_millis(u64::try_from(jitter_ms).unwrap_or(u64::MAX));
    capped.saturating_sub(jitter)
}
