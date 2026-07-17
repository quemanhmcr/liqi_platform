#![forbid(unsafe_code)]

mod health;
mod http;
mod shutdown;

pub use health::{HostReadinessStatus, read_host_readiness, refresh_readiness};
pub use http::{
    OperationalState, RequestCancellation, operational_router, platform_request_middleware,
    safe_code_response, safe_error_response, write_json_stdout,
};
pub use shutdown::{HttpServeError, serve_with_shutdown, wait_for_shutdown_signal};
