use ::time::OffsetDateTime;
use axum::extract::ws::{Message, WebSocket};
use futures_util::{SinkExt as _, StreamExt as _};
use liqi_application::{
    ApplicationError, BoundedExecutor, CommittedRealtimeReader, RealtimeCursor, RealtimeReadRequest,
};
use liqi_protocol::{
    AccessRevokedPayload, ApiError, AuthenticatePayload, ClientFrame, ClientFrameBody,
    HeartbeatAckPayload, HeartbeatPayload, HelloPayload, RequestContext, ResumePayload,
    ResumeSubscription, ServerEventPayload, ServerFrame, ServerFrameBody, SlowConsumerPayload,
    SubscribePayload, UnsubscribePayload, WelcomePayload, negotiate_protocol,
};
use liqi_telemetry::RuntimeMetrics;
use std::{collections::BTreeMap, sync::Arc, time::Duration};
use tokio::{
    sync::mpsc,
    task::JoinHandle,
    time::{self, Instant},
};
use tokio_util::sync::CancellationToken;
use tracing::{debug, warn};
use uuid::Uuid;

const HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(10);
const REALTIME_POLL_INTERVAL: Duration = Duration::from_millis(250);
const REALTIME_READ_BATCH: usize = 16;
const FRAME_FIELDS: [&str; 5] = ["protocolVersion", "messageId", "sentAt", "kind", "payload"];

#[derive(Clone)]
pub struct RealtimeSessionState {
    pub reader: Arc<dyn CommittedRealtimeReader>,
    pub metrics: Arc<RuntimeMetrics>,
    pub cancellation: CancellationToken,
    pub max_message_bytes: usize,
    pub outbound_capacity: usize,
    pub heartbeat_interval: Duration,
    pub heartbeat_timeout: Duration,
    pub slow_consumer_disconnect: Duration,
    pub max_subscriptions: usize,
    pub dev_authentication_enabled: bool,
    pub connection_executor: BoundedExecutor,
}

impl std::fmt::Debug for RealtimeSessionState {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("RealtimeSessionState")
            .field("max_message_bytes", &self.max_message_bytes)
            .field("outbound_capacity", &self.outbound_capacity)
            .field("heartbeat_interval", &self.heartbeat_interval)
            .field("heartbeat_timeout", &self.heartbeat_timeout)
            .field("max_subscriptions", &self.max_subscriptions)
            .field(
                "dev_authentication_enabled",
                &self.dev_authentication_enabled,
            )
            .finish_non_exhaustive()
    }
}

#[derive(Debug, Clone)]
struct Subscription {
    topic: String,
    cursor: Option<RealtimeCursor>,
}

#[derive(Debug, Default)]
struct SubscriptionSet {
    entries: BTreeMap<Uuid, Subscription>,
    poll_index: usize,
}

impl SubscriptionSet {
    fn subscribe(
        &mut self,
        payload: SubscribePayload,
        maximum: usize,
    ) -> Result<(), ApplicationError> {
        if payload.topic.is_empty() || payload.topic.len() > 160 {
            return Err(ApplicationError::InvalidRequest(Vec::new()));
        }
        if !self.entries.contains_key(&payload.subscription_id) && self.entries.len() >= maximum {
            return Err(ApplicationError::InvalidRequest(Vec::new()));
        }
        let cursor = payload
            .resume_cursor
            .map(RealtimeCursor::from_opaque)
            .transpose()
            .map_err(|_| ApplicationError::InvalidRequest(Vec::new()))?;
        self.entries.insert(
            payload.subscription_id,
            Subscription {
                topic: payload.topic,
                cursor,
            },
        );
        Ok(())
    }

