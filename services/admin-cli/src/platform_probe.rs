use ::time::{self as time_crate, OffsetDateTime};
use futures_util::{SinkExt as _, StreamExt as _};
use liqi_protocol::{
    ArtifactMetadata, AuthenticatePayload, ClientFrame, ClientFrameBody, CreateProbeRequest,
    CreateProbeResponse, HealthResponse, HealthStatus, HeartbeatAckPayload, HelloPayload,
    ServerFrame, ServerFrameBody, SubscribePayload,
};
use reqwest::{Client, StatusCode, Url, redirect::Policy};
use secrecy::{ExposeSecret as _, SecretString};
use serde::Serialize;
use sqlx::{Row as _, postgres::PgPoolOptions};
use std::{
    env, fs, io,
    path::{Path, PathBuf},
    time::{Duration, Instant},
};
use thiserror::Error;
use tokio::{net::TcpStream, time as tokio_time};
use tokio_tungstenite::{
    MaybeTlsStream, WebSocketStream, connect_async_with_config,
    tungstenite::{Error as WebSocketError, Message, protocol::WebSocketConfig},
};
use uuid::Uuid;

const RESULT_SCHEMA: &str = "platform-probe-result-v0";
const OWNER: &str = "Senior 3";
const PROTOCOL_VERSION: &str = "0";
const PROBE_TOPIC: &str = "platform.probe.requested.v0";
const MAX_HTTP_BODY_BYTES: usize = 65_536;
const MAX_RESULT_ERRORS: usize = 16;
const MAX_SAFE_MESSAGE_CHARS: usize = 500;
const UNOBSERVED_RELEASE_ID: &str = "liqi-unobserved";
const REQUIRED_CHECK_NAMES: [&str; 8] = [
    "api-liveness",
    "api-readiness",
    "realtime-liveness",
    "realtime-readiness",
    "worker-readiness",
    "durable-command",
    "outbox-terminal",
    "realtime-delivery",
];

