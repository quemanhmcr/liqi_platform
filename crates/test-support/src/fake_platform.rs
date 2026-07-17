use async_trait::async_trait;
use liqi_application::{
    CommittedProbe, CommittedRealtimeReader, DurableOutboxConsumer, OutboxClaimRequest,
    OutboxClaimToken, OutboxDelivery, OutboxRetry, PersistenceError, PersistenceReadiness,
    PlatformPersistence, ProbeCommit, ProbeEffectAckOutcome, RealtimeCursor, RealtimeEventBatch,
    RealtimeReadRequest,
};
use liqi_protocol::{EventEnvelope, EventMetadata};
use serde_json::{Value, json};
use std::{
    collections::{HashMap, HashSet},
    sync::Arc,
};
use time::OffsetDateTime;
use tokio::sync::Mutex;
use uuid::Uuid;

#[derive(Debug, Clone, Default)]
pub struct FakePlatformStore {
    state: Arc<Mutex<State>>,
}

#[derive(Debug, Default)]
struct State {
    ready: bool,
    probes_by_client_id: HashMap<Uuid, StoredProbe>,
    events: Vec<EventEnvelope<Value>>,
    acknowledged: HashSet<(String, Uuid)>,
    active_claim_by_delivery: HashMap<(String, Uuid), Uuid>,
    delivery_by_claim: HashMap<Uuid, (String, Uuid)>,
    terminal_effects: HashSet<Uuid>,
    retry_counts: HashMap<(String, Uuid), u32>,
    dead_letter: HashSet<(String, Uuid)>,
}

#[derive(Debug, Clone)]
struct StoredProbe {
    probe_id: Uuid,
    event_id: Uuid,
    committed_at: OffsetDateTime,
}

impl FakePlatformStore {
    #[must_use]
    pub fn ready() -> Self {
        Self {
            state: Arc::new(Mutex::new(State {
                ready: true,
                ..State::default()
            })),
        }
    }

    pub async fn set_ready(&self, ready: bool) {
        self.state.lock().await.ready = ready;
    }

    pub async fn terminal_effect_count(&self) -> usize {
        self.state.lock().await.terminal_effects.len()
    }

    pub async fn committed_event_count(&self) -> usize {
        self.state.lock().await.events.len()
    }
}

#[async_trait]
impl PlatformPersistence for FakePlatformStore {
    async fn readiness(&self) -> Result<PersistenceReadiness, PersistenceError> {
        let ready = self.state.lock().await.ready;
        Ok(PersistenceReadiness {
            database_reachable: ready,
            migration_ready: ready,
        })
    }

    async fn commit_probe(&self, commit: ProbeCommit) -> Result<CommittedProbe, PersistenceError> {
        let mut state = self.state.lock().await;
        if !state.ready {
            return Err(PersistenceError::NotReady);
        }
        if let Some(existing) = state
            .probes_by_client_id
            .get(&commit.request.client_probe_id)
        {
            return Ok(CommittedProbe {
                probe_id: existing.probe_id,
                event_id: existing.event_id,
                committed_at: existing.committed_at,
            });
        }
        let payload = json!({
            "probeId": commit.probe_id,
        });
        let mut metadata = EventMetadata::new();
        if let Some(trace_id) = &commit.request_context.trace_id {
            metadata.insert("trace_id".to_owned(), Value::String(trace_id.clone()));
        }
        let aggregate_key = format!("platform-probe:{}", commit.probe_id);
        let event = EventEnvelope {
            event_id: commit.event_id,
            event_type: "platform.probe.requested.v0".to_owned(),
            event_version: 0,
            occurred_at: commit.committed_at,
            producer: "liqi-api".to_owned(),
            correlation_id: Some(commit.request_context.request_id),
            causation_id: None,
            aggregate_key: aggregate_key.clone(),
            ordering_key: Some(aggregate_key),
            payload,
            metadata,
        };
        event
            .validate(4096)
            .map_err(|_| PersistenceError::InvalidOperation)?;
        state.events.push(event);
        state.probes_by_client_id.insert(
            commit.request.client_probe_id,
            StoredProbe {
                probe_id: commit.probe_id,
                event_id: commit.event_id,
                committed_at: commit.committed_at,
            },
        );
        Ok(CommittedProbe {
            probe_id: commit.probe_id,
            event_id: commit.event_id,
            committed_at: commit.committed_at,
        })
    }

    async fn apply_probe_effect_and_ack(
        &self,
        event_id: Uuid,
        claim: &OutboxClaimToken,
        consumer_id: &str,
    ) -> Result<ProbeEffectAckOutcome, PersistenceError> {
        let claim_id = parse_claim_id(claim)?;
        let mut state = self.state.lock().await;
        if !state.ready {
            return Err(PersistenceError::NotReady);
        }
        let key = (consumer_id.to_owned(), event_id);
        if state.acknowledged.contains(&key) {
            return Ok(ProbeEffectAckOutcome::AlreadyAcknowledged);
        }
        if state.dead_letter.contains(&key) {
            return Ok(ProbeEffectAckOutcome::TerminalWithoutEffect);
        }
        if state.active_claim_by_delivery.get(&key) != Some(&claim_id)
            || state.delivery_by_claim.get(&claim_id) != Some(&key)
        {
            return Ok(ProbeEffectAckOutcome::LeaseLost);
        }
        state.active_claim_by_delivery.remove(&key);
        state.delivery_by_claim.remove(&claim_id);
        let applied = state.terminal_effects.insert(event_id);
        state.acknowledged.insert(key);
        Ok(if applied {
            ProbeEffectAckOutcome::AppliedAndAcknowledged
        } else {
            ProbeEffectAckOutcome::AlreadyAcknowledged
        })
    }
}

