use std::{
    fmt::Write as _,
    sync::atomic::{AtomicU64, Ordering},
    time::Duration,
};

const METRICS_RENDER_MAX_BYTES: usize = 65_536;
const DURATION_BUCKETS_MILLIS: [u64; 14] = [
    5, 10, 25, 50, 75, 100, 250, 500, 750, 1_000, 2_500, 5_000, 7_500, 10_000,
];

#[derive(Debug)]
struct DurationHistogram {
    cumulative_buckets: [AtomicU64; DURATION_BUCKETS_MILLIS.len()],
    count: AtomicU64,
    sum_micros: AtomicU64,
}

impl Default for DurationHistogram {
    fn default() -> Self {
        Self {
            cumulative_buckets: std::array::from_fn(|_| AtomicU64::new(0)),
            count: AtomicU64::new(0),
            sum_micros: AtomicU64::new(0),
        }
    }
}

impl DurationHistogram {
    fn record(&self, duration: Duration) {
        let micros = u64::try_from(duration.as_micros()).unwrap_or(u64::MAX);
        self.count.fetch_add(1, Ordering::Relaxed);
        self.sum_micros.fetch_add(micros, Ordering::Relaxed);
        for (index, boundary_ms) in DURATION_BUCKETS_MILLIS.iter().enumerate() {
            if micros <= boundary_ms.saturating_mul(1_000) {
                self.cumulative_buckets[index].fetch_add(1, Ordering::Relaxed);
            }
        }
    }

    fn render(&self, output: &mut String, metric: &str, service: &str) {
        let _ = writeln!(output, "# TYPE {metric} histogram");
        for (index, boundary_ms) in DURATION_BUCKETS_MILLIS.iter().enumerate() {
            let boundary_seconds = (*boundary_ms as f64) / 1_000.0;
            let count = self.cumulative_buckets[index].load(Ordering::Relaxed);
            let _ = writeln!(
                output,
                "{metric}_bucket{{service=\"{service}\",le=\"{boundary_seconds}\"}} {count}"
            );
        }
        let count = self.count.load(Ordering::Relaxed);
        let sum_seconds = (self.sum_micros.load(Ordering::Relaxed) as f64) / 1_000_000.0;
        let _ = writeln!(
            output,
            "{metric}_bucket{{service=\"{service}\",le=\"+Inf\"}} {count}"
        );
        let _ = writeln!(
            output,
            "{metric}_sum{{service=\"{service}\"}} {sum_seconds}"
        );
        let _ = writeln!(output, "{metric}_count{{service=\"{service}\"}} {count}");
    }
}

#[derive(Debug, Default)]
pub struct RuntimeMetrics {
    accepted_requests: AtomicU64,
    rejected_requests: AtomicU64,
    in_flight_requests: AtomicU64,
    deadline_exceeded: AtomicU64,
    request_duration: DurationHistogram,
    probe_committed: AtomicU64,
    realtime_connections: AtomicU64,
    realtime_rejected_subscriptions: AtomicU64,
    realtime_slow_consumer_disconnects: AtomicU64,
    realtime_delivery_duration: DurationHistogram,
    worker_claimed: AtomicU64,
    worker_retried: AtomicU64,
    worker_terminal_failures: AtomicU64,
    worker_processing_duration: DurationHistogram,
}

impl RuntimeMetrics {
    pub fn request_accepted(&self) {
        self.accepted_requests.fetch_add(1, Ordering::Relaxed);
        self.in_flight_requests.fetch_add(1, Ordering::Relaxed);
    }

    pub fn request_finished(&self) {
        let _ =
            self.in_flight_requests
                .fetch_update(Ordering::Relaxed, Ordering::Relaxed, |current| {
                    Some(current.saturating_sub(1))
                });
    }

    pub fn request_rejected(&self) {
        self.rejected_requests.fetch_add(1, Ordering::Relaxed);
    }

    pub fn deadline_exceeded(&self) {
        self.deadline_exceeded.fetch_add(1, Ordering::Relaxed);
    }

    pub fn record_request_duration(&self, duration: Duration) {
        self.request_duration.record(duration);
    }

    pub fn probe_committed(&self) {
        self.probe_committed.fetch_add(1, Ordering::Relaxed);
    }

    pub fn realtime_connected(&self) {
        self.realtime_connections.fetch_add(1, Ordering::Relaxed);
    }

    pub fn realtime_disconnected(&self) {
        let _ = self.realtime_connections.fetch_update(
            Ordering::Relaxed,
            Ordering::Relaxed,
            |current| Some(current.saturating_sub(1)),
        );
    }

    pub fn realtime_subscription_rejected(&self) {
        self.realtime_rejected_subscriptions
            .fetch_add(1, Ordering::Relaxed);
    }