#[derive(Debug, Clone)]
pub struct PlatformProbeOptions {
    pub output: PathBuf,
    pub api_base_url: String,
    pub realtime_base_url: String,
    pub realtime_ws_url: String,
    pub worker_base_url: String,
    pub timeout: Duration,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PlatformProbeExit {
    Passed,
    Failed,
}

pub async fn run(options: PlatformProbeOptions) -> Result<PlatformProbeExit, PlatformProbeError> {
    let settings = ProbeSettings::from_environment()?;
    let started_at = OffsetDateTime::now_utc();
    let client = Client::builder()
        .redirect(Policy::none())
        .timeout(options.timeout)
        .user_agent("liqi-platform-probe-v0")
        .build()
        .map_err(|_| PlatformProbeError::HttpClient)?;
    let mut recorder = ProbeRecorder::new(settings.release_id.clone(), settings.environment);

    let api_live_started = Instant::now();
    let api_live = verify_service(
        &client,
        &options.api_base_url,
        "/health/live",
        "liqi-api",
        &settings,
        true,
    )
    .await;
    if api_live.is_ok() {
        recorder.observe_release(&settings.release_id);
    }
    recorder.record_elapsed("api-liveness", api_live_started, api_live);

    let api_ready_started = Instant::now();
    let api_ready = verify_service(
        &client,
        &options.api_base_url,
        "/health/ready",
        "liqi-api",
        &settings,
        false,
    )
    .await;
    recorder.record_elapsed("api-readiness", api_ready_started, api_ready);

    let realtime_live_started = Instant::now();
    let realtime_live = verify_service(
        &client,
        &options.realtime_base_url,
        "/health/live",
        "liqi-realtime",
        &settings,
        true,
    )
    .await;
    if realtime_live.is_ok() {
        recorder.observe_release(&settings.release_id);
    }
    recorder.record_elapsed("realtime-liveness", realtime_live_started, realtime_live);

    let realtime_ready_started = Instant::now();
    let realtime_ready = verify_service(
        &client,
        &options.realtime_base_url,
        "/health/ready",
        "liqi-realtime",
        &settings,
        false,
    )
    .await;
    recorder.record_elapsed("realtime-readiness", realtime_ready_started, realtime_ready);

    let worker_ready_started = Instant::now();
    let worker_ready = verify_service(
        &client,
        &options.worker_base_url,
        "/health/ready",
        "liqi-worker",
        &settings,
        true,
    )
    .await;
    if worker_ready.is_ok() {
        recorder.observe_release(&settings.release_id);
    }
    recorder.record_elapsed("worker-readiness", worker_ready_started, worker_ready);

    let subscription_id = Uuid::now_v7();
    let realtime_started = Instant::now();
    let realtime = RealtimeProbeConnection::connect(
        &options.realtime_ws_url,
        subscription_id,
        options.timeout,
        settings.environment,
    )
    .await;
    let durable_started = Instant::now();
    let probe_id = Uuid::now_v7();
    let durable = commit_probe(&client, &options.api_base_url, probe_id).await;
    let committed = match durable {
        Ok(response) => {
            recorder.record_elapsed(
                "durable-command",
                durable_started,
                Ok(format!(
                    "evidence://platform-probe/{}/durable-command",
                    response.probe_id
                )),
            );
            Some(response)
        }
        Err(error) => {
            recorder.record_elapsed("durable-command", durable_started, Err(error));
            None
        }
    };

    let terminal_started = Instant::now();
    let terminal = match &committed {
        Some(response) => {
            observe_terminal_effect(
                &settings.database_url,
                response.probe_id,
                response.event_id,
                options.timeout,
            )
            .await
        }
        None => Err(StepFailure::new(
            "probe.command_unavailable",
            "Durable command did not complete, so terminal effect could not be observed.",
        )),
    };
    recorder.record_elapsed("outbox-terminal", terminal_started, terminal);

    let delivery = match (realtime, &committed) {
        (Ok(mut connection), Some(response)) => connection
            .wait_for_event(subscription_id, response.event_id, options.timeout)
            .await
            .map(|()| {
                format!(
                    "evidence://platform-probe/{}/realtime-delivery",
                    response.probe_id
                )
            }),
        (Err(error), _) => Err(error),
        (_, None) => Err(StepFailure::new(
            "probe.command_unavailable",
            "Realtime delivery could not be checked without a committed probe event.",
        )),
    };
    recorder.record_elapsed("realtime-delivery", realtime_started, delivery);

    let completed_at = OffsetDateTime::now_utc();
    let result = recorder.finish(started_at, completed_at);
    write_result_atomic(&options.output, &result)?;
    Ok(if result.status == ProbeRunStatus::Passed {
        PlatformProbeExit::Passed
    } else {
        PlatformProbeExit::Failed
    })
}

#[derive(Clone)]
struct ProbeSettings {
    release_id: String,
    environment: ProbeEnvironment,
    database_url: SecretString,
}

impl ProbeSettings {
    fn from_environment() -> Result<Self, PlatformProbeError> {
        let release_id = env::var("LIQI_RELEASE_ID")
            .map_err(|_| PlatformProbeError::MissingEnvironment("LIQI_RELEASE_ID"))?;
        if !valid_release_id(&release_id) {
            return Err(PlatformProbeError::InvalidReleaseId);
        }
        let environment = env::var("LIQI_ENVIRONMENT")
            .map_err(|_| PlatformProbeError::MissingEnvironment("LIQI_ENVIRONMENT"))?
            .parse()?;
        let database_url = env::var("LIQI_TEST_DATABASE")
            .map_err(|_| PlatformProbeError::MissingEnvironment("LIQI_TEST_DATABASE"))?;
        if database_url.trim().is_empty() || database_url.len() > 4_096 {
            return Err(PlatformProbeError::InvalidDatabaseReference);
        }
        Ok(Self {
            release_id,
            environment,
            database_url: SecretString::from(database_url),
        })
    }
}

fn valid_release_id(value: &str) -> bool {
    let bytes = value.as_bytes();
    if !(8..=64).contains(&bytes.len()) || !value.starts_with("liqi-") {
        return false;
    }
    (bytes[5].is_ascii_lowercase() || bytes[5].is_ascii_digit())
        && bytes[5..].iter().all(|byte| {
            byte.is_ascii_lowercase()
                || byte.is_ascii_digit()
                || matches!(*byte, b'.' | b'_' | b'-')
        })
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
enum ProbeEnvironment {
    Development,
    Staging,
    Production,
}

impl ProbeEnvironment {
    const fn as_str(self) -> &'static str {
        match self {
            Self::Development => "development",
            Self::Staging => "staging",
            Self::Production => "production",
        }
    }
}

impl std::str::FromStr for ProbeEnvironment {
    type Err = PlatformProbeError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value {
            "development" => Ok(Self::Development),
            "staging" => Ok(Self::Staging),
            "production" => Ok(Self::Production),
            _ => Err(PlatformProbeError::InvalidEnvironment),
        }
    }
}

