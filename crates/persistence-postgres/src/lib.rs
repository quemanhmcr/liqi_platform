#![forbid(unsafe_code)]

use async_trait::async_trait;
use liqi_application::{
    CommittedProbe, CommittedRealtimeReader, DurableOutboxConsumer, OutboxClaimRequest,
    OutboxClaimToken, OutboxDelivery, OutboxRetry, PersistenceError, PersistenceReadiness,
    PlatformPersistence, ProbeCommit, ProbeEffectAckOutcome, RealtimeCursor, RealtimeDelivery,
    RealtimeEventBatch, RealtimeReadRequest,
};
use liqi_configuration::RuntimeConfig;
use liqi_protocol::{EventEnvelope, EventMetadata};
use secrecy::{ExposeSecret as _, SecretString};
use serde_json::Value;
use sqlx::{
    PgPool, Row as _,
    postgres::{PgConnectOptions, PgPoolOptions, PgRow, PgSslMode},
};
use std::time::Duration;
use thiserror::Error;
use time::OffsetDateTime;
use uuid::Uuid;

const PLATFORM_PROBE_EVENT: &str = "platform.probe.requested.v0";
const PLATFORM_PROBE_PRODUCER: &str = "liqi-api";
const POOL_IDLE_TIMEOUT: Duration = Duration::from_secs(300);
const POOL_MAX_LIFETIME: Duration = Duration::from_secs(1800);

#[derive(Clone)]
pub struct PostgresAuthorityStore {
    pool: PgPool,
    required_migration_version: i64,
}

impl std::fmt::Debug for PostgresAuthorityStore {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("PostgresAuthorityStore")
            .field(
                "required_migration_version",
                &self.required_migration_version,
            )
            .finish_non_exhaustive()
    }
}

impl PostgresAuthorityStore {
    pub fn connect_lazy(
        config: &RuntimeConfig,
        database_secret: SecretString,
    ) -> Result<Self, PostgresAdapterError> {
        let database = &config.database;
        let options = PgConnectOptions::new()
            .host(&database.endpoint.host)
            .port(database.endpoint.port)
            .database(&database.database_name)
            .username(database.role.as_str())
            .password(database_secret.expose_secret())
            .application_name(config.service.name.artifact_name())
            .ssl_mode(PgSslMode::Disable)
            .statement_cache_capacity(0);
        let pool = PgPoolOptions::new()
            .max_connections(database.max_connections)
            .min_connections(database.min_connections)
            .acquire_timeout(Duration::from_millis(database.acquire_timeout_ms))
            .idle_timeout(Some(POOL_IDLE_TIMEOUT))
            .max_lifetime(Some(POOL_MAX_LIFETIME))
            .test_before_acquire(true)
            .connect_lazy_with(options);
        let required_migration_version = i64::try_from(database.required_migration_version)
            .map_err(|_| PostgresAdapterError::MigrationVersionOutOfRange)?;
        Ok(Self {
            pool,
            required_migration_version,
        })
    }

    #[must_use]
    pub fn pool(&self) -> &PgPool {
        &self.pool
    }

    async fn query_status(
        &self,
        sql: &'static str,
        event_id: Uuid,
        claim: &OutboxClaimToken,
        consumer_id: &str,
    ) -> Result<String, PersistenceError> {
        let claim_id =
            Uuid::parse_str(claim.as_opaque()).map_err(|_| PersistenceError::InvalidOperation)?;
        let row = sqlx::query(sql)
            .bind(event_id)
            .bind(claim_id)
            .bind(consumer_id)
            .persistent(false)
            .fetch_one(&self.pool)
            .await
            .map_err(map_sqlx)?;
        row.try_get::<String, _>("status").map_err(map_sqlx)
    }
}