    pub fn slow_consumer_disconnected(&self) {
        self.realtime_slow_consumer_disconnects
            .fetch_add(1, Ordering::Relaxed);
    }

    pub fn record_realtime_delivery_duration(&self, duration: Duration) {
        self.realtime_delivery_duration.record(duration);
    }

    pub fn worker_claimed(&self, count: usize) {
        self.worker_claimed
            .fetch_add(count.try_into().unwrap_or(u64::MAX), Ordering::Relaxed);
    }

    pub fn worker_retried(&self, count: usize) {
        self.worker_retried
            .fetch_add(count.try_into().unwrap_or(u64::MAX), Ordering::Relaxed);
    }

    pub fn worker_terminal_failed(&self, count: usize) {
        self.worker_terminal_failures
            .fetch_add(count.try_into().unwrap_or(u64::MAX), Ordering::Relaxed);
    }

    pub fn record_worker_processing_duration(&self, duration: Duration) {
        self.worker_processing_duration.record(duration);
    }

    #[must_use]
    pub fn render_prometheus(&self, service: &str) -> String {
        let safe_service: String = service
            .chars()
            .filter(|character| character.is_ascii_alphanumeric() || *character == '-')
            .take(64)
            .collect();
        let mut output = String::with_capacity(8_192);
        append_counter(
            &mut output,
            "liqi_runtime_requests_accepted_total",
            &safe_service,
            self.accepted_requests.load(Ordering::Relaxed),
        );
        append_counter(
            &mut output,
            "liqi_runtime_requests_rejected_total",
            &safe_service,
            self.rejected_requests.load(Ordering::Relaxed),
        );
        append_gauge(
            &mut output,
            "liqi_runtime_requests_in_flight",
            &safe_service,
            self.in_flight_requests.load(Ordering::Relaxed),
        );
        append_counter(
            &mut output,
            "liqi_runtime_deadline_exceeded_total",
            &safe_service,
            self.deadline_exceeded.load(Ordering::Relaxed),
        );
        self.request_duration.render(
            &mut output,
            "liqi_runtime_request_duration_seconds",
            &safe_service,
        );
        append_counter(
            &mut output,
            "liqi_platform_probe_committed_total",
            &safe_service,
            self.probe_committed.load(Ordering::Relaxed),
        );
        append_gauge(
            &mut output,
            "liqi_realtime_connections",
            &safe_service,
            self.realtime_connections.load(Ordering::Relaxed),
        );
        append_counter(
            &mut output,
            "liqi_realtime_subscriptions_rejected_total",
            &safe_service,
            self.realtime_rejected_subscriptions.load(Ordering::Relaxed),
        );
        append_counter(
            &mut output,
            "liqi_realtime_slow_consumer_disconnects_total",
            &safe_service,
            self.realtime_slow_consumer_disconnects
                .load(Ordering::Relaxed),
        );
        self.realtime_delivery_duration.render(
            &mut output,
            "liqi_realtime_delivery_duration_seconds",
            &safe_service,
        );
        append_counter(
            &mut output,
            "liqi_worker_claimed_total",
            &safe_service,
            self.worker_claimed.load(Ordering::Relaxed),
        );
        append_counter(
            &mut output,
            "liqi_worker_retried_total",
            &safe_service,
            self.worker_retried.load(Ordering::Relaxed),
        );
        append_counter(
            &mut output,
            "liqi_worker_terminal_failures_total",
            &safe_service,
            self.worker_terminal_failures.load(Ordering::Relaxed),
        );
        self.worker_processing_duration.render(
            &mut output,
            "liqi_worker_processing_duration_seconds",
            &safe_service,
        );
        if output.len() > METRICS_RENDER_MAX_BYTES {
            output.truncate(METRICS_RENDER_MAX_BYTES);
        }
        output
    }
}

fn append_counter(output: &mut String, metric: &str, service: &str, value: u64) {
    let _ = writeln!(output, "# TYPE {metric} counter");
    let _ = writeln!(output, "{metric}{{service=\"{service}\"}} {value}");
}

fn append_gauge(output: &mut String, metric: &str, service: &str, value: u64) {
    let _ = writeln!(output, "# TYPE {metric} gauge");
    let _ = writeln!(output, "{metric}{{service=\"{service}\"}} {value}");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn metrics_are_bounded_and_do_not_contain_secret_fields() {
        let metrics = RuntimeMetrics::default();
        metrics.request_accepted();
        metrics.record_request_duration(Duration::from_millis(10));
        metrics.request_finished();
        let rendered = metrics.render_prometheus("liqi-api");
        assert!(rendered.len() <= METRICS_RENDER_MAX_BYTES);
        assert!(rendered.contains("liqi_runtime_request_duration_seconds_bucket"));
        assert!(!rendered.contains("secret"));
        assert!(!rendered.contains("password"));
        assert!(!rendered.contains("token"));
    }
}