async fn verify_service(
    client: &Client,
    base_url: &str,
    health_path: &str,
    expected_service: &str,
    settings: &ProbeSettings,
    verify_metadata: bool,
) -> Result<String, StepFailure> {
    let health_url = join_url(base_url, health_path)?;
    let health: HealthResponse = get_json(client, &health_url, StatusCode::OK).await?;
    let expected_health_status = if health_path == "/health/live" {
        HealthStatus::Live
    } else {
        HealthStatus::Ready
    };
    if health.status != expected_health_status
        || health.service != expected_service
        || health.version != settings.release_id
    {
        return Err(StepFailure::new(
            "health.identity_mismatch",
            "Service health did not report the expected ready/live release identity.",
        ));
    }
    if verify_metadata {
        let metadata_url = join_url(base_url, "/platform/v0/metadata")?;
        let metadata: ArtifactMetadata = get_json(client, &metadata_url, StatusCode::OK).await?;
        if metadata.artifact != expected_service
            || metadata.version != settings.release_id
            || metadata.release_id != settings.release_id
            || metadata.environment != settings.environment.as_str()
        {
            return Err(StepFailure::new(
                "metadata.identity_mismatch",
                "Artifact metadata did not report the expected release and environment.",
            ));
        }
    }
    Ok(format!(
        "evidence://runtime/{expected_service}{}",
        health_path.replace('/', "-")
    ))
}

async fn get_json<T: serde::de::DeserializeOwned>(
    client: &Client,
    url: &str,
    expected_status: StatusCode,
) -> Result<T, StepFailure> {
    let response = client.get(url).send().await.map_err(|_| {
        StepFailure::new("http.unavailable", "Runtime HTTP endpoint was unavailable.")
    })?;
    if response.status() != expected_status {
        return Err(StepFailure::new(
            "http.unexpected_status",
            "Runtime HTTP endpoint returned an unexpected status.",
        ));
    }
    if response
        .content_length()
        .is_some_and(|size| size > u64::try_from(MAX_HTTP_BODY_BYTES).unwrap_or(u64::MAX))
    {
        return Err(StepFailure::new(
            "http.body_too_large",
            "Runtime HTTP evidence exceeded the bounded response size.",
        ));
    }
    let bytes = response.bytes().await.map_err(|_| {
        StepFailure::new(
            "http.read_failed",
            "Runtime HTTP evidence could not be read.",
        )
    })?;
    if bytes.len() > MAX_HTTP_BODY_BYTES {
        return Err(StepFailure::new(
            "http.body_too_large",
            "Runtime HTTP evidence exceeded the bounded response size.",
        ));
    }
    serde_json::from_slice(&bytes).map_err(|_| {
        StepFailure::new(
            "http.invalid_json",
            "Runtime HTTP evidence did not match the expected JSON contract.",
        )
    })
}