#[async_trait]
impl PlatformPersistence for PostgresAuthorityStore {
    async fn readiness(&self) -> Result<PersistenceReadiness, PersistenceError> {
        let row = sqlx::query(
            "SELECT ready, current_version, expected_version \
             FROM platform.database_readiness_v0($1)",
        )
        .bind(self.required_migration_version)
        .persistent(false)
        .fetch_one(&self.pool)
        .await
        .map_err(map_sqlx)?;
        let ready = row.try_get::<bool, _>("ready").map_err(map_sqlx)?;
        let current = row.try_get::<i64, _>("current_version").map_err(map_sqlx)?;
        let expected = row
            .try_get::<i64, _>("expected_version")
            .map_err(map_sqlx)?;
        Ok(PersistenceReadiness {
            database_reachable: true,
            migration_ready: ready && current >= expected,
        })
    }

    async fn close(&self) {
        self.pool.close().await;
    }

    async fn commit_probe(&self, commit: ProbeCommit) -> Result<CommittedProbe, PersistenceError> {
        let mut metadata = EventMetadata::new();
        if let Some(trace_id) = &commit.request_context.trace_id {
            metadata.insert("trace_id".to_owned(), Value::String(trace_id.clone()));
        }
        let metadata = serde_json::to_value(metadata).map_err(|_| PersistenceError::Internal)?;
        let row =
            sqlx::query("SELECT platform.request_probe_v0($1, $2, $3, $4, $5, $6) AS event_id")
                .bind(commit.probe_id)
                .bind(commit.event_id)
                .bind(commit.committed_at)
                .bind(commit.request_context.request_id)
                .bind(Option::<Uuid>::None)
                .bind(metadata)
                .persistent(false)
                .fetch_one(&self.pool)
                .await
                .map_err(map_sqlx)?;
        let committed_event_id = row.try_get::<Uuid, _>("event_id").map_err(map_sqlx)?;
        if committed_event_id != commit.event_id {
            return Err(PersistenceError::Internal);
        }
        Ok(CommittedProbe {
            probe_id: commit.probe_id,
            event_id: committed_event_id,
            committed_at: commit.committed_at,
        })
    }

    async fn apply_probe_effect_and_ack(
        &self,
        event_id: Uuid,
        claim: &OutboxClaimToken,
        consumer_id: &str,
    ) -> Result<ProbeEffectAckOutcome, PersistenceError> {
        let status = self
            .query_status(
                "SELECT platform.apply_probe_effect_and_ack_v0($1, $2, $3) AS status",
                event_id,
                claim,
                consumer_id,
            )
            .await?;
        match status.as_str() {
            "acked" => Ok(ProbeEffectAckOutcome::AppliedAndAcknowledged),
            "already_succeeded" => Ok(ProbeEffectAckOutcome::AlreadyAcknowledged),
            "stale_claim" => Ok(ProbeEffectAckOutcome::LeaseLost),
            "already_dead_lettered" => Ok(ProbeEffectAckOutcome::TerminalWithoutEffect),
            "not_found" | "unsupported_event" => Err(PersistenceError::InvalidOperation),
            _ => Err(PersistenceError::Internal),
        }
    }
}

#[async_trait]
impl DurableOutboxConsumer for PostgresAuthorityStore {
    async fn claim(
        &self,
        request: OutboxClaimRequest,
    ) -> Result<Vec<OutboxDelivery>, PersistenceError> {
        request.validate()?;
        if request.event_types.as_slice() != [PLATFORM_PROBE_EVENT] {
            return Err(PersistenceError::InvalidOperation);
        }
        let batch_size =
            i32::try_from(request.batch_size).map_err(|_| PersistenceError::InvalidOperation)?;
        let lease_seconds = i32::try_from(request.lease_for.as_secs())
            .map_err(|_| PersistenceError::InvalidOperation)?;
        let rows = sqlx::query(
            "SELECT event_id, claim_token, attempt_no, schema_version, event_type, \
                    event_version, occurred_at, producer, correlation_id, causation_id, \
                    aggregate_key, ordering_key, payload, metadata \
             FROM platform.claim_outbox_v0($1, $2, $3)",
        )
        .bind(&request.consumer)
        .bind(batch_size)
        .bind(lease_seconds)
        .persistent(false)
        .fetch_all(&self.pool)
        .await
        .map_err(map_sqlx)?;
        rows.into_iter().map(map_claimed_row).collect()
    }

