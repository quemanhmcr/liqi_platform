use http::StatusCode;
use serde::{Deserialize, Serialize};
use uuid::Uuid;

const MAX_VALIDATION_DETAILS: usize = 16;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum ErrorCode {
    PlatformInvalidRequest,
    PlatformUnauthorized,
    PlatformAccessRevoked,
    PlatformNotFound,
    PlatformConflict,
    PlatformBodyTooLarge,
    PlatformConcurrencyLimited,
    PlatformProtocolUnsupported,
    PlatformDeadlineExceeded,
    PlatformNotReady,
    PlatformDependencyUnavailable,
    PlatformSecretUnavailable,
    PlatformSlowConsumer,
    PlatformInternal,
}

impl ErrorCode {
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::PlatformInvalidRequest => "PLATFORM_INVALID_REQUEST",
            Self::PlatformUnauthorized => "PLATFORM_UNAUTHORIZED",
            Self::PlatformAccessRevoked => "PLATFORM_ACCESS_REVOKED",
            Self::PlatformNotFound => "PLATFORM_NOT_FOUND",
            Self::PlatformConflict => "PLATFORM_CONFLICT",
            Self::PlatformBodyTooLarge => "PLATFORM_BODY_TOO_LARGE",
            Self::PlatformConcurrencyLimited => "PLATFORM_CONCURRENCY_LIMITED",
            Self::PlatformProtocolUnsupported => "PLATFORM_PROTOCOL_UNSUPPORTED",
            Self::PlatformDeadlineExceeded => "PLATFORM_DEADLINE_EXCEEDED",
            Self::PlatformNotReady => "PLATFORM_NOT_READY",
            Self::PlatformDependencyUnavailable => "PLATFORM_DEPENDENCY_UNAVAILABLE",
            Self::PlatformSecretUnavailable => "PLATFORM_SECRET_UNAVAILABLE",
            Self::PlatformSlowConsumer => "PLATFORM_SLOW_CONSUMER",
            Self::PlatformInternal => "PLATFORM_INTERNAL",
        }
    }

    #[must_use]
    pub const fn status(self) -> StatusCode {
        match self {
            Self::PlatformInvalidRequest => StatusCode::BAD_REQUEST,
            Self::PlatformUnauthorized => StatusCode::UNAUTHORIZED,
            Self::PlatformAccessRevoked => StatusCode::FORBIDDEN,
            Self::PlatformNotFound => StatusCode::NOT_FOUND,
            Self::PlatformConflict => StatusCode::CONFLICT,
            Self::PlatformBodyTooLarge => StatusCode::PAYLOAD_TOO_LARGE,
            Self::PlatformConcurrencyLimited | Self::PlatformSlowConsumer => {
                StatusCode::TOO_MANY_REQUESTS
            }
            Self::PlatformProtocolUnsupported => StatusCode::UPGRADE_REQUIRED,
            Self::PlatformDeadlineExceeded => StatusCode::GATEWAY_TIMEOUT,
            Self::PlatformNotReady
            | Self::PlatformDependencyUnavailable
            | Self::PlatformSecretUnavailable => StatusCode::SERVICE_UNAVAILABLE,
            Self::PlatformInternal => StatusCode::INTERNAL_SERVER_ERROR,
        }
    }

    #[must_use]
    pub const fn retryable(self) -> bool {
        matches!(
            self,
            Self::PlatformConcurrencyLimited
                | Self::PlatformDeadlineExceeded
                | Self::PlatformNotReady
                | Self::PlatformDependencyUnavailable
                | Self::PlatformSlowConsumer
        )
    }

    #[must_use]
    pub const fn safe_message(self) -> &'static str {
        match self {
            Self::PlatformInvalidRequest => "The request is invalid.",
            Self::PlatformUnauthorized => "Authentication is required.",
            Self::PlatformAccessRevoked => "Access is no longer permitted.",
            Self::PlatformNotFound => "The requested resource was not found.",
            Self::PlatformConflict => "The request conflicts with the current state.",
            Self::PlatformBodyTooLarge => "The request body exceeds the allowed size.",
            Self::PlatformConcurrencyLimited => "The platform is temporarily at capacity.",
            Self::PlatformProtocolUnsupported => "The protocol version is not supported.",
            Self::PlatformDeadlineExceeded => "The request deadline was exceeded.",
            Self::PlatformNotReady => "The service is not ready.",
            Self::PlatformDependencyUnavailable => "A required dependency is unavailable.",
            Self::PlatformSecretUnavailable => "A required secret is unavailable.",
            Self::PlatformSlowConsumer => "The realtime consumer is too slow.",
            Self::PlatformInternal => "The platform could not complete the request.",
        }
    }
}

pub const ERROR_CODE_REGISTRY: &[ErrorCode] = &[
    ErrorCode::PlatformInvalidRequest,
    ErrorCode::PlatformUnauthorized,
    ErrorCode::PlatformAccessRevoked,
    ErrorCode::PlatformNotFound,
    ErrorCode::PlatformConflict,
    ErrorCode::PlatformBodyTooLarge,
    ErrorCode::PlatformConcurrencyLimited,
    ErrorCode::PlatformProtocolUnsupported,
    ErrorCode::PlatformDeadlineExceeded,
    ErrorCode::PlatformNotReady,
    ErrorCode::PlatformDependencyUnavailable,
    ErrorCode::PlatformSecretUnavailable,
    ErrorCode::PlatformSlowConsumer,
    ErrorCode::PlatformInternal,
];

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ValidationDetail {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub path: Option<String>,
    pub reason: String,
}

impl ValidationDetail {
    #[must_use]
    pub fn bounded(path: Option<&str>, reason: &str) -> Self {
        Self {
            path: path.map(|value| truncate(value, 256)),
            reason: truncate(reason, 256),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ApiError {
    pub code: String,
    pub status: u16,
    pub retryable: bool,
    pub request_id: Uuid,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub trace_id: Option<String>,
    pub message: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub details: Vec<ValidationDetail>,
}

impl ApiError {
    #[must_use]
    pub fn new(code: ErrorCode, request_id: Uuid, trace_id: Option<String>) -> Self {
        Self {
            code: code.as_str().to_owned(),
            status: code.status().as_u16(),
            retryable: code.retryable(),
            request_id,
            trace_id,
            message: code.safe_message().to_owned(),
            details: Vec::new(),
        }
    }

    #[must_use]
    pub fn with_validation_details(
        mut self,
        details: impl IntoIterator<Item = ValidationDetail>,
    ) -> Self {
        self.details = details.into_iter().take(MAX_VALIDATION_DETAILS).collect();
        self
    }
}

fn truncate(value: &str, max_chars: usize) -> String {
    value.chars().take(max_chars).collect()
}