async fn commit_probe(
    client: &Client,
    api_base_url: &str,
    probe_id: Uuid,
) -> Result<CreateProbeResponse, StepFailure> {
    let url = join_url(api_base_url, "/platform/v0/probes")?;
    let response = client
        .post(url)
        .json(&CreateProbeRequest {
            client_probe_id: probe_id,
        })
        .send()
        .await
        .map_err(|_| StepFailure::new("probe.api_unavailable", "Probe API was unavailable."))?;
    if response.status() != StatusCode::ACCEPTED {
        return Err(StepFailure::new(
            "probe.command_rejected",
            "Probe API did not durably accept the command.",
        ));
    }
    let bytes = response.bytes().await.map_err(|_| {
        StepFailure::new(
            "probe.invalid_response",
            "Probe response could not be read.",
        )
    })?;
    if bytes.len() > MAX_HTTP_BODY_BYTES {
        return Err(StepFailure::new(
            "probe.invalid_response",
            "Probe response exceeded the bounded response size.",
        ));
    }
    let response: CreateProbeResponse = serde_json::from_slice(&bytes).map_err(|_| {
        StepFailure::new(
            "probe.invalid_response",
            "Probe response did not match the V0 contract.",
        )
    })?;
    if response.probe_id != probe_id {
        return Err(StepFailure::new(
            "probe.identity_mismatch",
            "Probe response did not preserve the client probe identity.",
        ));
    }
    Ok(response)
}

async fn observe_terminal_effect(
    database_url: &SecretString,
    probe_id: Uuid,
    event_id: Uuid,
    timeout: Duration,
) -> Result<String, StepFailure> {
    let pool = PgPoolOptions::new()
        .max_connections(1)
        .min_connections(0)
        .acquire_timeout(Duration::from_secs(2))
        .connect(database_url.expose_secret())
        .await
        .map_err(|_| {
            StepFailure::new(
                "database.unavailable",
                "Disposable probe database was unavailable.",
            )
        })?;
    let deadline = Instant::now() + timeout;
    let result = loop {
        let query = sqlx::query(
            "SELECT probe.status AS probe_status, event.state AS outbox_state, \
                    effect.applied_at IS NOT NULL AS effect_applied \
             FROM platform.probe_state_v0 probe \
             JOIN platform.outbox_events event ON event.event_id = probe.requested_event_id \
             LEFT JOIN platform.probe_effects_v0 effect ON effect.event_id = event.event_id \
             WHERE probe.probe_id = $1 AND event.event_id = $2",
        )
        .bind(probe_id)
        .bind(event_id)
        .persistent(false);
        let row = tokio_time::timeout(Duration::from_secs(2), query.fetch_optional(&pool))
            .await
            .map_err(|_| {
                StepFailure::new(
                    "database.probe_read_timeout",
                    "Terminal probe observation exceeded its bounded query deadline.",
                )
            })?
            .map_err(|_| {
                StepFailure::new(
                    "database.probe_read_failed",
                    "Terminal probe state could not be read from the disposable database.",
                )
            })?;
        if let Some(row) = row {
            let probe_status = row.try_get::<String, _>("probe_status").map_err(|_| {
                StepFailure::new("database.invalid_state", "Probe status was invalid.")
            })?;
            let outbox_state = row.try_get::<String, _>("outbox_state").map_err(|_| {
                StepFailure::new("database.invalid_state", "Outbox status was invalid.")
            })?;
            let effect_applied = row.try_get::<bool, _>("effect_applied").map_err(|_| {
                StepFailure::new("database.invalid_state", "Probe effect state was invalid.")
            })?;
            if probe_status == "completed" && outbox_state == "succeeded" && effect_applied {
                break Ok(format!(
                    "evidence://platform-probe/{probe_id}/outbox-terminal"
                ));
            }
            if outbox_state == "dead_letter" {
                break Err(StepFailure::new(
                    "outbox.dead_lettered",
                    "Probe event reached a terminal dead-letter state.",
                ));
            }
        }
        if Instant::now() >= deadline {
            break Err(StepFailure::new(
                "outbox.terminal_timeout",
                "Probe terminal effect was not observed before the bounded deadline.",
            ));
        }
        tokio_time::sleep(Duration::from_millis(200)).await;
    };
    let _ = tokio_time::timeout(Duration::from_secs(2), pool.close()).await;
    result
}

