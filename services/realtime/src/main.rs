#![forbid(unsafe_code)]

mod session;

use axum::{
    Extension, Router,
    extract::WebSocketUpgrade,
    middleware,
    response::{IntoResponse, Response},
    routing::any,
};
use liqi_application::{
    BoundedExecutor, CommittedRealtimeReader, HealthRegistry, PlatformPersistence, RuntimeControl,
};
use liqi_configuration::{
    LocalSecretResolver, RuntimeArgs, RuntimeConfig, SecretResolver as _, ServiceName,
};
use liqi_persistence_postgres::{PostgresAdapterError, PostgresAuthorityStore};
use liqi_protocol::{ArtifactMetadata, DependencyStatus, ErrorCode, RequestContext};
use liqi_runtime::{
    OperationalState, operational_router, platform_request_middleware, refresh_readiness,
    safe_code_response, serve_with_shutdown, write_json_stdout,
};
use liqi_telemetry::{RuntimeMetrics, initialize};
use secrecy::SecretString;
use session::{RealtimeSessionState, run_session};
use std::{error::Error, sync::Arc, time::Duration};
use tokio::{net::TcpListener, task::JoinHandle, time};
use tower_http::catch_panic::CatchPanicLayer;
use tracing::{error, info};

const ARTIFACT: &str = "liqi-realtime";

type MainError = Box<dyn Error + Send + Sync>;

#[tokio::main]
async fn main() -> Result<(), MainError> {
    let args = RuntimeArgs::from_process()?;
    let config = Arc::new(RuntimeConfig::load(
        &args.config_path,
        ServiceName::LiqiRealtime,
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
    let (persistence, realtime_reader, realtime_handoff_ready) =
        persistence_provider(&config, database_secret)?;
    let persistence_for_shutdown = Arc::clone(&persistence);
    let health = Arc::new(HealthRegistry::new(ARTIFACT, &config.service.version));
    let authentication_ready = dev_authentication_enabled(&config);
    let _ = health
        .set_check(
            "realtime-authentication",
            if authentication_ready {
                DependencyStatus::Up
            } else {
                DependencyStatus::Down
            },
            Some(if authentication_ready {
                "development-auth-placeholder-ready"
            } else {
                "authentication-provider-missing"
            }),
        )
        .await;
    let _ = health
        .set_check(
            "realtime-handoff",
            if realtime_handoff_ready {
                DependencyStatus::Up
            } else {
                DependencyStatus::Down
            },
            Some(if realtime_handoff_ready {
                "committed-handoff-ready"
            } else {
                "committed-handoff-provider-missing"
            }),
        )
        .await;
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
        persistence,
        control.child_token(),
    );
    let session_state = Arc::new(RealtimeSessionState {
        reader: realtime_reader,
        metrics,
        cancellation: control.child_token(),
        max_message_bytes: config.limits.max_realtime_message_bytes,
        outbound_capacity: config.limits.realtime_outbound_queue,
        heartbeat_interval: Duration::from_millis(config.limits.realtime_heartbeat_interval_ms),
        heartbeat_timeout: Duration::from_millis(config.limits.realtime_heartbeat_timeout_ms),
        slow_consumer_disconnect: Duration::from_millis(
            config.limits.realtime_slow_consumer_disconnect_ms,
        ),
        max_subscriptions: config.limits.realtime_max_subscriptions,
        dev_authentication_enabled: authentication_ready,
        connection_executor: BoundedExecutor::new(config.runtime.max_in_flight_requests),
    });

    let realtime_routes = Router::new()
        .route("/platform/v0/realtime", any(realtime_upgrade))
        .layer(Extension(session_state))
        .route_layer(middleware::from_fn_with_state(
            Arc::clone(&operational),
            platform_request_middleware,
        ));
    let router = operational_router(Arc::clone(&operational))
        .merge(realtime_routes)
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
        "LIQI realtime runtime listening"
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

async fn realtime_upgrade(
    Extension(state): Extension<Arc<RealtimeSessionState>>,
    Extension(context): Extension<RequestContext>,
    websocket: WebSocketUpgrade,
) -> Response {
    let permit = match state.connection_executor.try_acquire() {
        Ok(permit) => permit,
        Err(_) => return safe_code_response(ErrorCode::PlatformConcurrencyLimited, &context),
    };
    let max_message = state.max_message_bytes;
    let max_write_buffer = max_message
        .saturating_mul(3)
        .max(max_message.saturating_add(1));
    websocket
        .read_buffer_size(max_message.min(131_072))
        .write_buffer_size(max_message)
        .max_write_buffer_size(max_write_buffer)
        .max_frame_size(max_message)
        .max_message_size(max_message)
        .on_upgrade(move |socket| async move {
            let _connection_permit = permit;
            run_session(socket, state, context).await;
        })
        .into_response()
}

fn persistence_provider(
    config: &RuntimeConfig,
    password: SecretString,
) -> Result<
    (
        Arc<dyn PlatformPersistence>,
        Arc<dyn CommittedRealtimeReader>,
        bool,
    ),
    PostgresAdapterError,
> {
    #[cfg(feature = "dev-fakes")]
    if matches!(
        config.environment,
        liqi_configuration::Environment::Local
            | liqi_configuration::Environment::Development
            | liqi_configuration::Environment::Test
    ) && config.feature_enabled("persistence.fake")
    {
        drop(password);
        let store = Arc::new(liqi_test_support::FakePlatformStore::ready());
        return Ok((store.clone(), store, true));
    }
    let store = Arc::new(PostgresAuthorityStore::connect_lazy(config, password)?);
    Ok((store.clone(), store, false))
}

fn dev_authentication_enabled(config: &RuntimeConfig) -> bool {
    #[cfg(feature = "dev-fakes")]
    {
        return matches!(
            config.environment,
            liqi_configuration::Environment::Local
                | liqi_configuration::Environment::Development
                | liqi_configuration::Environment::Test
        );
    }
    #[cfg(not(feature = "dev-fakes"))]
    {
        let _ = config;
        false
    }
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
