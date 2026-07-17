#![forbid(unsafe_code)]

use http::HeaderMap;
use liqi_configuration::RuntimeConfig;
use opentelemetry::{
    KeyValue, global,
    trace::{TraceContextExt as _, TracerProvider as _},
};
use opentelemetry_http::HeaderExtractor;
use opentelemetry_otlp::{Protocol, WithExportConfig as _};
use opentelemetry_sdk::{
    Resource,
    propagation::TraceContextPropagator,
    trace::{Sampler, SdkTracerProvider},
};
use std::{
    fmt::Write as _,
    sync::atomic::{AtomicU64, Ordering},
    time::Duration,
};
use thiserror::Error;
use tracing::Span;
use tracing_opentelemetry::OpenTelemetrySpanExt as _;
use tracing_subscriber::{EnvFilter, layer::SubscriberExt as _, util::SubscriberInitExt as _};

const OTLP_EXPORT_TIMEOUT: Duration = Duration::from_secs(5);
const METRICS_RENDER_MAX_BYTES: usize = 65_536;

#[derive(Debug)]
pub struct TelemetryGuard {
    tracer_provider: Option<SdkTracerProvider>,
}

impl TelemetryGuard {
    pub fn shutdown(self, timeout: Duration) -> Result<(), TelemetryError> {
        if let Some(provider) = self.tracer_provider {
            provider
                .shutdown_with_timeout(timeout)
                .map_err(|_| TelemetryError::Shutdown)?;
        }
        Ok(())
    }
}

pub fn initialize(config: &RuntimeConfig) -> Result<TelemetryGuard, TelemetryError> {
    global::set_text_map_propagator(TraceContextPropagator::new());
    let filter = EnvFilter::try_new(&config.telemetry.log_level)
        .map_err(|_| TelemetryError::InvalidLogFilter)?;
    let json_logs = tracing_subscriber::fmt::layer()
        .json()
        .with_current_span(true)
        .with_span_list(true)
        .with_target(true)
        .with_thread_ids(true)
        .with_thread_names(true);

    if config.feature_enabled("telemetry.otlp") {
        let endpoint = config
            .telemetry
            .otlp_endpoint
            .as_deref()
            .ok_or(TelemetryError::MissingOtlpEndpoint)?;
        let exporter = opentelemetry_otlp::SpanExporter::builder()
            .with_http()
            .with_protocol(Protocol::HttpBinary)
            .with_endpoint(endpoint)
            .with_timeout(OTLP_EXPORT_TIMEOUT)
            .build()
            .map_err(|_| TelemetryError::ExporterBuild)?;
        let resource = Resource::builder()
            .with_service_name(config.service.name.artifact_name().to_owned())
            .with_attributes([
                KeyValue::new(
                    "deployment.environment.name",
                    format!("{:?}", config.environment).to_ascii_lowercase(),
                ),
                KeyValue::new("service.version", config.service.version.clone()),
            ])
            .build();
        let sampler = Sampler::ParentBased(Box::new(Sampler::TraceIdRatioBased(
            config.telemetry.trace_sample_ratio,
        )));
        let provider = SdkTracerProvider::builder()
            .with_batch_exporter(exporter)
            .with_resource(resource)
            .with_sampler(sampler)
            .build();
        let tracer = provider.tracer(config.service.name.artifact_name().to_owned());
        tracing_subscriber::registry()
            .with(filter)
            .with(json_logs)
            .with(tracing_opentelemetry::layer().with_tracer(tracer))
            .try_init()
            .map_err(|_| TelemetryError::SubscriberAlreadyInitialized)?;
        global::set_tracer_provider(provider.clone());
        Ok(TelemetryGuard {
            tracer_provider: Some(provider),
        })
    } else {
        tracing_subscriber::registry()
            .with(filter)
            .with(json_logs)
            .try_init()
            .map_err(|_| TelemetryError::SubscriberAlreadyInitialized)?;
        Ok(TelemetryGuard {
            tracer_provider: None,
        })
    }
}

pub fn attach_remote_parent(span: &Span, headers: &HeaderMap) {
    let parent =
        global::get_text_map_propagator(|propagator| propagator.extract(&HeaderExtractor(headers)));
    if parent.span().span_context().is_valid() {
        let _ = span.set_parent(parent);
    }
}

#[derive(Debug, Default)]
pub struct RuntimeMetrics {
    accepted_requests: AtomicU64,
    rejected_requests: AtomicU64,
    in_flight_requests: AtomicU64,
    deadline_exceeded: AtomicU64,
    realtime_connections: AtomicU64,
    realtime_slow_consumer_disconnects: AtomicU64,
    worker_claimed: AtomicU64,
    worker_retried: AtomicU64,
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

