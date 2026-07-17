use std::{future::Future, sync::Arc, time::Duration};
use thiserror::Error;
use tokio::{
    sync::{OwnedSemaphorePermit, Semaphore},
    time,
};
use tokio_util::sync::CancellationToken;

use crate::ApplicationError;

#[derive(Debug, Clone)]
pub struct RuntimeControl {
    stop_intake: CancellationToken,
    force_cancel: CancellationToken,
    shutdown_deadline: Duration,
}

impl RuntimeControl {
    #[must_use]
    pub fn new(shutdown_deadline: Duration) -> Self {
        Self {
            stop_intake: CancellationToken::new(),
            force_cancel: CancellationToken::new(),
            shutdown_deadline,
        }
    }

    #[must_use]
    pub fn child_token(&self) -> CancellationToken {
        self.stop_intake.child_token()
    }

    #[must_use]
    pub fn request_token(&self) -> CancellationToken {
        self.force_cancel.child_token()
    }

    #[must_use]
    pub const fn shutdown_deadline(&self) -> Duration {
        self.shutdown_deadline
    }

    pub fn begin_shutdown(&self) {
        self.stop_intake.cancel();
    }

    pub fn force_cancel(&self) {
        self.force_cancel.cancel();
    }

    pub async fn cancelled(&self) {
        self.stop_intake.cancelled().await;
    }
}

#[derive(Debug, Clone)]
pub struct BoundedExecutor {
    semaphore: Arc<Semaphore>,
}

impl BoundedExecutor {
    #[must_use]
    pub fn new(max_concurrency: usize) -> Self {
        Self {
            semaphore: Arc::new(Semaphore::new(max_concurrency)),
        }
    }

    pub fn try_acquire(&self) -> Result<OwnedSemaphorePermit, BoundedExecutorError> {
        self.semaphore
            .clone()
            .try_acquire_owned()
            .map_err(|_| BoundedExecutorError::AtCapacity)
    }

    #[must_use]
    pub fn available_permits(&self) -> usize {
        self.semaphore.available_permits()
    }
}

#[derive(Debug, Clone)]
pub struct BoundedBlockingExecutor {
    semaphore: Arc<Semaphore>,
}

impl BoundedBlockingExecutor {
    #[must_use]
    pub fn new(max_tasks: usize) -> Self {
        Self {
            semaphore: Arc::new(Semaphore::new(max_tasks)),
        }
    }

    pub fn try_spawn<F, T>(&self, task: F) -> Result<JoinHandle<T>, BoundedExecutorError>
    where
        F: FnOnce() -> T + Send + 'static,
        T: Send + 'static,
    {
        let permit = self
            .semaphore
            .clone()
            .try_acquire_owned()
            .map_err(|_| BoundedExecutorError::AtCapacity)?;
        Ok(tokio::task::spawn_blocking(move || {
            let _permit = permit;
            task()
        }))
    }

    #[must_use]
    pub fn available_permits(&self) -> usize {
        self.semaphore.available_permits()
    }
}

#[derive(Debug, Error)]
pub enum BoundedExecutorError {
    #[error("bounded executor is at capacity")]
    AtCapacity,
}

pub async fn run_with_deadline<F, T>(
    deadline: Duration,
    cancellation: CancellationToken,
    future: F,
) -> Result<T, ApplicationError>
where
    F: Future<Output = Result<T, ApplicationError>>,
{
    tokio::select! {
        () = cancellation.cancelled() => Err(ApplicationError::Cancelled),
        result = time::timeout(deadline, future) => {
            match result {
                Ok(value) => value,
                Err(_) => Err(ApplicationError::DeadlineExceeded),
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn deadline_cancels_an_unbounded_operation() {
        let result =
            run_with_deadline(Duration::from_millis(10), CancellationToken::new(), async {
                time::sleep(Duration::from_secs(1)).await;
                Ok::<_, ApplicationError>(())
            })
            .await;
        assert!(matches!(result, Err(ApplicationError::DeadlineExceeded)));
    }

    #[tokio::test]
    async fn caller_cancellation_propagates() {
        let cancellation = CancellationToken::new();
        cancellation.cancel();
        let result = run_with_deadline(Duration::from_secs(1), cancellation, async {
            Ok::<_, ApplicationError>(())
        })
        .await;
        assert!(matches!(result, Err(ApplicationError::Cancelled)));
    }

    #[test]
    fn graceful_shutdown_stops_intake_before_forcing_requests() {
        let control = RuntimeControl::new(Duration::from_secs(1));
        let intake = control.child_token();
        let request = control.request_token();
        control.begin_shutdown();
        assert!(intake.is_cancelled());
        assert!(!request.is_cancelled());
        control.force_cancel();
        assert!(request.is_cancelled());
    }

    #[tokio::test]
    async fn blocking_executor_rejects_work_instead_of_queueing_unboundedly() {
        let executor = BoundedBlockingExecutor::new(1);
        let (release_tx, release_rx) = tokio::sync::oneshot::channel::<()>();
        let first = executor.try_spawn(move || {
            let _ = release_rx.blocking_recv();
        });
        assert!(first.is_ok());
        assert!(matches!(
            executor.try_spawn(|| ()),
            Err(BoundedExecutorError::AtCapacity)
        ));
        assert!(release_tx.send(()).is_ok());
        let completed = first
            .unwrap_or_else(|error| unreachable!("first blocking task must start: {error}"))
            .await;
        assert!(completed.is_ok());
    }

    #[test]
    fn executor_rejects_work_above_its_bound() {
        let executor = BoundedExecutor::new(1);
        let first = executor.try_acquire();
        assert!(first.is_ok());
        assert!(matches!(
            executor.try_acquire(),
            Err(BoundedExecutorError::AtCapacity)
        ));
        drop(first);
        assert!(executor.try_acquire().is_ok());
    }
}
