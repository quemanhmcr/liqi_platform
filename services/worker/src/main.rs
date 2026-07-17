#![forbid(unsafe_code)]

use liqi_application::{
    DurableOutboxConsumer, HealthRegistry, PlatformPersistence, PlatformProbeWorker, RuntimeControl,
};
use liqi_configuration::{
    LocalSecretResolver, RuntimeArgs, RuntimeConfig, SecretResolver as _, ServiceName,
};
use liqi_persistence_postgres::{PostgresAdapterError, PostgresAuthorityStore};
use liqi_protocol::ArtifactMetadata;
use liqi_runtime::{
    OperationalState, operational_router, refresh_readiness, serve_with_shutdown, write_json_stdout,
};
use liqi_telemetry::{RuntimeMetrics, initialize};
use secrecy::SecretString;
use std::time::Instant;
use std::{error::Error, sync::Arc, time::Duration};
use tokio::{net::TcpListener, task::JoinHandle, time};
use tower_http::catch_panic::CatchPanicLayer;
use tracing::{error, info, warn};

const ARTIFACT: &str = "liqi-worker";
const EMPTY_POLL_INTERVAL: Duration = Duration::from_millis(500);
const FAILURE_POLL_INTERVAL: Duration = Duration::from_secs(1);

type MainError = Box<dyn Error + Send + Sync>;
type WorkerProviders = (Arc<dyn PlatformPersistence>, Arc<dyn DurableOutboxConsumer>);

#[tokio::main]
async fn main() -> Result<(), MainError> {
    let args = RuntimeArgs::from_process()?;
    let config = Arc::new(RuntimeConfig::load(
        &args.config_path,
        ServiceName::LiqiWorker,
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
    let (persistence, outbox) = persistence_provider(&config, database_secret)?;
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
    let worker = Arc::new(PlatformProbeWorker::new(
        persistence,
        outbox,
        config.limits.worker_claim_batch,
        Duration::from_millis(config.limits.worker_retry_base_ms),
        Duration::from_millis(config.limits.worker_retry_max_ms),
        config.limits.worker_concurrency,
    ));
    let worker_task = spawn_worker_loop(worker, Arc::clone(&metrics), control.child_token());

    let router = operational_router(operational).layer(CatchPanicLayer::new());
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
        "LIQI worker administration runtime listening"
    );
    let result = serve_with_shutdown(listener, router, health, control.clone()).await;
    control.begin_shutdown();
    drain_background("readiness", readiness_task, control.shutdown_deadline()).await;
    drain_background("worker", worker_task, control.shutdown_deadline()).await;
    close_persistence(persistence_for_shutdown, control.shutdown_deadline()).await;
    if let Err(error) = telemetry.shutdown(control.shutdown_deadline()) {
        error!(error = %error, "telemetry shutdown did not complete cleanly");
    }
    result?;
    Ok(())
}

fn persistence_provider(
    config: &RuntimeConfig,
    database_secret: SecretString,
) -> Result<WorkerProviders, PostgresAdapterError> {
    #[cfg(feature = "dev-fakes")]
    if matches!(
        config.environment,
        liqi_configuration::Environment::Local
            | liqi_configuration::Environment::Development
            | liqi_configuration::Environment::Test
    ) && config.feature_enabled("persistence.fake")
    {
        drop(database_secret);
        let store = Arc::new(liqi_test_support::FakePlatformStore::ready());
        return Ok((store.clone(), store));
    }
    let store = Arc::new(PostgresAuthorityStore::connect_lazy(
        config,
        &database_secret,
    )?);
    Ok((store.clone(), store))
}

fn spawn_readiness(
    health: Arc<HealthRegistry>,
    config: Arc<RuntimeConfig>,
    persistence: Arc<dyn PlatformPersistence>,
    cancellation: tokio_util::sync::CancellationToken,
) -> JoinHandle<()> {
    tokio::spawn(refresh_readiness(health, config, persistence, cancellation))
}

fn spawn_worker_loop(
    worker: Arc<PlatformProbeWorker>,
    metrics: Arc<RuntimeMetrics>,
    cancellation: tokio_util::sync::CancellationToken,
) -> JoinHandle<()> {
    tokio::spawn(async move {
        loop {
            if cancellation.is_cancelled() {
                break;
            }
            let started = Instant::now();
            match worker.run_once().await {
                Ok(outcome) => {
                    metrics.record_worker_processing_duration(started.elapsed());
                    metrics.worker_claimed(outcome.claimed);
                    metrics.worker_retried(outcome.retried);
                    metrics.worker_terminal_failed(outcome.terminal_without_effect);
                    if outcome.claimed == 0
                        && sleep_or_cancel(EMPTY_POLL_INTERVAL, &cancellation).await
                    {
                        break;
                    }
                }
                Err(error) => {
                    metrics.record_worker_processing_duration(started.elapsed());
                    metrics.worker_terminal_failed(1);
                    warn!(
                        operation = "worker.platform_probe",
                        result.class = "server_error",
                        error.class = "worker.iteration_failed",
                        error = %error,
                        "platform probe worker iteration failed"
                    );
                    if sleep_or_cancel(FAILURE_POLL_INTERVAL, &cancellation).await {
                        break;
                    }
                }
            }
        }
    })
}

async fn sleep_or_cancel(
    duration: Duration,
    cancellation: &tokio_util::sync::CancellationToken,
) -> bool {
    tokio::select! {
        () = cancellation.cancelled() => true,
        () = time::sleep(duration) => false,
    }
}

async fn close_persistence(persistence: Arc<dyn PlatformPersistence>, deadline: Duration) {
    if time::timeout(deadline, persistence.close()).await.is_err() {
        error!("persistence close exceeded shutdown deadline");
    }
}

async fn drain_background(name: &'static str, task: JoinHandle<()>, deadline: Duration) {
    match time::timeout(deadline, task).await {
        Ok(Ok(())) => {}
        Ok(Err(error)) => error!(task = name, error = %error, "background task failed"),
        Err(_) => error!(task = name, "background task exceeded shutdown deadline"),
    }
}
