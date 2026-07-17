use liqi_application::{
    CommittedRealtimeReader as _, PlatformProbeApplication, PlatformProbeWorker,
    RealtimeReadRequest,
};
use liqi_protocol::{CreateProbeRequest, RequestContext};
use liqi_test_support::FakePlatformStore;
use std::{sync::Arc, time::Duration};
use tokio_util::sync::CancellationToken;
use uuid::Uuid;

#[tokio::test]
async fn duplicate_delivery_does_not_duplicate_terminal_effect() {
    let store = Arc::new(FakePlatformStore::ready());
    let persistence: Arc<dyn liqi_application::PlatformPersistence> = store.clone();
    let outbox: Arc<dyn liqi_application::DurableOutboxConsumer> = store.clone();
    let application = PlatformProbeApplication::new(persistence.clone(), Duration::from_secs(1));
    let response = application
        .create(
            CreateProbeRequest {
                client_probe_id: Uuid::now_v7(),
            },
            RequestContext::new_root(),
            CancellationToken::new(),
        )
        .await;
    assert!(response.is_ok());
    assert_eq!(store.committed_event_count().await, 1);

    let realtime = store
        .read_committed(RealtimeReadRequest {
            topics: vec!["platform.probe.requested.v0".to_owned()],
            after: None,
            batch_size: 16,
        })
        .await;
    let realtime = realtime
        .unwrap_or_else(|error| unreachable!("committed fake event must be readable: {error}"));
    assert_eq!(realtime.deliveries.len(), 1);
    assert_eq!(
        realtime.deliveries[0].event.event_id,
        response.as_ref().map_or_else(
            |_| unreachable!("probe response was checked above"),
            |value| value.event_id,
        )
    );
    assert!(realtime.next_cursor.is_some());

    let worker = PlatformProbeWorker::new(
        persistence,
        outbox.clone(),
        32,
        Duration::from_millis(500),
        Duration::from_secs(30),
        4,
    );
    let first = worker.run_once().await;
    assert!(first.is_ok());
    assert_eq!(store.terminal_effect_count().await, 1);

    // A distinct at-least-once consumer can receive the same committed event.
    // It must acknowledge its own lease without creating a second terminal effect.
    let duplicate_worker = PlatformProbeWorker::try_with_consumer_id(
        store.clone(),
        store.clone(),
        32,
        Duration::from_millis(500),
        Duration::from_secs(30),
        4,
        "liqi-platform-probe-worker-v0-duplicate",
    )
    .unwrap_or_else(|error| unreachable!("test consumer ID must be valid: {error}"));
    let second = duplicate_worker.run_once().await;
    let second = second.unwrap_or_else(|error| {
        unreachable!("duplicate delivery must complete idempotently: {error}")
    });
    assert_eq!(second.duplicates, 1);
    assert_eq!(second.acknowledged, 1);
    assert_eq!(store.terminal_effect_count().await, 1);
}
