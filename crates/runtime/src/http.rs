use axum::{
    Extension, Json, Router,
    body::Body,
    extract::State,
    http::{Request, StatusCode, header},
    middleware::Next,
    response::{IntoResponse, Response},
    routing::get,
};
use liqi_application::{ApplicationError, BoundedExecutor, HealthRegistry, RuntimeControl};
use liqi_configuration::RuntimeConfig;
use liqi_protocol::{ApiError, ArtifactMetadata, ErrorCode, HealthStatus, RequestContext};
use liqi_telemetry::{RuntimeMetrics, attach_remote_parent};
use serde::Serialize;
use std::{
    io::{self, Write as _},
    sync::Arc,
    time::Duration,
};
use tokio::time;
use tokio_util::sync::CancellationToken;
use tracing::{Instrument as _, info_span};

#[derive(Debug, Clone)]
pub struct RequestCancellation(pub CancellationToken);

#[derive(Debug)]
pub struct OperationalState {
    pub config: Arc<RuntimeConfig>,
    pub health: Arc<HealthRegistry>,
    pub metrics: Arc<RuntimeMetrics>,
    pub metadata: ArtifactMetadata,
    pub control: RuntimeControl,
    pub blocking_executor: BoundedBlockingExecutor,
    request_executor: BoundedExecutor,
}

impl OperationalState {
    #[must_use]
    pub fn new(
        config: Arc<RuntimeConfig>,
        health: Arc<HealthRegistry>,
        metrics: Arc<RuntimeMetrics>,
        metadata: ArtifactMetadata,
        control: RuntimeControl,
    ) -> Self {
        let request_executor = BoundedExecutor::new(config.runtime.max_in_flight_requests);
        let blocking_executor = BoundedBlockingExecutor::new(config.runtime.max_blocking_tasks);
        Self {
            config,
            health,
            metrics,
            metadata,
            control,
            blocking_executor,
            request_executor,
        }
    }
}

#[must_use]
pub fn operational_router(state: Arc<OperationalState>) -> Router {
    Router::new()
        .route("/health/live", get(liveness))
        .route("/health/ready", get(readiness))
        .route("/metrics", get(metrics))
        .route("/platform/v0/metadata", get(metadata))
        .layer(Extension(state))
}

async fn liveness(Extension(state): Extension<Arc<OperationalState>>) -> impl IntoResponse {
    (StatusCode::OK, Json(state.health.liveness()))
}

async fn readiness(Extension(state): Extension<Arc<OperationalState>>) -> Response {
    let body = state.health.readiness().await;
    let status = if body.status == HealthStatus::Ready {
        StatusCode::OK
    } else {
        StatusCode::SERVICE_UNAVAILABLE
    };
    (status, Json(body)).into_response()
}

async fn metrics(Extension(state): Extension<Arc<OperationalState>>) -> Response {
    let body = state
        .metrics
        .render_prometheus(state.config.service.name.artifact_name());
    (
        StatusCode::OK,
        [(
            header::CONTENT_TYPE,
            "text/plain; version=0.0.4; charset=utf-8",
        )],
        body,
    )
        .into_response()
}

async fn metadata(Extension(state): Extension<Arc<OperationalState>>) -> impl IntoResponse {
    (StatusCode::OK, Json(state.metadata.clone()))
}

pub async fn platform_request_middleware(
    State(state): State<Arc<OperationalState>>,
    mut request: Request<Body>,
    next: Next,
) -> Response {
    let context = RequestContext::from_headers(request.headers());
    if state.health.is_draining() {
        return safe_code_response(ErrorCode::PlatformNotReady, &context);
    }
    let permit = match state.request_executor.try_acquire() {
        Ok(permit) => permit,
        Err(_) => {
            state.metrics.request_rejected();
            return safe_code_response(ErrorCode::PlatformConcurrencyLimited, &context);
        }
    };
    let cancellation = state.control.request_token();
    request.extensions_mut().insert(context.clone());
    request
        .extensions_mut()
        .insert(RequestCancellation(cancellation.clone()));
    let span = info_span!(
        "http.request",
        http.request.method = %request.method(),
        http.route = tracing::field::Empty,
        url.path = %request.uri().path(),
        liqi.request_id = %context.request_id,
        trace_id = context.trace_id.as_deref().unwrap_or("")
    );
    attach_remote_parent(&span, request.headers());
    state.metrics.request_accepted();
    let _guard = RequestGuard {
        cancellation: cancellation.clone(),
        metrics: Arc::clone(&state.metrics),
        _permit: permit,
    };
    let timeout = Duration::from_millis(state.config.runtime.request_timeout_ms);
    let response = tokio::select! {
        () = cancellation.cancelled() => {
            safe_error_response(ApplicationError::Cancelled, &context)
        }
        result = time::timeout(timeout, next.run(request).instrument(span)) => {
            match result {
                Ok(response) => response,
                Err(_) => {
                    state.metrics.deadline_exceeded();
                    cancellation.cancel();
                    safe_error_response(ApplicationError::DeadlineExceeded, &context)
                }
            }
        }
    };
    with_request_id(response, &context)
}

pub fn safe_error_response(error: ApplicationError, context: &RequestContext) -> Response {
    let wire = error.to_wire(context);
    wire_response(wire)
}

pub fn safe_code_response(code: ErrorCode, context: &RequestContext) -> Response {
    wire_response(ApiError::new(
        code,
        context.request_id,
        context.trace_id.clone(),
    ))
}

fn wire_response(error: ApiError) -> Response {
    let status = StatusCode::from_u16(error.status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
    (status, Json(error)).into_response()
}

fn with_request_id(mut response: Response, context: &RequestContext) -> Response {
    context.apply_response_headers(response.headers_mut());
    response
}

struct RequestGuard {
    cancellation: CancellationToken,
    metrics: Arc<RuntimeMetrics>,
    _permit: tokio::sync::OwnedSemaphorePermit,
}

impl Drop for RequestGuard {
    fn drop(&mut self) {
        self.cancellation.cancel();
        self.metrics.request_finished();
    }
}

pub fn write_json_stdout<T: Serialize>(value: &T) -> Result<(), io::Error> {
    let stdout = io::stdout();
    let mut locked = stdout.lock();
    serde_json::to_writer(&mut locked, value).map_err(io::Error::other)?;
    locked.write_all(b"\n")?;
    locked.flush()
}
