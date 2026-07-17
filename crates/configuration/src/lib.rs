#![forbid(unsafe_code)]

use async_trait::async_trait;
use secrecy::SecretString;
use serde::Deserialize;
use std::{
    collections::BTreeMap,
    env, fmt, fs,
    net::SocketAddr,
    path::{Path, PathBuf},
};
use thiserror::Error;

const MAX_CONFIG_BYTES: u64 = 1_048_576;
const CONFIG_PATH_ENV: &str = "LIQI_CONFIG_PATH";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Environment {
    Local,
    Development,
    Test,
    Staging,
    Production,
}

impl Environment {
    #[must_use]
    pub const fn is_production_like(self) -> bool {
        matches!(self, Self::Staging | Self::Production)
    }

    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Local => "local",
            Self::Development => "development",
            Self::Test => "test",
            Self::Staging => "staging",
            Self::Production => "production",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum ServiceName {
    LiqiApi,
    LiqiRealtime,
    LiqiWorker,
}

impl ServiceName {
    #[must_use]
    pub const fn artifact_name(self) -> &'static str {
        match self {
            Self::LiqiApi => "liqi-api",
            Self::LiqiRealtime => "liqi-realtime",
            Self::LiqiWorker => "liqi-worker",
        }
    }
}

#[derive(Clone, PartialEq, Eq, Deserialize)]
#[serde(transparent)]
pub struct SecretReference(String);

impl SecretReference {
    pub fn parse(value: impl Into<String>) -> Result<Self, ConfigError> {
        let value = value.into();
        let (scheme, locator) = value
            .split_once("://")
            .ok_or(ConfigError::InvalidSecretReference)?;
        if locator.trim().is_empty() || locator.chars().any(char::is_whitespace) {
            return Err(ConfigError::InvalidSecretReference);
        }
        match scheme {
            "env" | "file" | "oci-vault" => Ok(Self(value)),
            _ => Err(ConfigError::UnsupportedSecretScheme(scheme.to_owned())),
        }
    }

    #[must_use]
    pub fn scheme(&self) -> &str {
        self.0
            .split_once("://")
            .map_or("invalid", |(scheme, _)| scheme)
    }

    fn locator(&self) -> &str {
        self.0.split_once("://").map_or("", |(_, locator)| locator)
    }
}