#[async_trait]
impl DurableOutboxConsumer for FakePlatformStore {
    async fn claim(
        &self,
        request: OutboxClaimRequest,
    ) -> Result<Vec<OutboxDelivery>, PersistenceError> {
        request.validate()?;
        let mut state = self.state.lock().await;
        if !state.ready {
            return Err(PersistenceError::NotReady);
        }
        let consumer = request.consumer;
        let event_types: HashSet<_> = request.event_types.into_iter().collect();
        let candidates: Vec<_> = state
            .events
            .iter()
            .filter(|event| event_types.contains(&event.event_type))
            .map(|event| (event.event_id, event.clone()))
            .collect();
        let mut deliveries = Vec::with_capacity(request.batch_size);
        for (event_id, event) in candidates {
            if deliveries.len() >= request.batch_size {
                break;
            }
            let key = (consumer.clone(), event_id);
            if state.acknowledged.contains(&key)
                || state.dead_letter.contains(&key)
                || state.active_claim_by_delivery.contains_key(&key)
            {
                continue;
            }
            let claim_id = Uuid::now_v7();
            state.active_claim_by_delivery.insert(key.clone(), claim_id);
            state.delivery_by_claim.insert(claim_id, key.clone());
            let token = OutboxClaimToken::from_opaque(claim_id.to_string())?;
            let attempt = state
                .retry_counts
                .get(&key)
                .copied()
                .unwrap_or(0)
                .saturating_add(1);
            deliveries.push(OutboxDelivery {
                claim_token: token,
                attempt,
                event,
            });
        }
        Ok(deliveries)
    }

    async fn acknowledge(
        &self,
        event_id: Uuid,
        claim: &OutboxClaimToken,
        consumer_id: &str,
    ) -> Result<(), PersistenceError> {
        let claim_id = parse_claim_id(claim)?;
        let mut state = self.state.lock().await;
        let key = state
            .delivery_by_claim
            .remove(&claim_id)
            .ok_or(PersistenceError::Conflict)?;
        if key.0 != consumer_id
            || key.1 != event_id
            || state.active_claim_by_delivery.get(&key) != Some(&claim_id)
        {
            return Err(PersistenceError::Conflict);
        }
        state.active_claim_by_delivery.remove(&key);
        state.acknowledged.insert(key);
        Ok(())
    }

    async fn retry(
        &self,
        event_id: Uuid,
        claim: &OutboxClaimToken,
        consumer_id: &str,
        retry: OutboxRetry,
    ) -> Result<(), PersistenceError> {
        retry.validate()?;
        let claim_id = parse_claim_id(claim)?;
        let mut state = self.state.lock().await;
        let key = state
            .delivery_by_claim
            .remove(&claim_id)
            .ok_or(PersistenceError::Conflict)?;
        if key.0 != consumer_id
            || key.1 != event_id
            || state.active_claim_by_delivery.get(&key) != Some(&claim_id)
        {
            return Err(PersistenceError::Conflict);
        }
        state.active_claim_by_delivery.remove(&key);
        state.retry_counts.insert(key.clone(), retry.attempt);
        if retry.attempt >= retry.max_attempts {
            state.dead_letter.insert(key);
        }
        Ok(())
    }
}

#[async_trait]
impl CommittedRealtimeReader for FakePlatformStore {
    async fn read_committed(
        &self,
        request: RealtimeReadRequest,
    ) -> Result<RealtimeEventBatch, PersistenceError> {
        request.validate()?;
        let state = self.state.lock().await;
        if !state.ready {
            return Err(PersistenceError::NotReady);
        }
        let start = request
            .after
            .as_ref()
            .map_or(Ok(0), |cursor| parse_cursor(cursor))?;
        let topics: HashSet<_> = request.topics.into_iter().collect();
        let mut deliveries = Vec::with_capacity(request.batch_size);
        let mut next_cursor = request.after;
        for (index, event) in state.events.iter().enumerate().skip(start) {
            let cursor = RealtimeCursor::from_opaque(format!("fake:{}", index.saturating_add(1)))?;
            next_cursor = Some(cursor.clone());
            if topics.contains(&event.event_type) {
                deliveries.push(RealtimeDelivery {
                    cursor,
                    event: event.clone(),
                });
                if deliveries.len() >= request.batch_size {
                    break;
                }
            }
        }
        Ok(RealtimeEventBatch {
            deliveries,
            next_cursor,
        })
    }
}

fn parse_claim_id(claim: &OutboxClaimToken) -> Result<Uuid, PersistenceError> {
    Uuid::parse_str(claim.as_opaque()).map_err(|_| PersistenceError::InvalidOperation)
}

fn parse_cursor(cursor: &RealtimeCursor) -> Result<usize, PersistenceError> {
    let value = cursor
        .as_opaque()
        .strip_prefix("fake:")
        .ok_or(PersistenceError::InvalidOperation)?;
    value
        .parse::<usize>()
        .map_err(|_| PersistenceError::InvalidOperation)
}
