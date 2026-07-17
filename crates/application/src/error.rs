use liqi_protocol::{ApiError, ErrorCode, RequestContext, ValidationDetail};
use thiserror::Error;

#[derive(Debug, Error)]
pub enum ApplicationError {
    #[error("request validation failed")]
    InvalidRequest(Vec<ValidationDetail>),
    #[error("request body exceeds configured limit")]
    BodyTooLarge,
    #[error("authentication is required")]
    Unauthorized,
    #[error("access has been revoked")]
    AccessRevoked,
    #[error("protocol version is unsupported")]
    ProtocolUnsupported,
    #[error("realtime consumer is too slow")]
    SlowConsumer,
    #[error("request deadline exceeded")]
    DeadlineExceeded,
    #[error("request was cancelled")]
    Cancelled,
    #[error("service is not ready")]
    NotReady,
    #[error("dependency is unavailable")]
    DependencyUnavailable,
    #[error("operation conflicts with current state")]
    Conflict,
    #[error("internal application failure")]
    Internal,
}

impl ApplicationError {
    #[must_use]
    pub fn to_wire(&self, context: &RequestContext) -> ApiError {
        let code = match self {
            Self::InvalidRequest(_) => ErrorCode::PlatformInvalidRequest,
            Self::BodyTooLarge => ErrorCode::PlatformBodyTooLarge,
            Self::Unauthorized => ErrorCode::PlatformUnauthorized,
            Self::AccessRevoked => ErrorCode::PlatformAccessRevoked,
            Self::ProtocolUnsupported => ErrorCode::PlatformProtocolUnsupported,
            Self::SlowConsumer => ErrorCode::PlatformSlowConsumer,
            Self::DeadlineExceeded | Self::Cancelled => ErrorCode::PlatformDeadlineExceeded,
            Self::NotReady => ErrorCode::PlatformNotReady,
            Self::DependencyUnavailable => ErrorCode::PlatformDependencyUnavailable,
            Self::Conflict => ErrorCode::PlatformConflict,
            Self::Internal => ErrorCode::PlatformInternal,
        };
        let error = ApiError::new(code, context.request_id, context.trace_id.clone());
        match self {
            Self::InvalidRequest(details) => error.with_validation_details(details.clone()),
            _ => error,
        }
    }
}

#[derive(Debug, Error)]
pub enum PersistenceError {
    #[error("persistence is not ready")]
    NotReady,
    #[error("persistence operation timed out")]
    Timeout,
    #[error("persistence operation conflicts with durable state")]
    Conflict,
    #[error("persistence rejected an invalid operation")]
    InvalidOperation,
    #[error("persistence operation failed")]
    Internal,
}

impl From<PersistenceError> for ApplicationError {
    fn from(value: PersistenceError) -> Self {
        match value {
            PersistenceError::NotReady => Self::NotReady,
            PersistenceError::Timeout => Self::DeadlineExceeded,
            PersistenceError::Conflict => Self::Conflict,
            PersistenceError::InvalidOperation => Self::Internal,
            PersistenceError::Internal => Self::DependencyUnavailable,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use liqi_protocol::RequestContext;

    #[test]
    fn internal_failure_wire_response_is_safe() {
        let context = RequestContext::new_root();
        let wire = ApplicationError::Internal.to_wire(&context);
        assert_eq!(wire.code, "PLATFORM_INTERNAL");
        assert_eq!(wire.message, "The platform could not complete the request.");
        assert!(wire.details.is_empty());
        let serialized = serde_json::to_string(&wire)
            .unwrap_or_else(|error| unreachable!("safe wire error must serialize: {error}"));
        assert!(!serialized.contains("stack"));
        assert!(!serialized.contains("password"));
        assert!(!serialized.contains("secret"));
    }
}