type ProbeWebSocket = WebSocketStream<MaybeTlsStream<TcpStream>>;

struct RealtimeProbeConnection {
    socket: ProbeWebSocket,
}

impl RealtimeProbeConnection {
    async fn connect(
        url: &str,
        subscription_id: Uuid,
        timeout: Duration,
        environment: ProbeEnvironment,
    ) -> Result<Self, StepFailure> {
        validate_ws_url(url)?;
        let websocket_config = WebSocketConfig::default()
            .read_buffer_size(MAX_HTTP_BODY_BYTES)
            .write_buffer_size(8_192)
            .max_write_buffer_size(MAX_HTTP_BODY_BYTES + 8_192)
            .max_message_size(Some(MAX_HTTP_BODY_BYTES))
            .max_frame_size(Some(MAX_HTTP_BODY_BYTES));
        let (socket, _) = tokio_time::timeout(
            timeout,
            connect_async_with_config(url, Some(websocket_config), true),
        )
        .await
        .map_err(|_| {
            StepFailure::new("realtime.connect_timeout", "Realtime connection timed out.")
        })?
        .map_err(|_| StepFailure::new("realtime.connect_failed", "Realtime connection failed."))?;
        let mut connection = Self { socket };
        connection
            .send(ClientFrameBody::Hello(HelloPayload {
                supported_versions: vec![PROTOCOL_VERSION.to_owned()],
            }))
            .await?;
        connection.wait_for(ExpectedFrame::Welcome, timeout).await?;
        let credential = if environment == ProbeEnvironment::Development {
            "platform-probe-development-placeholder".to_owned()
        } else {
            env::var("LIQI_PROBE_AUTH_TOKEN").map_err(|_| {
                StepFailure::new(
                    "realtime.auth_unavailable",
                    "A probe authentication token is required outside development.",
                )
            })?
        };
        connection
            .send(ClientFrameBody::Authenticate(AuthenticatePayload {
                scheme: "bearer".to_owned(),
                credential,
            }))
            .await?;
        connection
            .wait_for(ExpectedFrame::Authenticated, timeout)
            .await?;
        connection
            .send(ClientFrameBody::Subscribe(SubscribePayload {
                subscription_id,
                topic: PROBE_TOPIC.to_owned(),
                resume_cursor: None,
            }))
            .await?;
        connection
            .wait_for(ExpectedFrame::Subscribed(subscription_id), timeout)
            .await?;
        Ok(connection)
    }

    async fn wait_for_event(
        &mut self,
        subscription_id: Uuid,
        event_id: Uuid,
        timeout: Duration,
    ) -> Result<(), StepFailure> {
        self.wait_for(
            ExpectedFrame::Event {
                subscription_id,
                event_id,
            },
            timeout,
        )
        .await
    }

    async fn send(&mut self, body: ClientFrameBody) -> Result<(), StepFailure> {
        let frame = ClientFrame {
            protocol_version: PROTOCOL_VERSION.to_owned(),
            message_id: Uuid::now_v7(),
            sent_at: OffsetDateTime::now_utc(),
            body,
        };
        let encoded = serde_json::to_string(&frame).map_err(|_| {
            StepFailure::new(
                "realtime.encode_failed",
                "Realtime probe frame could not be encoded.",
            )
        })?;
        self.socket
            .send(Message::Text(encoded.into()))
            .await
            .map_err(|_| {
                StepFailure::new(
                    "realtime.send_failed",
                    "Realtime probe frame could not be sent.",
                )
            })
    }

    async fn wait_for(
        &mut self,
        expected: ExpectedFrame,
        timeout: Duration,
    ) -> Result<(), StepFailure> {
        tokio_time::timeout(timeout, self.wait_for_inner(expected))
            .await
            .map_err(|_| {
                StepFailure::new(
                    "realtime.frame_timeout",
                    "Expected realtime frame was not observed.",
                )
            })?
    }

