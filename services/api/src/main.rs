#![forbid(unsafe_code)]

use axum::{
    Extension, Json, Router,
    extract::{DefaultBodyLimit, rejection::JsonRejection},
    http::StatusCode,
    middleware,
    response::{IntoResponse, Response},
    routing::post,
};
use liqi_application::{
    ApplicationError, HealthRegistry, PlatformPersistence, PlatformProbeApplication, RuntimeControl,
};
use liqi_configuration::{
    LocalSecretResolver, RuntimeArgs, RuntimeConfig, SecretResolver as _, ServiceName,
};
use liqi_persistence_postgres::{PostgresAdapterError, PostgresAuthorityStore};
use liqi_protocol::{ArtifactMetadata, CreateProbeRequest, RequestContext, ValidationDetail};
use liqi_runtime::{
    OperationalState, RequestCancellation, operational_router, platform_request_middleware,
    refresh_readiness, safe_error_response, serve_with_shutdown, write_json_stdout,
};
use liqi_telemetry::{RuntimeMetrics, initialize};
use secrecy::SecretString;
use std::{error::Error, sync::Arc, time::Duration};
use tokio::{net::TcpListener, task::JoinHandle, time};
use tower_http::catch_panic::CatchPanicLayer;
use tracing::{error, info};

const ARTIFACT: &str = "liqi-api";

type MainError = Box<dyn Error + Send + Sync>;

#[tokio::main]
async fn main() -> Result<(), MainError> {
    let args = RuntimeArgs::from_process()?;
    let config = Arc::new(RuntimeConfig::load(
        &args.config_path,
        ServiceName::LiqiApi,
    )?);
    let metadata = ArtifactMetadata::current(
        ARTIFACT,
        &config.service.version,
        config.environment.as_str(),
    );
    if args.print_artifact_metadata {
        write_json_stdout(&metadata)?;
        return Ok(());
    }

    let telemetry = initialize(&config)?;
    let resolver = LocalSecretResolver;
    let database_secret = resolver.resolve(&config.database.secret_ref).await?;
    let persistence = persistence_provider(&config, database_secret)?;
    let persistence_for_shutdown = Arc::clone(&persistence);
    let health = Arc::new(HealthRegistry::new(ARTIFACT, &config.service.version));
    let metrics = Arc::new(RuntimeMetrics::default());
    let control = RuntimeControl::new(Duration::from_millis(config.runtime.shutdown_deadline_ms));
    let operational = Arc::new(OperationalState::new(
        Arc::clone(&config),
        Arc::clone(&health),
        Arc::clone(&metrics),
        metadata,
        control.clone(),
    ));
    let readiness_task = spawn_readiness(
        Arc::clone(&health),
        Arc::clone(&config),
        Arc::clone(&persistence),
        control.child_token(),
    );
    let probe = Arc::new(PlatformProbeApplication::new(
        persistence,
        Duration::from_millis(config.runtime.request_timeout_ms),
    ));

    let platform_routes = Router::new()
        .route("/platform/v0/probes", post(create_probe))
        .layer(DefaultBodyLimit::max(config.limits.max_request_body_bytes))
        .layer(Extension(probe))
        .layer(Extension(Arc::clone(&metrics)))
        .route_layer(middleware::from_fn_with_state(
            Arc::clone(&operational),
            platform_request_middleware,
        ));
    let router = operational_router(Arc::clone(&operational))
        .merge(platform_routes)
        .layer(CatchPanicLayer::new());
    let listen = config.service.listen.socket_addr()?;
    let listener = TcpListener::bind(listen).await?;
    info!(
        service.name = ARTIFACT,
        liqi.release.id = %config.service.version,
        deployment.environment.name = config.environment.as_str(),
        operation = "runtime.listen",
        result.class = "success",
        error.class = "none",
        address = %listen,
        "LIQI API runtime listening"
    );
    let result = serve_with_shutdown(listener, router, health, control.clone()).await;
    control.begin_shutdown();
    drain_background(readiness_task, control.shutdown_deadline()).await;
    close_persistence(persistence_for_shutdown, control.shutdown_deadline()).await;
    if let Err(error) = telemetry.shutdown(control.shutdown_deadline()) {
        error!(error = %error, "telemetry shutdown did not complete cleanly");
    }
    result?;
    Ok(())
}

async fn create_probe(
    Extension(application): Extension<Arc<PlatformProbeApplication>>,
    Extension(metrics): Extension<Arc<RuntimeMetrics>>,
    Extension(context): Extension<RequestContext>,
    Extension(cancellation): Extension<RequestCancellation>,
    payload: Result<Json<CreateProbeRequest>, JsonRejection>,
) -> Response {
    let request = match payload {
        Ok(Json(request)) => request,
        Err(rejection) if rejection.status() == StatusCode::PAYLOAD_TOO_LARGE => {
            return safe_error_response(ApplicationError::BodyTooLarge, &context);
        }
        Err(_) => {
            return safe_error_response(
                ApplicationError::InvalidRequest(vec![ValidationDetail::bounded(
                    None,
                    "Request JSON does not match the platform probe contract.",
                )]),
                &context,
            );
        }
    };
    match application
        .create(request, context.clone(), cancellation.0)
        .await
    {
        Ok(response) => {
            metrics.probe_committed();
            (StatusCode::ACCEPTED, Json(response)).into_response()
        }
        Err(error) => safe_error_response(error, &context),
    }
}

fn persistence_provider(
    config: &RuntimeConfig,
    password: SecretString,
) -> Result<Arc<dyn PlatformPersistence>, PostgresAdapterError> {
    #[cfg(feature = "dev-fakes")]
    if matches!(
        config.environment,
        liqi_configuration::Environment::Local
            | liqi_configuration::Environment::Development
            | liqi_configuration::Environment::Test
    ) && config.feature_enabled("persistence.fake")
    {
        drop(password);
        return Ok(Arc::new(liqi_test_support::FakePlatformStore::ready()));
    }
    Ok(Arc::new(PostgresAuthorityStore::connect_lazy(
        config, password,
    )?))
}

fn spawn_readiness(
    health: Arc<HealthRegistry>,
    config: Arc<RuntimeConfig>,
    persistence: Arc<dyn PlatformPersistence>,
    cancellation: tokio_util::sync::CancellationToken,
) -> JoinHandle<()> {
    tokio::spawn(refresh_readiness(health, config, persistence, cancellation))
}

async fn close_persistence(persistence: Arc<dyn PlatformPersistence>, deadline: Duration) {
    if time::timeout(deadline, persistence.close()).await.is_err() {
        error!("persistence close exceeded shutdown deadline");
    }
}

async fn drain_background(task: JoinHandle<()>, deadline: Duration) {
    match time::timeout(deadline, task).await {
        Ok(Ok(())) => {}
        Ok(Err(error)) => error!(error = %error, "readiness task failed"),
        Err(_) => error!("readiness task exceeded shutdown deadline"),
    }
}
