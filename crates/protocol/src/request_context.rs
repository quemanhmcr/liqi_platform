use http::{HeaderMap, HeaderValue, header::HeaderName};
use uuid::Uuid;

const REQUEST_ID_HEADER: HeaderName = HeaderName::from_static("x-request-id");
const TRACEPARENT_HEADER: HeaderName = HeaderName::from_static("traceparent");

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RequestContext {
    pub request_id: Uuid,
    pub trace_id: Option<String>,
}

impl RequestContext {
    #[must_use]
    pub fn from_headers(headers: &HeaderMap) -> Self {
        let request_id = headers
            .get(&REQUEST_ID_HEADER)
            .and_then(|value| value.to_str().ok())
            .and_then(|value| Uuid::parse_str(value).ok())
            .unwrap_or_else(Uuid::now_v7);
        let trace_id = headers
            .get(&TRACEPARENT_HEADER)
            .and_then(|value| value.to_str().ok())
            .and_then(extract_trace_id);
        Self {
            request_id,
            trace_id,
        }
    }

    #[must_use]
    pub fn new_root() -> Self {
        Self {
            request_id: Uuid::now_v7(),
            trace_id: None,
        }
    }

    pub fn apply_response_headers(&self, headers: &mut HeaderMap) {
        if let Ok(value) = HeaderValue::from_str(&self.request_id.to_string()) {
            headers.insert(REQUEST_ID_HEADER, value);
        }
    }
}

fn extract_trace_id(value: &str) -> Option<String> {
    let mut parts = value.split('-');
    let version = parts.next()?;
    let trace_id = parts.next()?;
    let parent_id = parts.next()?;
    let flags = parts.next()?;
    if parts.next().is_some()
        || version != "00"
        || trace_id.len() != 32
        || parent_id.len() != 16
        || flags.len() != 2
        || trace_id.chars().all(|character| character == '0')
        || !trace_id
            .chars()
            .all(|character| character.is_ascii_hexdigit() && !character.is_ascii_uppercase())
        || !parent_id
            .chars()
            .all(|character| character.is_ascii_hexdigit() && !character.is_ascii_uppercase())
        || !flags
            .chars()
            .all(|character| character.is_ascii_hexdigit() && !character.is_ascii_uppercase())
    {
        return None;
    }
    Some(trace_id.to_owned())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn preserves_valid_request_and_trace_ids() {
        let request_id = Uuid::now_v7();
        let mut headers = HeaderMap::new();
        if let Ok(value) = HeaderValue::from_str(&request_id.to_string()) {
            headers.insert(REQUEST_ID_HEADER, value);
        }
        headers.insert(
            TRACEPARENT_HEADER,
            HeaderValue::from_static("00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"),
        );
        let context = RequestContext::from_headers(&headers);
        assert_eq!(context.request_id, request_id);
        assert_eq!(
            context.trace_id.as_deref(),
            Some("4bf92f3577b34da6a3ce929d0e0e4736")
        );
    }

    #[test]
    fn rejects_invalid_traceparent() {
        let mut headers = HeaderMap::new();
        headers.insert(
            TRACEPARENT_HEADER,
            HeaderValue::from_static("secret-stack-trace"),
        );
        let context = RequestContext::from_headers(&headers);
        assert!(context.trace_id.is_none());
    }
}
