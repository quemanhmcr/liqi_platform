use http::{HeaderMap, HeaderName, HeaderValue};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

pub static X_REQUEST_ID: HeaderName = HeaderName::from_static("x-request-id");
pub static TRACEPARENT: HeaderName = HeaderName::from_static("traceparent");

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct RequestContext {
    pub request_id: Uuid,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub trace_id: Option<String>,
}

impl RequestContext {
    #[must_use]
    pub fn new_root() -> Self {
        Self {
            request_id: Uuid::now_v7(),
            trace_id: None,
        }
    }

    #[must_use]
    pub fn from_headers(headers: &HeaderMap) -> Self {
        let request_id = headers
            .get(&X_REQUEST_ID)
            .and_then(|value| value.to_str().ok())
            .and_then(|value| Uuid::parse_str(value).ok())
            .unwrap_or_else(Uuid::now_v7);
        let trace_id = headers
            .get(&TRACEPARENT)
            .and_then(|value| value.to_str().ok())
            .and_then(parse_traceparent);
        Self {
            request_id,
            trace_id,
        }
    }

    pub fn apply_response_headers(&self, headers: &mut HeaderMap) {
        if let Ok(value) = HeaderValue::from_str(&self.request_id.to_string()) {
            headers.insert(X_REQUEST_ID.clone(), value);
        }
    }
}

fn parse_traceparent(value: &str) -> Option<String> {
    if value.len() != 55 || value.bytes().any(|byte| byte.is_ascii_uppercase()) {
        return None;
    }
    let mut parts = value.split('-');
    let version = parts.next()?;
    let trace_id = parts.next()?;
    let parent_id = parts.next()?;
    let flags = parts.next()?;
    if parts.next().is_some()
        || version != "00"
        || !valid_lower_hex(trace_id, 32)
        || !valid_lower_hex(parent_id, 16)
        || !valid_lower_hex(flags, 2)
        || trace_id.bytes().all(|byte| byte == b'0')
        || parent_id.bytes().all(|byte| byte == b'0')
    {
        return None;
    }
    Some(trace_id.to_owned())
}

fn valid_lower_hex(value: &str, expected_length: usize) -> bool {
    value.len() == expected_length
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_valid_w3c_traceparent() {
        let mut headers = HeaderMap::new();
        headers.insert(
            TRACEPARENT.clone(),
            HeaderValue::from_static(
                "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
            ),
        );
        let context = RequestContext::from_headers(&headers);
        assert_eq!(
            context.trace_id.as_deref(),
            Some("4bf92f3577b34da6a3ce929d0e0e4736")
        );
    }

    #[test]
    fn rejects_zero_or_uppercase_trace_context() {
        for value in [
            "00-00000000000000000000000000000000-00f067aa0ba902b7-01",
            "00-4BF92F3577B34DA6A3CE929D0E0E4736-00f067aa0ba902b7-01",
            "00-4bf92f3577b34da6a3ce929d0e0e4736-0000000000000000-01",
        ] {
            let mut headers = HeaderMap::new();
            let header = HeaderValue::from_str(value);
            assert!(header.is_ok());
            if let Ok(header) = header {
                headers.insert(TRACEPARENT.clone(), header);
            }
            assert!(RequestContext::from_headers(&headers).trace_id.is_none());
        }
    }

    #[test]
    fn invalid_request_id_is_replaced() {
        let mut headers = HeaderMap::new();
        headers.insert(X_REQUEST_ID.clone(), HeaderValue::from_static("not-a-uuid"));
        let context = RequestContext::from_headers(&headers);
        assert_ne!(context.request_id, Uuid::nil());
    }
}