    pub fn slow_consumer_disconnected(&self) {
        self.realtime_slow_consumer_disconnects
            .fetch_add(1, Ordering::Relaxed);
    }

    pub fn worker_claimed(&self, count: usize) {
        self.worker_claimed
            .fetch_add(count.try_into().unwrap_or(u64::MAX), Ordering::Relaxed);
    }

    pub fn worker_retried(&self, count: usize) {
        self.worker_retried
            .fetch_add(count.try_into().unwrap_or(u64::MAX), Ordering::Relaxed);
    }

    #[must_use]
    pub fn render_prometheus(&self, service: &str) -> String {
        let safe_service: String = service
            .chars()
            .filter(|character| character.is_ascii_alphanumeric() || *character == '-')
            .take(64)
            .collect();
        let mut output = String::with_capacity(2048);
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
        append_gauge(
            &mut output,
            "liqi_realtime_connections",
            &safe_service,
            self.realtime_connections.load(Ordering::Relaxed),
        );
        append_counter(
            &mut output,
            "liqi_realtime_slow_consumer_disconnects_total",
            &safe_service,
            self.realtime_slow_consumer_disconnects
                .load(Ordering::Relaxed),
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

#[derive(Debug, Error)]
pub enum TelemetryError {
    #[error("telemetry log filter is invalid")]
    InvalidLogFilter,
    #[error("telemetry OTLP endpoint is required when the capability is enabled")]
    MissingOtlpEndpoint,
    #[error("telemetry OTLP exporter could not be built")]
    ExporterBuild,
    #[error("global telemetry subscriber is already initialized")]
    SubscriberAlreadyInitialized,
    #[error("telemetry provider did not shut down cleanly")]
    Shutdown,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn metrics_are_bounded_and_do_not_contain_secret_fields() {
        let metrics = RuntimeMetrics::default();
        metrics.request_accepted();
        metrics.request_finished();
        let rendered = metrics.render_prometheus("liqi-api");
        assert!(rendered.len() <= METRICS_RENDER_MAX_BYTES);
        assert!(!rendered.contains("secret"));
        assert!(!rendered.contains("password"));
        assert!(!rendered.contains("token"));
    }
}

#[cfg(test)]
mod log_tests {
    use super::*;
    use liqi_configuration::SecretReference;
    use std::{
        io,
        sync::{Arc, Mutex},
    };
    use tracing_subscriber::{Registry, layer::SubscriberExt as _};

    #[derive(Clone, Debug, Default)]
    struct CapturedWriter {
        bytes: Arc<Mutex<Vec<u8>>>,
    }

    #[derive(Debug)]
    struct CapturedWriterHandle {
        bytes: Arc<Mutex<Vec<u8>>>,
    }

    impl<'writer> tracing_subscriber::fmt::MakeWriter<'writer> for CapturedWriter {
        type Writer = CapturedWriterHandle;

        fn make_writer(&'writer self) -> Self::Writer {
            CapturedWriterHandle {
                bytes: Arc::clone(&self.bytes),
            }
        }
    }

    impl io::Write for CapturedWriterHandle {
        fn write(&mut self, buffer: &[u8]) -> io::Result<usize> {
            let mut bytes = self
                .bytes
                .lock()
                .map_err(|_| io::Error::other("captured log lock poisoned"))?;
            bytes.extend_from_slice(buffer);
            Ok(buffer.len())
        }

        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }

    #[test]
    fn structured_log_does_not_expose_secret_reference_locator() {
        let writer = CapturedWriter::default();
        let subscriber = Registry::default().with(
            tracing_subscriber::fmt::layer()
                .json()
                .with_ansi(false)
                .with_writer(writer.clone()),
        );
        let reference =
            SecretReference::parse("file:///run/liqi/secrets/liqi-api/database-password")
                .unwrap_or_else(|error| unreachable!("test reference must parse: {error}"));
        tracing::subscriber::with_default(subscriber, || {
            tracing::info!(secret_reference = ?reference, "secret resolution attempted");
        });
        let bytes = writer
            .bytes
            .lock()
            .unwrap_or_else(|error| error.into_inner())
            .clone();
        let rendered = String::from_utf8_lossy(&bytes);
        assert!(rendered.contains("file"));
        assert!(!rendered.contains("database-password"));
        assert!(!rendered.contains("/run/liqi/secrets/liqi-api"));
    }
}