    async fn wait_for_inner(&mut self, expected: ExpectedFrame) -> Result<(), StepFailure> {
        while let Some(message) = self.socket.next().await {
            let message = message.map_err(map_websocket_error)?;
            match message {
                Message::Text(text) => {
                    let frame: ServerFrame = serde_json::from_str(text.as_str()).map_err(|_| {
                        StepFailure::new(
                            "realtime.invalid_frame",
                            "Realtime server frame did not match the V0 contract.",
                        )
                    })?;
                    match frame.body {
                        ServerFrameBody::Heartbeat(payload) => {
                            self.send(ClientFrameBody::HeartbeatAck(HeartbeatAckPayload {
                                nonce: payload.nonce,
                            }))
                            .await?;
                        }
                        ServerFrameBody::Error { error } => {
                            return Err(StepFailure::new(
                                "realtime.server_error",
                                bounded_message(&error.message),
                            ));
                        }
                        ServerFrameBody::SlowConsumer(_) => {
                            return Err(StepFailure::new(
                                "realtime.slow_consumer",
                                "Realtime probe was disconnected as a slow consumer.",
                            ));
                        }
                        ServerFrameBody::AccessRevoked(_) => {
                            return Err(StepFailure::new(
                                "realtime.access_revoked",
                                "Realtime probe access was revoked.",
                            ));
                        }
                        body if expected.matches(&body) => return Ok(()),
                        _ => {}
                    }
                }
                Message::Ping(bytes) => {
                    self.socket
                        .send(Message::Pong(bytes))
                        .await
                        .map_err(map_websocket_error)?;
                }
                Message::Close(_) => {
                    return Err(StepFailure::new(
                        "realtime.closed",
                        "Realtime connection closed before expected evidence was observed.",
                    ));
                }
                _ => {}
            }
        }
        Err(StepFailure::new(
            "realtime.closed",
            "Realtime connection ended before expected evidence was observed.",
        ))
    }
}

enum ExpectedFrame {
    Welcome,
    Authenticated,
    Subscribed(Uuid),
    Event {
        subscription_id: Uuid,
        event_id: Uuid,
    },
}

impl ExpectedFrame {
    fn matches(&self, body: &ServerFrameBody) -> bool {
        match (self, body) {
            (Self::Welcome, ServerFrameBody::Welcome(_))
            | (
                Self::Authenticated,
                ServerFrameBody::Authenticated {
                    authenticated: true,
                },
            ) => true,
            (
                Self::Subscribed(expected),
                ServerFrameBody::Subscribed {
                    subscription_id,
                    topic,
                    ..
                },
            ) => expected == subscription_id && topic == PROBE_TOPIC,
            (
                Self::Event {
                    subscription_id,
                    event_id,
                },
                ServerFrameBody::ServerEvent(payload),
            ) => subscription_id == &payload.subscription_id && event_id == &payload.event.event_id,
            _ => false,
        }
    }
}

fn map_websocket_error(_error: WebSocketError) -> StepFailure {
    StepFailure::new(
        "realtime.transport_failed",
        "Realtime transport failed before expected evidence was observed.",
    )
}

fn validate_ws_url(value: &str) -> Result<(), StepFailure> {
    let url = Url::parse(value).map_err(|_| invalid_endpoint())?;
    if matches!(url.scheme(), "ws" | "wss")
        && url.host_str() == Some("127.0.0.1")
        && url.port().is_some()
        && url.username().is_empty()
        && url.password().is_none()
        && url.fragment().is_none()
    {
        Ok(())
    } else {
        Err(invalid_endpoint())
    }
}

fn join_url(base: &str, path: &str) -> Result<String, StepFailure> {
    let url = Url::parse(base).map_err(|_| invalid_endpoint())?;
    if !matches!(url.scheme(), "http" | "https")
        || url.host_str() != Some("127.0.0.1")
        || url.port().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
        || url.path() != "/"
    {
        return Err(invalid_endpoint());
    }
    url.join(path)
        .map(|joined| joined.to_string())
        .map_err(|_| invalid_endpoint())
}

