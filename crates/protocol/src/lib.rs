#![forbid(unsafe_code)]

mod error;
mod event;
mod health;
mod metadata;
mod probe;
mod realtime;
mod request_context;

pub use error::{ApiError, ERROR_CODE_REGISTRY, ErrorCode, ValidationDetail};
pub use event::{EventEnvelope, EventEnvelopeError, EventMetadata};
pub use health::{DependencyCheck, DependencyStatus, HealthResponse, HealthStatus};
pub use metadata::{ArtifactMetadata, ContractVersions};
pub use probe::{CreateProbeRequest, CreateProbeResponse, ProbeStatus};
pub use realtime::{
    AccessRevokedPayload, AuthenticatePayload, ClientFrame, ClientFrameBody, HeartbeatAckPayload,
    HeartbeatPayload, HelloPayload, ProtocolNegotiationError, ResumePayload, ResumeSubscription,
    ServerEventPayload, ServerFrame, ServerFrameBody, SlowConsumerPayload, SubscribePayload,
    UnsubscribePayload, WelcomePayload, negotiate_protocol,
};
pub use request_context::RequestContext;

pub const PLATFORM_API_VERSION: &str = "0";
pub const ERROR_MODEL_VERSION: &str = "0";
pub const EVENT_ENVELOPE_VERSION: &str = "0";
pub const REALTIME_PROTOCOL_VERSION: &str = "0";
pub const RUNTIME_CONFIG_VERSION: &str = "0";
pub const RUST_TOOLCHAIN_VERSION: &str = "1.97.1";
