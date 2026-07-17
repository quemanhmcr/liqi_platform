use axum::extract::ws::{Message, WebSocket};
use futures_util::{SinkExt as _, StreamExt as _};
use liqi_application::{
    ApplicationError, BoundedExecutor, CommittedRealtimeReader, RealtimeCursor, RealtimeReadRequest,
};
use liqi_protocol::{
    AccessRevokedPayload, ApiError, AuthenticatePayload, ClientFrame, ClientFrameBody,
    HeartbeatPayload, RequestContext, ResumeSubscription, ServerEventPayload, ServerFrame,
    ServerFrameBody, SlowConsumerPayload, SubscribePayload, WelcomePayload, negotiate_protocol,
};
use liqi_telemetry::RuntimeMetrics;
use std::{collections::BTreeMap, sync::Arc, time::Duration};
use time::OffsetDateTime;
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

    let mut negotiated = false;
    let mut authenticated = false;
    let mut subscriptions = SubscriptionSet::default();
    let mut handshake_deadline = Box::pin(time::sleep(HANDSHAKE_TIMEOUT));
    let mut last_heartbeat_ack = Instant::now();
    let mut expected_heartbeat: Option<Uuid> = None;
    let mut heartbeat = time::interval_at(
        Instant::now() + state.heartbeat_interval,
        state.heartbeat_interval,
    );
    heartbeat.set_missed_tick_behavior(time::MissedTickBehavior::Skip);
    let mut poll = time::interval(REALTIME_POLL_INTERVAL);
    poll.set_missed_tick_behavior(time::MissedTickBehavior::Skip);

    'session: loop {
        tokio::select! {
            () = state.cancellation.cancelled() => break,
            () = &mut handshake_deadline, if !negotiated => {
                break_protocol(
                    &outbound,
                    connection_id,
                    &context,
                    ApplicationError::ProtocolUnsupported,
                    state.max_message_bytes,
                );
                break;
            }
            incoming = stream.next() => {
                let Some(incoming) = incoming else { break; };
                let message = match incoming {
                    Ok(message) => message,
                    Err(_) => break,
                };
                let text = match message {
                    Message::Text(text) => text,
                    Message::Close(_) => break,
                    Message::Ping(bytes) => {
                        if outbound.try_send(Message::Pong(bytes)).is_err() {
                            disconnect_slow_consumer(&state, &outbound, connection_id);
                            break;
                        }
                        continue;
                    }
                    Message::Pong(_) => continue,
                    Message::Binary(_) => {
                        let frame = error_frame(connection_id, &context, ApplicationError::InvalidRequest(Vec::new()));
                        let _ = enqueue(&outbound, frame, state.max_message_bytes);
                        break;
                    }
                };
                let frame = match decode_client_frame(text.as_str(), state.max_message_bytes) {
                    Ok(frame) => frame,
                    Err(error) => {
                        let frame = error_frame(connection_id, &context, error);
                        let _ = enqueue(&outbound, frame, state.max_message_bytes);
                        break;
                    }
                };
                match frame.body {
                    ClientFrameBody::Hello(payload) => {
                        if negotiated || negotiate_protocol(&payload.supported_versions).is_err() {
                            let frame = error_frame(connection_id, &context, ApplicationError::ProtocolUnsupported);
                            let _ = enqueue(&outbound, frame, state.max_message_bytes);
                            break;
                        }
                        negotiated = true;
                        if enqueue(
                            &outbound,
                            welcome_frame(connection_id, &state),
                            state.max_message_bytes,
                        ).is_err() {
                            disconnect_slow_consumer(&state, &outbound, connection_id);
                            break;
                        }
                    }
                    ClientFrameBody::Authenticate(mut payload) => {
                        if !negotiated {
                            break_protocol(&outbound, connection_id, &context, ApplicationError::ProtocolUnsupported, state.max_message_bytes);
                            break;
                        }
                        authenticated = authenticate_placeholder(&mut payload, state.dev_authentication_enabled);
                        if !authenticated {
                            break_protocol(&outbound, connection_id, &context, ApplicationError::Unauthorized, state.max_message_bytes);
                            break;
                        }
                        let response = server_frame(
                            connection_id,
                            ServerFrameBody::Authenticated { authenticated: true },
                        );
                        if enqueue(&outbound, response, state.max_message_bytes).is_err() {
                            disconnect_slow_consumer(&state, &outbound, connection_id);
                            break;
                        }
                    }
                    ClientFrameBody::Subscribe(payload) => {
                        if !authenticated {
                            break_protocol(&outbound, connection_id, &context, ApplicationError::Unauthorized, state.max_message_bytes);
                            break;
                        }
                        let subscription_id = payload.subscription_id;
                        let topic = payload.topic.clone();
                        if let Err(error) = subscriptions.subscribe(payload, state.max_subscriptions) {
                            state.metrics.realtime_subscription_rejected();
                            break_protocol(&outbound, connection_id, &context, error, state.max_message_bytes);
                            break;
                        }
                        let response = server_frame(
                            connection_id,
                            ServerFrameBody::Subscribed {
                                subscription_id,
                                topic,
                                accepted_cursor: subscriptions.entries.get(&subscription_id).and_then(|value| value.cursor.as_ref()).map(|value| value.as_opaque().to_owned()),
                            },
                        );
                        if enqueue(&outbound, response, state.max_message_bytes).is_err() {
                            disconnect_slow_consumer(&state, &outbound, connection_id);
                            break;
                        }
                    }
                    ClientFrameBody::Unsubscribe(payload) => {
                        subscriptions.unsubscribe(payload.subscription_id);
                        let response = server_frame(
                            connection_id,
                            ServerFrameBody::Unsubscribed { subscription_id: payload.subscription_id },
                        );
                        if enqueue(&outbound, response, state.max_message_bytes).is_err() {
                            disconnect_slow_consumer(&state, &outbound, connection_id);
                            break;
                        }
                    }
                    ClientFrameBody::Resume(payload) => {
                        if !authenticated {
                            break_protocol(&outbound, connection_id, &context, ApplicationError::Unauthorized, state.max_message_bytes);
                            break;
                        }
                        if let Err(error) = subscriptions.resume(payload.subscriptions, state.max_subscriptions) {
                            break_protocol(&outbound, connection_id, &context, error, state.max_message_bytes);
                            break;
                        }
                    }
                    ClientFrameBody::HeartbeatAck(payload) => {
                        if expected_heartbeat == Some(payload.nonce) {
                            last_heartbeat_ack = Instant::now();
                            expected_heartbeat = None;
                        }
                    }
                }
            }
            _ = heartbeat.tick(), if negotiated => {
                if heartbeat_timed_out(
                    last_heartbeat_ack,
                    Instant::now(),
                    state.heartbeat_timeout,
                ) {
                    debug!(%connection_id, "realtime heartbeat timed out");
                    break;
                }
                let nonce = Uuid::now_v7();
                expected_heartbeat = Some(nonce);
                let frame = server_frame(connection_id, ServerFrameBody::Heartbeat(HeartbeatPayload { nonce }));
                if enqueue(&outbound, frame, state.max_message_bytes).is_err() {
                    disconnect_slow_consumer(&state, &outbound, connection_id);
                    break;
                }
            }
            _ = poll.tick(), if authenticated && !subscriptions.entries.is_empty() => {
                let Some((subscription_id, topic, cursor)) = subscriptions.next_for_poll() else { continue; };
                match state.reader.read_committed(RealtimeReadRequest {
                    topics: vec![topic],
                    after: cursor,
                    batch_size: REALTIME_READ_BATCH,
                }).await {
                    Ok(batch) => {
                        for delivery in batch.deliveries {
                            state.metrics.record_realtime_delivery_duration(
                                committed_delivery_age(delivery.event.occurred_at),
                            );
                            let cursor = delivery.cursor.clone();
                            let frame = server_frame(
                                connection_id,
                                ServerFrameBody::ServerEvent(ServerEventPayload {
                                    subscription_id,
                                    cursor: cursor.as_opaque().to_owned(),
                                    event: delivery.event,
                                }),
                            );
                            if enqueue(&outbound, frame, state.max_message_bytes).is_err() {
                                disconnect_slow_consumer(&state, &outbound, connection_id);
                                break 'session;
                            }
                            subscriptions.advance(subscription_id, cursor);
                        }
                        if let Some(cursor) = batch.next_cursor {
                            subscriptions.advance(subscription_id, cursor);
                        }
                    }
                    Err(error) => warn!(%connection_id, error = %error, "committed realtime reader unavailable"),
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
    const FRAME_FIELDS: [&str; 5] = ["protocolVersion", "messageId", "sentAt", "kind", "payload"];
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
    error: ApplicationError,
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
    frame: ServerFrame,
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
    error: ApplicationError,
    maximum_bytes: usize,
) {
    let _ = enqueue(
        outbound,
        error_frame(connection_id, context, error),
        maximum_bytes,
    );
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
    let _ = enqueue(outbound, frame, state.max_message_bytes);
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
        assert!(enqueue(&sender, first, 65_536).is_ok());
        assert!(enqueue(&sender, second, 65_536).is_err());
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