const fn invalid_endpoint() -> StepFailure {
    StepFailure::new(
        "probe.endpoint_invalid",
        "V0 platform probe endpoints must remain loopback-only.",
    )
}

#[derive(Debug, Clone)]
struct StepFailure {
    class: &'static str,
    safe_message: &'static str,
}

impl StepFailure {
    const fn new(class: &'static str, safe_message: &'static str) -> Self {
        Self {
            class,
            safe_message,
        }
    }
}

fn bounded_message(_message: &str) -> &'static str {
    "Realtime server returned a safe platform error."
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
enum ProbeRunStatus {
    Passed,
    Failed,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
enum CheckStatus {
    Passed,
    Failed,
}

#[derive(Debug, Serialize)]
struct PlatformProbeResult {
    schema_version: &'static str,
    owner: &'static str,
    release_id: String,
    observed_release_id: String,
    environment: ProbeEnvironment,
    #[serde(with = "time_crate::serde::rfc3339")]
    started_at: OffsetDateTime,
    #[serde(with = "time_crate::serde::rfc3339")]
    completed_at: OffsetDateTime,
    status: ProbeRunStatus,
    checks: Vec<ProbeCheck>,
    errors: Vec<ProbeError>,
}

#[derive(Debug, Serialize)]
struct ProbeCheck {
    name: &'static str,
    status: CheckStatus,
    duration_ms: u64,
    evidence_ref: Option<String>,
    error_class: Option<&'static str>,
}

#[derive(Debug, Serialize)]
struct ProbeError {
    error_class: &'static str,
    message: String,
}

struct ProbeRecorder {
    release_id: String,
    observed_release_id: Option<String>,
    environment: ProbeEnvironment,
    checks: Vec<ProbeCheck>,
    errors: Vec<ProbeError>,
}

impl ProbeRecorder {
    fn new(release_id: String, environment: ProbeEnvironment) -> Self {
        Self {
            release_id,
            observed_release_id: None,
            environment,
            checks: Vec::with_capacity(8),
            errors: Vec::new(),
        }
    }

    fn observe_release(&mut self, release_id: &str) {
        if self.observed_release_id.is_none() {
            self.observed_release_id = Some(release_id.to_owned());
        }
    }

    fn record_elapsed(
        &mut self,
        name: &'static str,
        started: Instant,
        result: Result<String, StepFailure>,
    ) {
        let duration_ms = u64::try_from(started.elapsed().as_millis())
            .unwrap_or(u64::MAX)
            .min(300_000);
        match result {
            Ok(evidence_ref) => self.checks.push(ProbeCheck {
                name,
                status: CheckStatus::Passed,
                duration_ms,
                evidence_ref: Some(evidence_ref),
                error_class: None,
            }),
            Err(error) => {
                self.push_error(error.class, error.safe_message);
                self.checks.push(ProbeCheck {
                    name,
                    status: CheckStatus::Failed,
                    duration_ms,
                    evidence_ref: None,
                    error_class: Some(error.class),
                });
            }
        }
    }

    fn push_error(&mut self, error_class: &'static str, message: &'static str) {
        if self.errors.len() < MAX_RESULT_ERRORS {
            self.errors.push(ProbeError {
                error_class,
                message: message.chars().take(MAX_SAFE_MESSAGE_CHARS).collect(),
            });
        }
    }