    fn resume(
        &mut self,
        subscriptions: Vec<ResumeSubscription>,
        maximum: usize,
    ) -> Result<(), ApplicationError> {
        if subscriptions.len() > maximum {
            return Err(ApplicationError::InvalidRequest(Vec::new()));
        }
        for subscription in subscriptions {
            self.subscribe(
                SubscribePayload {
                    subscription_id: subscription.subscription_id,
                    topic: subscription.topic,
                    resume_cursor: Some(subscription.cursor),
                },
                maximum,
            )?;
        }
        Ok(())
    }

    fn unsubscribe(&mut self, subscription_id: Uuid) {
        self.entries.remove(&subscription_id);
        if self.poll_index >= self.entries.len() {
            self.poll_index = 0;
        }
    }

    fn next_for_poll(&mut self) -> Option<(Uuid, String, Option<RealtimeCursor>)> {
        if self.entries.is_empty() {
            return None;
        }
        let index = self.poll_index % self.entries.len();
        self.poll_index = self.poll_index.wrapping_add(1);
        self.entries
            .iter()
            .nth(index)
            .map(|(id, value)| (*id, value.topic.clone(), value.cursor.clone()))
    }

    fn advance(&mut self, subscription_id: Uuid, cursor: RealtimeCursor) {
        if let Some(subscription) = self.entries.get_mut(&subscription_id) {
            subscription.cursor = Some(cursor);
        }
    }

    #[allow(
        dead_code,
        reason = "reserved for the fail-closed authorization revocation provider seam"
    )]
    fn revoke(&mut self, subscription_id: Uuid) -> Option<AccessRevokedPayload> {
        self.entries
            .remove(&subscription_id)
            .map(|_| AccessRevokedPayload {
                subscription_id,
                reason_code: "PLATFORM_ACCESS_REVOKED".to_owned(),
            })
    }
}

