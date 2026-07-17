use axum::Router;
use liqi_application::{HealthRegistry, RuntimeControl};
use std::{future::Future, io, sync::Arc};
use thiserror::Error;
use tokio::{net::TcpListener, signal, task::JoinError, time};
use tokio_util::sync::CancellationToken;

pub async fn serve_with_shutdown(
    listener: TcpListener,
    router: Router,
    health: Arc<HealthRegistry>,
    control: RuntimeControl,
) -> Result<(), HttpServeError> {
    serve_until_shutdown(
        listener,
        router,
        health,
        control,
        wait_for_shutdown_signal(),
    )
    .await
}

async fn serve_until_shutdown<F>(
    listener: TcpListener,
    router: Router,
    health: Arc<HealthRegistry>,
    control: RuntimeControl,
    shutdown: F,
) -> Result<(), HttpServeError>
where
    F: Future<Output = ()>,
{
    let graceful = CancellationToken::new();
    let graceful_signal = graceful.clone().cancelled_owned();
    let server = axum::serve(listener, router).with_graceful_shutdown(graceful_signal);
    let mut server_task = tokio::spawn(async move { server.await });
    tokio::pin!(shutdown);

    tokio::select! {
        result = &mut server_task => return flatten_server_result(result),
        () = &mut shutdown => {}
    }

    health.mark_draining();
    control.begin_shutdown();
    graceful.cancel();

    match time::timeout(control.shutdown_deadline(), &mut server_task).await {
        Ok(result) => flatten_server_result(result),
        Err(_) => {
            control.force_cancel();
            server_task.abort();
            let _ = server_task.await;
            Err(HttpServeError::DrainDeadlineExceeded)
        }
    }
}

pub async fn wait_for_shutdown_signal() {
    #[cfg(unix)]
    {
        let terminate = signal::unix::signal(signal::unix::SignalKind::terminate());
        match terminate {
            Ok(mut terminate) => {
                tokio::select! {
                    _ = signal::ctrl_c() => {}
                    _ = terminate.recv() => {}
                }
            }
            Err(_) => {
                let _ = signal::ctrl_c().await;
            }
        }
    }
    #[cfg(not(unix))]
    {
        let _ = signal::ctrl_c().await;
    }
}

fn flatten_server_result(
    result: Result<Result<(), io::Error>, JoinError>,
) -> Result<(), HttpServeError> {
    match result {
        Ok(Ok(())) => Ok(()),
        Ok(Err(error)) => Err(HttpServeError::Io(error)),
        Err(error) => Err(HttpServeError::Join(error)),
    }
}

#[derive(Debug, Error)]
pub enum HttpServeError {
    #[error("HTTP server failed")]
    Io(#[source] io::Error),
    #[error("HTTP server task failed")]
    Join(#[source] JoinError),
    #[error("HTTP graceful drain exceeded the configured deadline")]
    DrainDeadlineExceeded,
}

#[cfg(test)]
mod tests {
    use super::*;
    use liqi_protocol::HealthStatus;
    use std::time::Duration;
    use tokio::sync::oneshot;

    #[tokio::test]
    async fn shutdown_marks_draining_and_finishes_within_deadline() {
        let listener = TcpListener::bind("127.0.0.1:0")
            .await
            .unwrap_or_else(|error| unreachable!("test listener must bind: {error}"));
        let health = Arc::new(HealthRegistry::new("liqi-api", "test"));
        let control = RuntimeControl::new(Duration::from_secs(1));
        let (shutdown_tx, shutdown_rx) = oneshot::channel::<()>();
        let task_health = Arc::clone(&health);
        let server = tokio::spawn(async move {
            serve_until_shutdown(listener, Router::new(), task_health, control, async move {
                let _ = shutdown_rx.await;
            })
            .await
        });
        tokio::task::yield_now().await;
        assert!(shutdown_tx.send(()).is_ok());
        let result = time::timeout(Duration::from_secs(2), server).await;
        assert!(matches!(result, Ok(Ok(Ok(())))));
        assert_eq!(health.liveness().status, HealthStatus::Draining);
    }
}
