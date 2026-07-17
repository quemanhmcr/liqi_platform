use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeMap;
use thiserror::Error;
use time::OffsetDateTime;
use uuid::Uuid;

pub type EventMetadata = BTreeMap<String, Value>;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct EventEnvelope<T> {
    pub event_id: Uuid,
    pub event_type: String,
    pub event_version: u32,
    #[serde(with = "time::serde::rfc3339")]
    pub occurred_at: OffsetDateTime,
    pub producer: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub correlation_id: Option<Uuid>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub causation_id: Option<Uuid>,
    pub aggregate_key: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ordering_key: Option<String>,
    pub payload: T,
    pub metadata: EventMetadata,
}

impl<T> EventEnvelope<T>
where
    T: Serialize,
{
    /// Validates the durable event envelope against the V0 protocol bounds.
    ///
    /// # Errors
    ///
    /// Returns an error when event identity/version fields, producer, aggregate or ordering keys,
    /// metadata, or payload shape violate the protocol contract or cannot be serialized.
    pub fn validate(&self, metadata_max_bytes: usize) -> Result<(), EventEnvelopeError> {
        let event_type_version =
            event_type_version(&self.event_type).ok_or(EventEnvelopeError::InvalidEventType)?;
        if event_type_version != self.event_version {
            return Err(EventEnvelopeError::EventVersionMismatch {
                event_type_version,
                envelope_version: self.event_version,
            });
        }
        if !valid_producer(&self.producer) {
            return Err(EventEnvelopeError::InvalidProducer);
        }
        if self.aggregate_key.is_empty() || self.aggregate_key.len() > 160 {
            return Err(EventEnvelopeError::InvalidAggregateKey);
        }
        if self
            .ordering_key
            .as_ref()
            .is_some_and(|value| value.is_empty() || value.len() > 128)
        {
            return Err(EventEnvelopeError::InvalidOrderingKey);
        }
        if self.metadata.len() > 16 {
            return Err(EventEnvelopeError::TooManyMetadataEntries);
        }
        for (key, value) in &self.metadata {
            if !valid_metadata_key(key) {
                return Err(EventEnvelopeError::InvalidMetadataKey);
            }
            if value
                .as_str()
                .is_some_and(|text| text.chars().count() > 256)
                || matches!(value, Value::Array(_) | Value::Object(_))
            {
                return Err(EventEnvelopeError::InvalidMetadataValue);
            }
        }
        let bytes = serde_json::to_vec(&self.metadata)
            .map_err(EventEnvelopeError::MetadataSerialization)?;
        if bytes.len() > metadata_max_bytes {
            return Err(EventEnvelopeError::MetadataTooLarge {
                actual: bytes.len(),
                maximum: metadata_max_bytes,
            });
        }
        let payload = serde_json::to_value(&self.payload)
            .map_err(EventEnvelopeError::PayloadSerialization)?;
        if !payload.is_object() {
            return Err(EventEnvelopeError::PayloadMustBeObject);
        }
        Ok(())
    }
}

fn event_type_version(value: &str) -> Option<u32> {
    if value.len() > 160 {
        return None;
    }
    let (prefix, version) = value.rsplit_once(".v")?;
    let mut segments = prefix.split('.');
    let first = segments.next()?;
    if !valid_event_segment(first) {
        return None;
    }
    let remaining = segments.collect::<Vec<_>>();
    if remaining.is_empty()
        || remaining
            .iter()
            .any(|segment| !valid_event_segment(segment))
    {
        return None;
    }
    version.parse().ok()
}

fn valid_event_segment(value: &str) -> bool {
    !value.is_empty()
        && value
            .chars()
            .all(|character| character.is_ascii_lowercase() || character.is_ascii_digit())
        && value
            .chars()
            .next()
            .is_some_and(|character| character.is_ascii_lowercase())
}

fn valid_producer(value: &str) -> bool {
    let Some(name) = value.strip_prefix("liqi-") else {
        return false;
    };
    (2..=48).contains(&name.len())
        && name.chars().all(|character| {
            character.is_ascii_lowercase() || character.is_ascii_digit() || character == '-'
        })
}

fn valid_metadata_key(value: &str) -> bool {
    if value.is_empty() || value.len() > 64 {
        return false;
    }
    let mut characters = value.chars();
    characters
        .next()
        .is_some_and(|character| character.is_ascii_lowercase())
        && characters.all(|character| {
            character.is_ascii_lowercase()
                || character.is_ascii_digit()
                || matches!(character, '_' | '.' | '-')
        })
}

#[derive(Debug, Error)]
pub enum EventEnvelopeError {
    #[error("event type must be a lower-case dotted name ending in .vN")]
    InvalidEventType,
    #[error(
        "event type version {event_type_version} differs from envelope version {envelope_version}"
    )]
    EventVersionMismatch {
        event_type_version: u32,
        envelope_version: u32,
    },
    #[error("event producer is invalid")]
    InvalidProducer,
    #[error("aggregate key is invalid")]
    InvalidAggregateKey,
    #[error("ordering key is invalid")]
    InvalidOrderingKey,
    #[error("event metadata has more than 16 entries")]
    TooManyMetadataEntries,
    #[error("event metadata key is invalid")]
    InvalidMetadataKey,
    #[error("event metadata value is invalid")]
    InvalidMetadataValue,
    #[error("event metadata could not be serialized")]
    MetadataSerialization(#[source] serde_json::Error),
    #[error("event metadata is {actual} bytes, maximum is {maximum}")]
    MetadataTooLarge { actual: usize, maximum: usize },
    #[error("event payload could not be serialized")]
    PayloadSerialization(#[source] serde_json::Error),
    #[error("event payload must serialize as a JSON object")]
    PayloadMustBeObject,
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn valid_event() -> EventEnvelope<Value> {
        EventEnvelope {
            event_id: Uuid::now_v7(),
            event_type: "platform.probe.requested.v0".to_owned(),
            event_version: 0,
            occurred_at: OffsetDateTime::now_utc(),
            producer: "liqi-api".to_owned(),
            correlation_id: None,
            causation_id: None,
            aggregate_key: format!("platform-probe:{}", Uuid::now_v7()),
            ordering_key: None,
            payload: json!({ "probeId": Uuid::now_v7() }),
            metadata: EventMetadata::new(),
        }
    }

    #[test]
    fn event_version_must_match_type_suffix() {
        let mut event = valid_event();
        event.event_version = 1;
        assert!(matches!(
            event.validate(4096),
            Err(EventEnvelopeError::EventVersionMismatch { .. })
        ));
    }

    #[test]
    fn payload_must_be_a_json_object() {
        let mut event = valid_event();
        event.payload = json!("not-an-object");
        assert!(matches!(
            event.validate(4096),
            Err(EventEnvelopeError::PayloadMustBeObject)
        ));
    }
}