pub async fn run_session(
    socket: WebSocket,
    state: Arc<RealtimeSessionState>,
    context: RequestContext,
) {
    let connection_id = Uuid::now_v7();
    state.metrics.realtime_connected();
    let (mut sink, mut stream) = socket.split();
    let (outbound, mut outbound_rx) = mpsc::channel::<Message>(state.outbound_capacity);
    let writer_cancel = state.cancellation.child_token();
    let writer_token = writer_cancel.clone();
    let writer: JoinHandle<()> = tokio::spawn(async move {
        loop {
            tokio::select! {
                () = writer_token.cancelled() => {
                    let _ = sink.send(Message::Close(None)).await;
                    break;
                }
                message = outbound_rx.recv() => {
                    let Some(message) = message else { break; };
                    if sink.send(message).await.is_err() {
                        break;
                    }
                }
            }
        }
    });

    let mut protocol = SessionProtocolState::new();
    let mut handshake_deadline = Box::pin(time::sleep(HANDSHAKE_TIMEOUT));
    let mut heartbeat = time::interval_at(
        Instant::now() + state.heartbeat_interval,
        state.heartbeat_interval,
    );
    heartbeat.set_missed_tick_behavior(time::MissedTickBehavior::Skip);
    let mut poll = time::interval(REALTIME_POLL_INTERVAL);
    poll.set_missed_tick_behavior(time::MissedTickBehavior::Skip);

    loop {
        tokio::select! {
            () = state.cancellation.cancelled() => break,
            () = &mut handshake_deadline, if !protocol.negotiated => {
                break_protocol(
                    &outbound,
                    connection_id,
                    &context,
                    &ApplicationError::ProtocolUnsupported,
                    state.max_message_bytes,
                );
                break;
            }
            incoming = stream.next() => {
                let Some(incoming) = incoming else { break; };
                let Ok(message) = incoming else { break; };
                if handle_wire_message(
                    message,
                    &mut protocol,
                    &state,
                    &context,
                    &outbound,
                    connection_id,
                ) == SessionControl::Break {
                    break;
                }
            }
            _ = heartbeat.tick(), if protocol.negotiated => {
                if handle_heartbeat(&mut protocol, &state, &outbound, connection_id)
                    == SessionControl::Break
                {
                    break;
                }
            }
            _ = poll.tick(), if protocol.authenticated && !protocol.subscriptions.entries.is_empty() => {
                if poll_realtime(&mut protocol, &state, &outbound, connection_id).await
                    == SessionControl::Break
                {
                    break;
                }
            }
        }
    }

    writer_cancel.cancel();
    let _ = time::timeout(
        state.slow_consumer_disconnect.max(Duration::from_secs(1)),
        writer,
    )
    .await;
    state.metrics.realtime_disconnected();
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum SessionControl {
    Continue,
    Break,
}

#[derive(Debug)]
struct SessionProtocolState {
    negotiated: bool,
    authenticated: bool,
    subscriptions: SubscriptionSet,
    last_heartbeat_ack: Instant,
    expected_heartbeat: Option<Uuid>,
}

impl SessionProtocolState {
    fn new() -> Self {
        Self {
            negotiated: false,
            authenticated: false,
            subscriptions: SubscriptionSet::default(),
            last_heartbeat_ack: Instant::now(),
            expected_heartbeat: None,
        }
    }
}

fn handle_wire_message(
    message: Message,
    protocol: &mut SessionProtocolState,
    state: &RealtimeSessionState,
    context: &RequestContext,
    outbound: &mpsc::Sender<Message>,
    connection_id: Uuid,
) -> SessionControl {
    let text = match message {
        Message::Text(text) => text,
        Message::Close(_) => return SessionControl::Break,
        Message::Ping(bytes) => {
            if outbound.try_send(Message::Pong(bytes)).is_err() {
                disconnect_slow_consumer(state, outbound, connection_id);
                return SessionControl::Break;
            }
            return SessionControl::Continue;
        }
        Message::Pong(_) => return SessionControl::Continue,
        Message::Binary(_) => {
            let error = ApplicationError::InvalidRequest(Vec::new());
            let frame = error_frame(connection_id, context, &error);
            let _ = enqueue(outbound, &frame, state.max_message_bytes);
            return SessionControl::Break;
        }
    };
    let frame = match decode_client_frame(text.as_str(), state.max_message_bytes) {
        Ok(frame) => frame,
        Err(error) => {
            let frame = error_frame(connection_id, context, &error);
            let _ = enqueue(outbound, &frame, state.max_message_bytes);
            return SessionControl::Break;
        }
    };
    handle_client_frame(frame, protocol, state, context, outbound, connection_id)
}

fn handle_client_frame(
    frame: ClientFrame,
    protocol: &mut SessionProtocolState,
    state: &RealtimeSessionState,
    context: &RequestContext,
    outbound: &mpsc::Sender<Message>,
    connection_id: Uuid,
) -> SessionControl {
    match frame.body {
        ClientFrameBody::Hello(payload) => {
            handle_hello(&payload, protocol, state, context, outbound, connection_id)
        }
        ClientFrameBody::Authenticate(payload) => {
            handle_authenticate(payload, protocol, state, context, outbound, connection_id)
        }
        ClientFrameBody::Subscribe(payload) => {
            handle_subscribe(payload, protocol, state, context, outbound, connection_id)
        }
        ClientFrameBody::Unsubscribe(payload) => {
            handle_unsubscribe(&payload, protocol, state, outbound, connection_id)
        }
        ClientFrameBody::Resume(payload) => {
            handle_resume(payload, protocol, state, context, outbound, connection_id)
        }
        ClientFrameBody::HeartbeatAck(payload) => handle_heartbeat_ack(&payload, protocol),
    }
}

fn handle_hello(
    payload: &HelloPayload,
    protocol: &mut SessionProtocolState,
    state: &RealtimeSessionState,
    context: &RequestContext,
    outbound: &mpsc::Sender<Message>,
    connection_id: Uuid,
) -> SessionControl {
    if protocol.negotiated || negotiate_protocol(&payload.supported_versions).is_err() {
        break_protocol(
            outbound,
            connection_id,
            context,
            &ApplicationError::ProtocolUnsupported,
            state.max_message_bytes,
        );
        return SessionControl::Break;
    }
    protocol.negotiated = true;
    let response = welcome_frame(connection_id, state);
    enqueue_or_disconnect(state, outbound, connection_id, &response)
}

fn handle_authenticate(
    mut payload: AuthenticatePayload,
    protocol: &mut SessionProtocolState,
    state: &RealtimeSessionState,
    context: &RequestContext,
    outbound: &mpsc::Sender<Message>,
    connection_id: Uuid,
) -> SessionControl {
    if !protocol.negotiated {
        break_protocol(
            outbound,
            connection_id,
            context,
            &ApplicationError::ProtocolUnsupported,
            state.max_message_bytes,
        );
        return SessionControl::Break;
    }
    protocol.authenticated =
        authenticate_placeholder(&mut payload, state.dev_authentication_enabled);
    if !protocol.authenticated {
        break_protocol(
            outbound,
            connection_id,
            context,
            &ApplicationError::Unauthorized,
            state.max_message_bytes,
        );
        return SessionControl::Break;
    }
    let response = server_frame(
        connection_id,
        ServerFrameBody::Authenticated {
            authenticated: true,
        },
    );
    enqueue_or_disconnect(state, outbound, connection_id, &response)
}

fn handle_subscribe(
    payload: SubscribePayload,
    protocol: &mut SessionProtocolState,
    state: &RealtimeSessionState,
    context: &RequestContext,
    outbound: &mpsc::Sender<Message>,
    connection_id: Uuid,
) -> SessionControl {
    if !protocol.authenticated {
        break_protocol(
            outbound,
            connection_id,
            context,
            &ApplicationError::Unauthorized,
            state.max_message_bytes,
        );
        return SessionControl::Break;
    }
    let subscription_id = payload.subscription_id;
    let topic = payload.topic.clone();
    if let Err(error) = protocol
        .subscriptions
        .subscribe(payload, state.max_subscriptions)
    {
        state.metrics.realtime_subscription_rejected();
        break_protocol(
            outbound,
            connection_id,
            context,
            &error,
            state.max_message_bytes,
        );
        return SessionControl::Break;
    }
    let response = server_frame(
        connection_id,
        ServerFrameBody::Subscribed {
            subscription_id,
            topic,
            accepted_cursor: protocol
                .subscriptions
                .entries
                .get(&subscription_id)
                .and_then(|value| value.cursor.as_ref())
                .map(|value| value.as_opaque().to_owned()),
        },
    );
    enqueue_or_disconnect(state, outbound, connection_id, &response)
}

fn handle_unsubscribe(
    payload: &UnsubscribePayload,
    protocol: &mut SessionProtocolState,
    state: &RealtimeSessionState,
    outbound: &mpsc::Sender<Message>,
    connection_id: Uuid,
) -> SessionControl {
    protocol.subscriptions.unsubscribe(payload.subscription_id);
    let response = server_frame(
        connection_id,
        ServerFrameBody::Unsubscribed {
            subscription_id: payload.subscription_id,
        },
    );
    enqueue_or_disconnect(state, outbound, connection_id, &response)
}

fn handle_resume(
    payload: ResumePayload,
    protocol: &mut SessionProtocolState,
    state: &RealtimeSessionState,
    context: &RequestContext,
    outbound: &mpsc::Sender<Message>,
    connection_id: Uuid,
) -> SessionControl {
    if !protocol.authenticated {
        break_protocol(
            outbound,
            connection_id,
            context,
            &ApplicationError::Unauthorized,
            state.max_message_bytes,
        );
        return SessionControl::Break;
    }
    if let Err(error) = protocol
        .subscriptions
        .resume(payload.subscriptions, state.max_subscriptions)
    {
        break_protocol(
            outbound,
            connection_id,
            context,
            &error,
            state.max_message_bytes,
        );
        return SessionControl::Break;
    }
    SessionControl::Continue
}

fn handle_heartbeat_ack(
    payload: &HeartbeatAckPayload,
    protocol: &mut SessionProtocolState,
) -> SessionControl {
    if protocol.expected_heartbeat == Some(payload.nonce) {
        protocol.last_heartbeat_ack = Instant::now();
        protocol.expected_heartbeat = None;
    }
    SessionControl::Continue
}

fn handle_heartbeat(
    protocol: &mut SessionProtocolState,
    state: &RealtimeSessionState,
    outbound: &mpsc::Sender<Message>,
    connection_id: Uuid,
) -> SessionControl {
    if heartbeat_timed_out(
        protocol.last_heartbeat_ack,
        Instant::now(),
        state.heartbeat_timeout,
    ) {
        debug!(%connection_id, "realtime heartbeat timed out");
        return SessionControl::Break;
    }
    let nonce = Uuid::now_v7();
    protocol.expected_heartbeat = Some(nonce);
    let frame = server_frame(
        connection_id,
        ServerFrameBody::Heartbeat(HeartbeatPayload { nonce }),
    );
    enqueue_or_disconnect(state, outbound, connection_id, &frame)
}

async fn poll_realtime(
    protocol: &mut SessionProtocolState,
    state: &RealtimeSessionState,
    outbound: &mpsc::Sender<Message>,
    connection_id: Uuid,
) -> SessionControl {
    let Some((subscription_id, topic, cursor)) = protocol.subscriptions.next_for_poll() else {
        return SessionControl::Continue;
    };
    match state
        .reader
        .read_committed(RealtimeReadRequest {
            topics: vec![topic],
            after: cursor,
            batch_size: REALTIME_READ_BATCH,
        })
        .await
    {
        Ok(batch) => {
            for delivery in batch.deliveries {
                state
                    .metrics
                    .record_realtime_delivery_duration(committed_delivery_age(
                        delivery.event.occurred_at,
                    ));
                let cursor = delivery.cursor.clone();
                let frame = server_frame(
                    connection_id,
                    ServerFrameBody::ServerEvent(ServerEventPayload {
                        subscription_id,
                        cursor: cursor.as_opaque().to_owned(),
                        event: delivery.event,
                    }),
                );
                if enqueue_or_disconnect(state, outbound, connection_id, &frame)
                    == SessionControl::Break
                {
                    return SessionControl::Break;
                }
                protocol.subscriptions.advance(subscription_id, cursor);
            }
            if let Some(cursor) = batch.next_cursor {
                protocol.subscriptions.advance(subscription_id, cursor);
            }
        }
        Err(error) => {
            warn!(%connection_id, error = %error, "committed realtime reader unavailable");
        }
    }
    SessionControl::Continue
}

fn enqueue_or_disconnect(
    state: &RealtimeSessionState,
    outbound: &mpsc::Sender<Message>,
    connection_id: Uuid,
    frame: &ServerFrame,
) -> SessionControl {
    if enqueue(outbound, frame, state.max_message_bytes).is_err() {
        disconnect_slow_consumer(state, outbound, connection_id);
        SessionControl::Break
    } else {
        SessionControl::Continue
    }
}

fn committed_delivery_age(occurred_at: OffsetDateTime) -> Duration {
    let elapsed_nanos = OffsetDateTime::now_utc()
        .unix_timestamp_nanos()
        .saturating_sub(occurred_at.unix_timestamp_nanos())
        .max(0);
    Duration::from_nanos(u64::try_from(elapsed_nanos).unwrap_or(u64::MAX))
}

fn decode_client_frame(text: &str, maximum_bytes: usize) -> Result<ClientFrame, ApplicationError> {
    if text.len() > maximum_bytes {
        return Err(ApplicationError::BodyTooLarge);
    }
    let value: serde_json::Value =
        serde_json::from_str(text).map_err(|_| ApplicationError::InvalidRequest(Vec::new()))?;
    let object = value
        .as_object()
        .ok_or_else(|| ApplicationError::InvalidRequest(Vec::new()))?;
    if object.len() != FRAME_FIELDS.len()
        || FRAME_FIELDS
            .iter()
            .any(|field| !object.contains_key(*field))
    {
        return Err(ApplicationError::InvalidRequest(Vec::new()));
    }
    let frame: ClientFrame =
        serde_json::from_value(value).map_err(|_| ApplicationError::InvalidRequest(Vec::new()))?;
    if frame.protocol_version != "0" {
        return Err(ApplicationError::ProtocolUnsupported);
    }
    Ok(frame)
}

fn heartbeat_timed_out(last_ack: Instant, now: Instant, timeout: Duration) -> bool {
    now.saturating_duration_since(last_ack) >= timeout
}

fn authenticate_placeholder(payload: &mut AuthenticatePayload, enabled: bool) -> bool {
    let valid = enabled && payload.scheme == "bearer" && !payload.credential.is_empty();
    payload.credential.clear();
    valid
}

fn welcome_frame(connection_id: Uuid, state: &RealtimeSessionState) -> ServerFrame {
    server_frame(
        connection_id,
        ServerFrameBody::Welcome(WelcomePayload {
            selected_version: "0".to_owned(),
            heartbeat_interval_ms: u64::try_from(state.heartbeat_interval.as_millis())
                .unwrap_or(u64::MAX),
            heartbeat_timeout_ms: u64::try_from(state.heartbeat_timeout.as_millis())
                .unwrap_or(u64::MAX),
            max_message_bytes: state.max_message_bytes,
            outbound_queue_capacity: state.outbound_capacity,
        }),
    )
}

fn error_frame(
    connection_id: Uuid,
    context: &RequestContext,
    error: &ApplicationError,
) -> ServerFrame {
    let error: ApiError = error.to_wire(context);
    server_frame(connection_id, ServerFrameBody::Error { error })
}

fn server_frame(connection_id: Uuid, body: ServerFrameBody) -> ServerFrame {
    ServerFrame {
        protocol_version: "0".to_owned(),
        message_id: Uuid::now_v7(),
        connection_id,
        sent_at: OffsetDateTime::now_utc(),
        body,
    }
}

fn enqueue(
    outbound: &mpsc::Sender<Message>,
    frame: &ServerFrame,
    maximum_bytes: usize,
) -> Result<(), ()> {
    let encoded = serde_json::to_string(&frame).map_err(|_| ())?;
    if encoded.len() > maximum_bytes {
        return Err(());
    }
    outbound
        .try_send(Message::Text(encoded.into()))
        .map_err(|_| ())
}

fn break_protocol(
    outbound: &mpsc::Sender<Message>,
    connection_id: Uuid,
    context: &RequestContext,
    error: &ApplicationError,
    maximum_bytes: usize,
) {
    let frame = error_frame(connection_id, context, error);
    let _ = enqueue(outbound, &frame, maximum_bytes);
}

fn disconnect_slow_consumer(
    state: &RealtimeSessionState,
    outbound: &mpsc::Sender<Message>,
    connection_id: Uuid,
) {
    state.metrics.slow_consumer_disconnected();
    let frame = server_frame(
        connection_id,
        ServerFrameBody::SlowConsumer(SlowConsumerPayload {
            queued_messages: state.outbound_capacity,
            queue_capacity: state.outbound_capacity,
            disconnect_after_ms: u64::try_from(state.slow_consumer_disconnect.as_millis())
                .unwrap_or(u64::MAX),
        }),
    );
    let _ = enqueue(outbound, &frame, state.max_message_bytes);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn access_revocation_removes_subscription_and_reserves_wire_behavior() {
        let subscription_id = Uuid::now_v7();
        let mut subscriptions = SubscriptionSet::default();
        let result = subscriptions.subscribe(
            SubscribePayload {
                subscription_id,
                topic: "platform.probe.requested.v0".to_owned(),
                resume_cursor: None,
            },
            64,
        );
        assert!(result.is_ok());
        let revoked = subscriptions.revoke(subscription_id);
        assert_eq!(
            revoked.map(|value| value.reason_code),
            Some("PLATFORM_ACCESS_REVOKED".to_owned())
        );
        assert!(subscriptions.entries.is_empty());
    }

    #[test]
    fn unexpected_top_level_frame_fields_are_rejected() {
        let message_id = Uuid::now_v7();
        let text = format!(
            r#"{{"protocolVersion":"0","messageId":"{message_id}","sentAt":"2026-07-17T12:00:00Z","kind":"hello","payload":{{"supportedVersions":["0"]}},"unexpected":"value"}}"#
        );
        assert!(matches!(
            decode_client_frame(&text, 65_536),
            Err(ApplicationError::InvalidRequest(_))
        ));
    }

    #[test]
    fn oversized_and_malformed_messages_are_rejected() {
        assert!(matches!(
            decode_client_frame("{}", 1),
            Err(ApplicationError::BodyTooLarge)
        ));
        assert!(matches!(
            decode_client_frame("{}", 1024),
            Err(ApplicationError::InvalidRequest(_))
        ));
    }

    #[test]
    fn heartbeat_timeout_is_deterministic() {
        let last_ack = Instant::now();
        assert!(!heartbeat_timed_out(
            last_ack,
            last_ack + Duration::from_secs(44),
            Duration::from_secs(45),
        ));
        assert!(heartbeat_timed_out(
            last_ack,
            last_ack + Duration::from_secs(45),
            Duration::from_secs(45),
        ));
    }

    #[tokio::test]
    async fn full_outbound_queue_rejects_a_slow_consumer() {
        let (sender, _receiver) = mpsc::channel(1);
        let connection_id = Uuid::now_v7();
        let first = server_frame(
            connection_id,
            ServerFrameBody::Heartbeat(HeartbeatPayload {
                nonce: Uuid::now_v7(),
            }),
        );
        let second = server_frame(
            connection_id,
            ServerFrameBody::Heartbeat(HeartbeatPayload {
                nonce: Uuid::now_v7(),
            }),
        );
        assert!(enqueue(&sender, &first, 65_536).is_ok());
        assert!(enqueue(&sender, &second, 65_536).is_err());
    }

    #[test]
    fn resume_cursor_advances_opaquely() {
        let subscription_id = Uuid::now_v7();
        let mut subscriptions = SubscriptionSet::default();
        let result = subscriptions.subscribe(
            SubscribePayload {
                subscription_id,
                topic: "platform.probe.requested.v0".to_owned(),
                resume_cursor: Some("opaque:1".to_owned()),
            },
            64,
        );
        assert!(result.is_ok());
        let first = subscriptions.next_for_poll();
        assert_eq!(
            first
                .as_ref()
                .and_then(|(_, _, cursor)| cursor.as_ref())
                .map(RealtimeCursor::as_opaque),
            Some("opaque:1")
        );
        let next = RealtimeCursor::from_opaque("opaque:2")
            .unwrap_or_else(|error| unreachable!("bounded cursor must parse: {error}"));
        subscriptions.advance(subscription_id, next);
        let second = subscriptions.next_for_poll();
        assert_eq!(
            second
                .as_ref()
                .and_then(|(_, _, cursor)| cursor.as_ref())
                .map(RealtimeCursor::as_opaque),
            Some("opaque:2")
        );
    }

    #[test]
    fn authentication_credential_is_cleared_after_placeholder_check() {
        let mut payload = AuthenticatePayload {
            scheme: "bearer".to_owned(),
            credential: "test-only-value".to_owned(),
        };
        assert!(authenticate_placeholder(&mut payload, true));
        assert!(payload.credential.is_empty());
    }
}