    async fn acknowledge(
        &self,
        event_id: Uuid,
        claim: &OutboxClaimToken,
        consumer_id: &str,
    ) -> Result<(), PersistenceError> {
        let status = self
            .query_status(
                "SELECT platform.ack_outbox_v0($1, $2, $3) AS status",
                event_id,
                claim,
                consumer_id,
            )
            .await?;
        match status.as_str() {
            "acked" | "already_succeeded" => Ok(()),
            "stale_claim" => Err(PersistenceError::Conflict),
            "already_dead_lettered" | "not_found" => Err(PersistenceError::InvalidOperation),
            _ => Err(PersistenceError::Internal),
        }
    }

    async fn retry(
        &self,
        event_id: Uuid,
        claim: &OutboxClaimToken,
        consumer_id: &str,
        retry: OutboxRetry,
    ) -> Result<(), PersistenceError> {
        retry.validate()?;
        let claim_id =
            Uuid::parse_str(claim.as_opaque()).map_err(|_| PersistenceError::InvalidOperation)?;
        let retry_at = OffsetDateTime::now_utc() + retry.delay;
        let row = sqlx::query("SELECT platform.fail_outbox_v0($1, $2, $3, $4, $5) AS status")
            .bind(event_id)
            .bind(claim_id)
            .bind(consumer_id)
            .bind(retry.reason_code)
            .bind(retry_at)
            .persistent(false)
            .fetch_one(&self.pool)
            .await
            .map_err(map_sqlx)?;
        let status = row.try_get::<String, _>("status").map_err(map_sqlx)?;
        match status.as_str() {
            "retry_scheduled" | "dead_lettered" | "already_dead_lettered" | "already_succeeded" => {
                Ok(())
            }
            "stale_claim" => Err(PersistenceError::Conflict),
            "not_found" => Err(PersistenceError::InvalidOperation),
            _ => Err(PersistenceError::Internal),
        }
    }
}

#[async_trait]
impl CommittedRealtimeReader for PostgresAuthorityStore {
    async fn read_committed(
        &self,
        request: RealtimeReadRequest,
    ) -> Result<RealtimeEventBatch, PersistenceError> {
        request.validate()?;
        if request.topics.len() != 1 || request.topics[0] != PLATFORM_PROBE_EVENT {
            return Err(PersistenceError::InvalidOperation);
        }
        let after_handoff_id = request
            .after
            .as_ref()
            .map(|cursor| {
                cursor
                    .as_opaque()
                    .parse::<i64>()
                    .map_err(|_| PersistenceError::InvalidOperation)
            })
            .transpose()?
            .unwrap_or(0);
        if after_handoff_id < 0 {
            return Err(PersistenceError::InvalidOperation);
        }
        let batch_size =
            i32::try_from(request.batch_size).map_err(|_| PersistenceError::InvalidOperation)?;
        let rows = sqlx::query(
            "SELECT handoff_id, event_id, schema_version, event_type, event_version, \
                    occurred_at, producer, correlation_id, causation_id, aggregate_key, \
                    ordering_key, payload, metadata \
             FROM platform.read_realtime_handoff_v0($1, $2)",
        )
        .bind(after_handoff_id)
        .bind(batch_size)
        .persistent(false)
        .fetch_all(&self.pool)
        .await
        .map_err(map_sqlx)?;
        let deliveries = rows
            .into_iter()
            .map(map_realtime_row)
            .collect::<Result<Vec<_>, _>>()?;
        let next_cursor = deliveries.last().map(|delivery| delivery.cursor.clone());
        Ok(RealtimeEventBatch {
            deliveries,
            next_cursor,
        })
    }
}