    fn finish(
        mut self,
        started_at: OffsetDateTime,
        completed_at: OffsetDateTime,
    ) -> PlatformProbeResult {
        for required in REQUIRED_CHECK_NAMES {
            if !self.checks.iter().any(|check| check.name == required) {
                self.push_error(
                    "probe.incomplete",
                    "A required platform probe check did not execute.",
                );
                self.checks.push(ProbeCheck {
                    name: required,
                    status: CheckStatus::Failed,
                    duration_ms: 0,
                    evidence_ref: None,
                    error_class: Some("probe.incomplete"),
                });
            }
        }
        self.checks.sort_by_key(|check| {
            REQUIRED_CHECK_NAMES
                .iter()
                .position(|required| required == &check.name)
                .unwrap_or(REQUIRED_CHECK_NAMES.len())
        });
        let status = if self.checks.len() == REQUIRED_CHECK_NAMES.len()
            && self.errors.is_empty()
            && self
                .checks
                .iter()
                .all(|check| check.status == CheckStatus::Passed)
        {
            ProbeRunStatus::Passed
        } else {
            ProbeRunStatus::Failed
        };
        PlatformProbeResult {
            schema_version: RESULT_SCHEMA,
            owner: OWNER,
            observed_release_id: self
                .observed_release_id
                .unwrap_or_else(|| UNOBSERVED_RELEASE_ID.to_owned()),
            release_id: self.release_id,
            environment: self.environment,
            started_at,
            completed_at,
            status,
            checks: self.checks,
            errors: self.errors,
        }
    }
}

fn write_result_atomic(
    path: &Path,
    result: &PlatformProbeResult,
) -> Result<(), PlatformProbeError> {
    if let Some(parent) = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
    {
        fs::create_dir_all(parent).map_err(PlatformProbeError::WriteResult)?;
    }
    let temporary = path.with_extension(format!("tmp-{}", std::process::id()));
    let encoded = serde_json::to_vec_pretty(result).map_err(PlatformProbeError::SerializeResult)?;
    fs::write(&temporary, encoded).map_err(PlatformProbeError::WriteResult)?;
    if path.exists() {
        fs::remove_file(path).map_err(PlatformProbeError::WriteResult)?;
    }
    fs::rename(&temporary, path).map_err(PlatformProbeError::WriteResult)
}

#[derive(Debug, Error)]
pub enum PlatformProbeError {
    #[error("required platform probe environment variable is missing: {0}")]
    MissingEnvironment(&'static str),
    #[error("LIQI_RELEASE_ID does not satisfy the release identity contract")]
    InvalidReleaseId,
    #[error("LIQI_ENVIRONMENT must be development, staging or production")]
    InvalidEnvironment,
    #[error("LIQI_TEST_DATABASE is empty or exceeds the bounded reference size")]
    InvalidDatabaseReference,
    #[error("platform probe HTTP client could not be initialized")]
    HttpClient,
    #[error("platform probe result could not be serialized")]
    SerializeResult(#[source] serde_json::Error),
    #[error("platform probe result could not be written")]
    WriteResult(#[source] io::Error),
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn release_identity_matches_operations_contract() {
        assert!(valid_release_id("liqi-v0.0.0-test"));
        assert!(valid_release_id("liqi-abc"));
        assert!(!valid_release_id("liqi-a"));
        assert!(!valid_release_id("LIQI-v0"));
        assert!(!valid_release_id("liqi-bad/value"));
    }

    #[test]
    fn endpoint_validation_rejects_authority_tricks() {
        assert!(join_url("http://127.0.0.1:8080", "/health/live").is_ok());
        assert!(validate_ws_url("ws://127.0.0.1:8081/platform/v0/realtime").is_ok());
        assert!(join_url("http://127.0.0.1:8080@external.invalid", "/health/live").is_err());
        assert!(validate_ws_url("ws://127.0.0.1:8081@external.invalid/socket").is_err());
    }

    #[test]
    fn failed_result_uses_explicit_unobserved_release() {
        let recorder =
            ProbeRecorder::new("liqi-v0.0.0-test".to_owned(), ProbeEnvironment::Development);
        let now = OffsetDateTime::now_utc();
        let result = recorder.finish(now, now);
        assert_eq!(result.observed_release_id, UNOBSERVED_RELEASE_ID);
        assert_eq!(result.status, ProbeRunStatus::Failed);
        assert_eq!(result.checks.len(), REQUIRED_CHECK_NAMES.len());
        assert!(
            result
                .errors
                .iter()
                .all(|error| error.error_class == "probe.incomplete")
        );
    }
}
