use serde::{Deserialize, Serialize};
use serde_json::Value;
use thiserror::Error;
use time::OffsetDateTime;
use uuid::Uuid;

use crate::{ApiError, EventEnvelope, REALTIME_PROTOCOL_VERSION};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ClientFrame {
    pub protocol_version: String,
    pub message_id: Uuid,
    #[serde(with = "time::serde::rfc3339")]
    pub sent_at: OffsetDateTime,
    #[serde(flatten)]
    pub body: ClientFrameBody,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", content = "payload", rename_all = "snake_case")]
pub enum ClientFrameBody {
    Hello(HelloPayload),
    Authenticate(AuthenticatePayload),
    Subscribe(SubscribePayload),
    Unsubscribe(UnsubscribePayload),
    Resume(ResumePayload),
    HeartbeatAck(HeartbeatAckPayload),
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ServerFrame {
    pub protocol_version: String,
    pub message_id: Uuid,
    pub connection_id: Uuid,
    #[serde(with = "time::serde::rfc3339")]
    pub sent_at: OffsetDateTime,
    #[serde(flatten)]
    pub body: ServerFrameBody,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", content = "payload", rename_all = "snake_case")]
pub enum ServerFrameBody {
    Welcome(WelcomePayload),
    Authenticated {
        authenticated: bool,
    },
    Subscribed {
        subscription_id: Uuid,
        topic: String,
        accepted_cursor: Option<String>,
    },
    Unsubscribed {
        subscription_id: Uuid,
    },
    ServerEvent(ServerEventPayload),
    Error {
        error: ApiError,
    },
    Heartbeat(HeartbeatPayload),
    SlowConsumer(SlowConsumerPayload),
    AccessRevoked(AccessRevokedPayload),
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct HelloPayload {
    pub supported_versions: Vec<String>,
}

#[derive(Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct AuthenticatePayload {
    pub scheme: String,
    pub credential: String,
}

impl std::fmt::Debug for AuthenticatePayload {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("AuthenticatePayload")
            .field("scheme", &self.scheme)
            .field("credential", &"[REDACTED]")
            .finish()
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct SubscribePayload {
    pub subscription_id: Uuid,
    pub topic: String,
    pub resume_cursor: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct UnsubscribePayload {
    pub subscription_id: Uuid,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ResumePayload {
    pub subscriptions: Vec<ResumeSubscription>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ResumeSubscription {
    pub subscription_id: Uuid,
    pub topic: String,
    pub cursor: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct HeartbeatAckPayload {
    pub nonce: Uuid,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct WelcomePayload {
    pub selected_version: String,
    pub heartbeat_interval_ms: u64,
    pub heartbeat_timeout_ms: u64,
    pub max_message_bytes: usize,
    pub outbound_queue_capacity: usize,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ServerEventPayload {
    pub subscription_id: Uuid,
    pub cursor: String,
    pub event: EventEnvelope<Value>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct HeartbeatPayload {
    pub nonce: Uuid,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct SlowConsumerPayload {
    pub queued_messages: usize,
    pub queue_capacity: usize,
    pub disconnect_after_ms: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct AccessRevokedPayload {
    pub subscription_id: Uuid,
    pub reason_code: String,
}

pub fn negotiate_protocol(
    supported_versions: &[String],
) -> Result<&'static str, ProtocolNegotiationError> {
    if supported_versions
        .iter()
        .any(|version| version == REALTIME_PROTOCOL_VERSION)
    {
        Ok(REALTIME_PROTOCOL_VERSION)
    } else {
        Err(ProtocolNegotiationError::Unsupported)
    }
}

#[derive(Debug, Error)]
pub enum ProtocolNegotiationError {
    #[error("no mutually supported realtime protocol version")]
    Unsupported,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn version_zero_negotiates_explicitly() {
        let versions = vec!["1".to_owned(), "0".to_owned()];
        assert_eq!(negotiate_protocol(&versions).ok(), Some("0"));
    }

    #[test]
    fn unknown_versions_fail_closed() {
        let versions = vec!["1".to_owned()];
        assert!(negotiate_protocol(&versions).is_err());
    }

    #[test]
    fn credential_debug_is_redacted() {
        let payload = AuthenticatePayload {
            scheme: "bearer".to_owned(),
            credential: "do-not-log-this".to_owned(),
        };
        let rendered = format!("{payload:?}");
        assert!(!rendered.contains("do-not-log-this"));
    }
}