impl fmt::Debug for SecretReference {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("SecretReference")
            .field("scheme", &self.scheme())
            .field("locator", &"[REDACTED]")
            .finish()
    }
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct RuntimeConfig {
    pub schema_version: String,
    pub environment: Environment,
    pub host: HostConfig,
    pub service: ServiceConfig,
    pub database: DatabaseConfig,
    pub runtime: RuntimeLimits,
    pub limits: PayloadLimits,
    pub telemetry: TelemetryConfig,
    pub features: BTreeMap<String, bool>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct HostConfig {
    pub readiness_file: PathBuf,
    pub readiness_schema_version: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ServiceConfig {
    pub name: ServiceName,
    pub version: String,
    pub instance_id: Option<String>,
    pub listen: ListenConfig,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ListenConfig {
    pub host: String,
    pub port: u16,
}

impl ListenConfig {
    pub fn socket_addr(&self) -> Result<SocketAddr, ConfigError> {
        format!("{}:{}", self.host, self.port)
            .parse()
            .map_err(|_| ConfigError::InvalidListenAddress)
    }
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct DatabaseConfig {
    pub endpoint: DatabaseEndpoint,
    pub database_name: String,
    pub role: DatabaseRole,
    pub secret_ref: SecretReference,
    pub connect_timeout_ms: u64,
    pub acquire_timeout_ms: u64,
    pub statement_timeout_ms: u64,
    pub lock_timeout_ms: u64,
    pub idle_in_transaction_timeout_ms: u64,
    pub max_connections: u32,
    pub min_connections: u32,
    pub required_migration_version: u64,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct DatabaseEndpoint {
    pub host: String,
    pub port: u16,
    pub pooling_mode: PoolingMode,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum PoolingMode {
    Transaction,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DatabaseRole {
    LiqiApi,
    LiqiRealtime,
    LiqiWorker,
}

impl DatabaseRole {
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::LiqiApi => "liqi_api",
            Self::LiqiRealtime => "liqi_realtime",
            Self::LiqiWorker => "liqi_worker",
        }
    }

    #[must_use]
    pub const fn maximum_pool_size(self) -> u32 {
        match self {
            Self::LiqiApi => 20,
            Self::LiqiRealtime => 5,
            Self::LiqiWorker => 10,
        }
    }

    #[must_use]
    pub const fn expected_for(service: ServiceName) -> Self {
        match service {
            ServiceName::LiqiApi => Self::LiqiApi,
            ServiceName::LiqiRealtime => Self::LiqiRealtime,
            ServiceName::LiqiWorker => Self::LiqiWorker,
        }
    }
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct RuntimeLimits {
    pub request_timeout_ms: u64,
    pub shutdown_deadline_ms: u64,
    pub max_in_flight_requests: usize,
    pub max_blocking_tasks: usize,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct PayloadLimits {
    pub max_request_body_bytes: usize,
    pub max_realtime_message_bytes: usize,
    pub realtime_outbound_queue: usize,
    pub realtime_heartbeat_interval_ms: u64,
    pub realtime_heartbeat_timeout_ms: u64,
    pub realtime_slow_consumer_disconnect_ms: u64,
    pub realtime_max_subscriptions: usize,
    pub worker_claim_batch: usize,
    pub worker_concurrency: usize,
    pub worker_max_attempts: u32,
    pub worker_retry_base_ms: u64,
    pub worker_retry_max_ms: u64,
    pub validation_error_items: usize,
    pub event_metadata_bytes: usize,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct TelemetryConfig {
    pub log_level: String,
    pub otlp_endpoint: Option<String>,
    pub trace_sample_ratio: f64,
}

impl RuntimeConfig {
    pub fn load(
        path: impl AsRef<Path>,
        expected_service: ServiceName,
    ) -> Result<Self, ConfigError> {
        let path = path.as_ref();
        let metadata = fs::metadata(path).map_err(|source| ConfigError::Read {
            path: path.to_path_buf(),
            source,
        })?;
        if metadata.len() > MAX_CONFIG_BYTES {
            return Err(ConfigError::TooLarge(metadata.len()));
        }
        let bytes = fs::read(path).map_err(|source| ConfigError::Read {
            path: path.to_path_buf(),
            source,
        })?;
        let mut config: Self = serde_json::from_slice(&bytes).map_err(ConfigError::Parse)?;
        config.database.secret_ref = SecretReference::parse(config.database.secret_ref.0)?;
        config.validate(expected_service)?;
        Ok(config)
    }

    pub fn validate(&self, expected_service: ServiceName) -> Result<(), ConfigError> {
        if self.schema_version != "0" {
            return Err(ConfigError::UnsupportedSchemaVersion(
                self.schema_version.clone(),
            ));
        }
        if self.service.name != expected_service {
            return Err(ConfigError::WrongService {
                expected: expected_service,
                actual: self.service.name,
            });
        }
        if self.service.version.trim().is_empty() {
            return Err(ConfigError::InvalidValue("service.version"));
        }
        let _ = self.service.listen.socket_addr()?;
        let readiness_path = self.host.readiness_file.to_string_lossy();
        if !(self.host.readiness_file.is_absolute() || readiness_path.starts_with('/'))
            || self.host.readiness_schema_version != "liqi.platform.host-readiness/v0"
        {
            return Err(ConfigError::InvalidValue("host readiness contract"));
        }
        if self.database.endpoint.host.trim().is_empty()
            || self.database.endpoint.port != 6432
            || self.database.database_name != "liqi"
            || self.database.role != DatabaseRole::expected_for(expected_service)
            || self.database.max_connections == 0
            || self.database.max_connections > self.database.role.maximum_pool_size()
            || self.database.min_connections > self.database.max_connections
        {
            return Err(ConfigError::InvalidValue(
                "database transaction-pool contract",
            ));
        }
        let timeout_valid = match expected_service {
            ServiceName::LiqiApi => {
                self.database.statement_timeout_ms <= 5_000
                    && self.database.lock_timeout_ms <= 2_000
                    && self.database.idle_in_transaction_timeout_ms <= 15_000
            }
            ServiceName::LiqiRealtime => {
                self.database.statement_timeout_ms <= 3_000
                    && self.database.lock_timeout_ms <= 2_000
                    && self.database.idle_in_transaction_timeout_ms <= 15_000
            }
            ServiceName::LiqiWorker => {
                self.database.statement_timeout_ms <= 30_000
                    && self.database.lock_timeout_ms <= 5_000
                    && self.database.idle_in_transaction_timeout_ms <= 30_000
            }
        };
        if !timeout_valid {
            return Err(ConfigError::InvalidValue("database timeout policy"));
        }
        if self.runtime.max_in_flight_requests == 0 || self.runtime.max_blocking_tasks == 0 {
            return Err(ConfigError::InvalidValue("runtime bounds"));
        }
        if self.limits.max_request_body_bytes == 0
            || self.limits.max_realtime_message_bytes == 0
            || self.limits.realtime_outbound_queue == 0
            || self.limits.realtime_heartbeat_interval_ms == 0
            || self.limits.realtime_heartbeat_timeout_ms
                <= self.limits.realtime_heartbeat_interval_ms
            || self.limits.realtime_max_subscriptions == 0
            || self.limits.realtime_max_subscriptions > 64
            || self.limits.worker_claim_batch == 0
            || self.limits.worker_claim_batch > 50
            || self.limits.worker_concurrency == 0
            || self.limits.worker_max_attempts != 8
            || self.limits.worker_retry_base_ms == 0
            || self.limits.worker_retry_base_ms > self.limits.worker_retry_max_ms
            || self.limits.validation_error_items == 0
            || self.limits.validation_error_items > 16
            || self.limits.event_metadata_bytes != 4096
        {
            return Err(ConfigError::InvalidValue("payload and worker bounds"));
        }
        if !(0.0..=1.0).contains(&self.telemetry.trace_sample_ratio) {
            return Err(ConfigError::InvalidValue("telemetry.traceSampleRatio"));
        }
        if self.environment.is_production_like() && self.database.secret_ref.scheme() == "env" {
            return Err(ConfigError::ProductionEnvironmentSecret);
        }
        if self.environment.is_production_like() && self.feature_enabled("persistence.fake") {
            return Err(ConfigError::ProductionFakePersistence);
        }
        Ok(())
    }

    #[must_use]
    pub fn feature_enabled(&self, name: &str) -> bool {
        self.features.get(name).copied().unwrap_or(false)
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RuntimeArgs {
    pub config_path: PathBuf,
    pub print_artifact_metadata: bool,
}

impl RuntimeArgs {
    pub fn from_process() -> Result<Self, ConfigError> {
        Self::parse(env::args().skip(1), env::var_os(CONFIG_PATH_ENV))
    }

    pub fn parse<I, S>(args: I, env_path: Option<std::ffi::OsString>) -> Result<Self, ConfigError>
    where
        I: IntoIterator<Item = S>,
        S: Into<String>,
    {
        let mut config_path = env_path.map(PathBuf::from);
        let mut print_artifact_metadata = false;
        let mut args = args.into_iter().map(Into::into);
        while let Some(argument) = args.next() {
            match argument.as_str() {
                "--config" => {
                    let value = args.next().ok_or(ConfigError::MissingConfigArgument)?;
                    config_path = Some(PathBuf::from(value));
                }
                "--print-artifact-metadata" => print_artifact_metadata = true,
                "--help" | "-h" => return Err(ConfigError::HelpRequested),
                unknown => return Err(ConfigError::UnknownArgument(unknown.to_owned())),
            }
        }
        let config_path = config_path.ok_or(ConfigError::MissingConfigPath)?;
        Ok(Self {
            config_path,
            print_artifact_metadata,
        })
    }

    #[must_use]
    pub fn usage(artifact: &str) -> String {
        format!(
            "Usage: {artifact} --config <path> [--print-artifact-metadata]\n       LIQI_CONFIG_PATH=<path> {artifact}"
        )
    }
}

#[async_trait]
pub trait SecretResolver: Send + Sync {
    async fn resolve(&self, reference: &SecretReference) -> Result<SecretString, SecretError>;
}

#[derive(Debug, Default)]
pub struct LocalSecretResolver;

#[async_trait]
impl SecretResolver for LocalSecretResolver {
    async fn resolve(&self, reference: &SecretReference) -> Result<SecretString, SecretError> {
        match reference.scheme() {
            "env" => {
                let name = reference.locator();
                let value = env::var(name).map_err(|_| SecretError::Unavailable("env"))?;
                if value.is_empty() {
                    return Err(SecretError::Unavailable("env"));
                }
                Ok(SecretString::from(value))
            }
            "file" => {
                let path = Path::new(reference.locator());
                if !path.is_absolute() {
                    return Err(SecretError::RelativeFilePath);
                }
                let value =
                    fs::read_to_string(path).map_err(|_| SecretError::Unavailable("file"))?;
                let value = value
                    .trim_end_matches(|character| character == '\r' || character == '\n')
                    .to_owned();
                if value.is_empty() {
                    return Err(SecretError::Unavailable("file"));
                }
                Ok(SecretString::from(value))
            }
            "oci-vault" => Err(SecretError::ProviderNotIntegrated("oci-vault")),
            _ => Err(SecretError::ProviderNotIntegrated("unknown")),
        }
    }
}

#[derive(Debug, Error)]
pub enum ConfigError {
    #[error("runtime configuration could not be read")]
    Read {
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },
    #[error("runtime configuration is larger than the 1 MiB bound: {0} bytes")]
    TooLarge(u64),
    #[error("runtime configuration is not valid JSON")]
    Parse(#[source] serde_json::Error),
    #[error("runtime configuration schema version is unsupported: {0}")]
    UnsupportedSchemaVersion(String),
    #[error("runtime configuration targets {actual:?}, expected {expected:?}")]
    WrongService {
        expected: ServiceName,
        actual: ServiceName,
    },
    #[error("runtime configuration contains an invalid value: {0}")]
    InvalidValue(&'static str),
    #[error("runtime listen address is invalid")]
    InvalidListenAddress,
    #[error("secret reference must use a supported scheme and non-empty locator")]
    InvalidSecretReference,
    #[error("secret reference scheme is unsupported: {0}")]
    UnsupportedSecretScheme(String),
    #[error("staging and production database secrets cannot use env:// references")]
    ProductionEnvironmentSecret,
    #[error("staging and production cannot enable fake persistence")]
    ProductionFakePersistence,
    #[error("--config requires a path")]
    MissingConfigArgument,
    #[error("configuration path is required through --config or LIQI_CONFIG_PATH")]
    MissingConfigPath,
    #[error("unknown runtime argument: {0}")]
    UnknownArgument(String),
    #[error("help requested")]
    HelpRequested,
}

#[derive(Debug, Error)]
pub enum SecretError {
    #[error("secret is unavailable from {0} provider")]
    Unavailable(&'static str),
    #[error("file secret references must use an absolute path")]
    RelativeFilePath,
    #[error("secret provider is not integrated: {0}")]
    ProviderNotIntegrated(&'static str),
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn secret_reference_debug_redacts_locator() {
        let reference = SecretReference::parse("env://VERY_PRIVATE_NAME")
            .unwrap_or_else(|error| unreachable!("valid reference unexpectedly failed: {error}"));
        let rendered = format!("{reference:?}");
        assert!(rendered.contains("env"));
        assert!(!rendered.contains("VERY_PRIVATE_NAME"));
    }

    #[test]
    fn missing_config_path_fails_closed() {
        let result = RuntimeArgs::parse(Vec::<String>::new(), None);
        assert!(matches!(result, Err(ConfigError::MissingConfigPath)));
    }

    #[tokio::test]
    async fn missing_secret_reference_fails_closed() {
        let reference =
            SecretReference::parse("env://LIQI_TEST_SECRET_THAT_MUST_NOT_EXIST_7F274344")
                .unwrap_or_else(|error| unreachable!("test secret reference must parse: {error}"));
        let resolver = LocalSecretResolver;
        let result = resolver.resolve(&reference).await;
        assert!(matches!(result, Err(SecretError::Unavailable("env"))));
    }

    #[test]
    fn production_rejects_fake_persistence() {
        let mut config: RuntimeConfig = serde_json::from_str(include_str!(
            "../../../contracts/platform/runtime-config-api.local.example.json"
        ))
        .unwrap_or_else(|error| unreachable!("checked example must parse: {error}"));
        config.environment = Environment::Production;
        config.database.secret_ref =
            SecretReference::parse("file:///run/liqi/secrets/liqi-api/database-password")
                .unwrap_or_else(|error| {
                    unreachable!("production file reference must parse: {error}")
                });
        let result = config.validate(ServiceName::LiqiApi);
        assert!(matches!(
            result,
            Err(ConfigError::ProductionFakePersistence)
        ));
    }

    #[test]
    fn production_rejects_environment_database_secret() {
        let config: RuntimeConfig = serde_json::from_str(include_str!(
            "../../../contracts/platform/runtime-config-api.local.example.json"
        ))
        .unwrap_or_else(|error| unreachable!("checked example must parse: {error}"));
        let mut production = config;
        production.environment = Environment::Production;
        let result = production.validate(ServiceName::LiqiApi);
        assert!(matches!(
            result,
            Err(ConfigError::ProductionEnvironmentSecret)
        ));
    }
}
