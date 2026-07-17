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
use std::time::Duration;
use thiserror::Error;
use tracing::Span;
use tracing_opentelemetry::OpenTelemetrySpanExt as _;
use tracing_subscriber::{EnvFilter, layer::SubscriberExt as _, util::SubscriberInitExt as _};

const OTLP_EXPORT_TIMEOUT: Duration = Duration::from_secs(5);

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
                KeyValue::new("service.namespace", "liqi-platform"),
                KeyValue::new("deployment.environment.name", config.environment.as_str()),
                KeyValue::new("service.version", config.service.version.clone()),
                KeyValue::new("liqi.release.id", config.service.version.clone()),
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

mod metrics;

pub use metrics::RuntimeMetrics;

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