fn map_claimed_row(row: PgRow) -> Result<OutboxDelivery, PersistenceError> {
    let claim_token = row.try_get::<Uuid, _>("claim_token").map_err(map_sqlx)?;
    let attempt = row.try_get::<i16, _>("attempt_no").map_err(map_sqlx)?;
    let event = map_event_row(&row)?;
    Ok(OutboxDelivery {
        claim_token: OutboxClaimToken::from_opaque(claim_token.to_string())?,
        attempt: u32::try_from(attempt).map_err(|_| PersistenceError::InvalidOperation)?,
        event,
    })
}

fn map_realtime_row(row: PgRow) -> Result<RealtimeDelivery, PersistenceError> {
    let handoff_id = row.try_get::<i64, _>("handoff_id").map_err(map_sqlx)?;
    if handoff_id <= 0 {
        return Err(PersistenceError::InvalidOperation);
    }
    Ok(RealtimeDelivery {
        cursor: RealtimeCursor::from_opaque(handoff_id.to_string())?,
        event: map_event_row(&row)?,
    })
}

fn map_event_row(row: &PgRow) -> Result<EventEnvelope<Value>, PersistenceError> {
    let schema_version = row.try_get::<i16, _>("schema_version").map_err(map_sqlx)?;
    if schema_version != 0 {
        return Err(PersistenceError::InvalidOperation);
    }
    let event_type = row.try_get::<String, _>("event_type").map_err(map_sqlx)?;
    if event_type != PLATFORM_PROBE_EVENT {
        return Err(PersistenceError::InvalidOperation);
    }
    let producer = row.try_get::<String, _>("producer").map_err(map_sqlx)?;
    if producer != PLATFORM_PROBE_PRODUCER {
        return Err(PersistenceError::InvalidOperation);
    }
    let metadata_value = row.try_get::<Value, _>("metadata").map_err(map_sqlx)?;
    let metadata = serde_json::from_value::<EventMetadata>(metadata_value)
        .map_err(|_| PersistenceError::InvalidOperation)?;
    let event_version = row.try_get::<i32, _>("event_version").map_err(map_sqlx)?;
    let envelope = EventEnvelope {
        event_id: row.try_get::<Uuid, _>("event_id").map_err(map_sqlx)?,
        event_type,
        event_version: u32::try_from(event_version)
            .map_err(|_| PersistenceError::InvalidOperation)?,
        occurred_at: row
            .try_get::<OffsetDateTime, _>("occurred_at")
            .map_err(map_sqlx)?,
        producer,
        correlation_id: row
            .try_get::<Option<Uuid>, _>("correlation_id")
            .map_err(map_sqlx)?,
        causation_id: row
            .try_get::<Option<Uuid>, _>("causation_id")
            .map_err(map_sqlx)?,
        aggregate_key: row
            .try_get::<String, _>("aggregate_key")
            .map_err(map_sqlx)?,
        ordering_key: Some(row.try_get::<String, _>("ordering_key").map_err(map_sqlx)?),
        payload: row.try_get::<Value, _>("payload").map_err(map_sqlx)?,
        metadata,
    };
    envelope
        .validate(4096)
        .map_err(|_| PersistenceError::InvalidOperation)?;
    Ok(envelope)
}

fn map_sqlx(error: sqlx::Error) -> PersistenceError {
    match error {
        sqlx::Error::PoolTimedOut => PersistenceError::Timeout,
        sqlx::Error::PoolClosed | sqlx::Error::Io(_) | sqlx::Error::Tls(_) => {
            PersistenceError::NotReady
        }
        sqlx::Error::Database(database) => match database.code().as_deref() {
            Some("23505") => PersistenceError::Conflict,
            Some("22023") => PersistenceError::InvalidOperation,
            _ => PersistenceError::Internal,
        },
        _ => PersistenceError::Internal,
    }
}

#[derive(Debug, Error)]
pub enum PostgresAdapterError {
    #[error("required migration version does not fit PostgreSQL bigint")]
    MigrationVersionOutOfRange,
}
