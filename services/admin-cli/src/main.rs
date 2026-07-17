#![forbid(unsafe_code)]

mod platform_probe;

use clap::{Parser, Subcommand, ValueEnum};
use jsonschema::{Retrieve, Uri};
use liqi_configuration::{RuntimeConfig, ServiceName};
use liqi_protocol::{ClientFrame, ERROR_CODE_REGISTRY, ServerFrame};
use platform_probe::{PlatformProbeExit, PlatformProbeOptions};
use serde::Serialize;
use serde_json::Value;
use std::{
    collections::{BTreeMap, BTreeSet},
    error::Error,
    fs,
    io::{self, Write as _},
    path::{Path, PathBuf},
    time::Duration,
};
use thiserror::Error;

const MAX_CONTRACT_BYTES: u64 = 4 * 1024 * 1024;

#[derive(Debug, Parser)]
#[command(
    name = "liqi-platform-tool",
    version,
    about = "Stable V0 platform validation interface"
)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Debug, Subcommand)]
enum Command {
    /// Validate all Senior 3 machine-readable contracts and checked examples.
    ValidateContracts {
        #[arg(long, default_value = ".")]
        root: PathBuf,
    },
    /// Validate one runtime configuration without resolving its secret.
    ValidateRuntimeConfig {
        #[arg(long)]
        service: ServiceArgument,
        #[arg(long)]
        config: PathBuf,
    },
    /// Run the provider-owned end-to-end platform promotion probe.
    PlatformProbe {
        #[arg(long)]
        output: PathBuf,
        #[arg(long, default_value = "http://127.0.0.1:8080")]
        api_base_url: String,
        #[arg(long, default_value = "http://127.0.0.1:8081")]
        realtime_base_url: String,
        #[arg(long, default_value = "ws://127.0.0.1:8081/platform/v0/realtime")]
        realtime_ws_url: String,
        #[arg(long, default_value = "http://127.0.0.1:8082")]
        worker_base_url: String,
        #[arg(long, default_value_t = 10)]
        timeout_seconds: u64,
    },
    /// Print stable commands and runtime health/artifact interfaces for CI/release consumers.
    PrintValidationManifest,
}

#[derive(Debug, Clone, Copy, ValueEnum)]
enum ServiceArgument {
    #[value(name = "liqi-api")]
    Api,
    #[value(name = "liqi-realtime")]
    Realtime,
    #[value(name = "liqi-worker")]
    Worker,
}

impl From<ServiceArgument> for ServiceName {
    fn from(value: ServiceArgument) -> Self {
        match value {
            ServiceArgument::Api => Self::LiqiApi,
            ServiceArgument::Realtime => Self::LiqiRealtime,
            ServiceArgument::Worker => Self::LiqiWorker,
        }
    }
}

#[tokio::main]
async fn main() -> Result<(), ToolError> {
    let cli = Cli::parse();
    match cli.command {
        Command::ValidateContracts { root } => {
            let report = validate_contracts(&root)?;
            write_json(&report)?;
        }
        Command::ValidateRuntimeConfig { service, config } => {
            let config = RuntimeConfig::load(config, service.into())?;
            write_json(&RuntimeConfigReport {
                valid: true,
                schema_version: config.schema_version,
                service: config.service.name.artifact_name().to_owned(),
                environment: format!("{:?}", config.environment).to_ascii_lowercase(),
                secret_scheme: config.database.secret_ref.scheme().to_owned(),
                required_migration_version: config.database.required_migration_version,
            })?;
        }
        Command::PlatformProbe {
            output,
            api_base_url,
            realtime_base_url,
            realtime_ws_url,
            worker_base_url,
            timeout_seconds,
        } => {
            if !(1..=15).contains(&timeout_seconds) {
                return Err(ToolError::InvalidProbeTimeout);
            }
            let result = platform_probe::run(PlatformProbeOptions {
                output: output.clone(),
                api_base_url,
                realtime_base_url,
                realtime_ws_url,
                worker_base_url,
                timeout: Duration::from_secs(timeout_seconds),
            })
            .await?;
            if result == PlatformProbeExit::Failed {
                return Err(ToolError::PlatformProbeFailed(output));
            }
        }
        Command::PrintValidationManifest => write_json(&ValidationManifest::v0())?,
    }
    Ok(())
}

fn validate_contracts(root: &Path) -> Result<ValidationReport, ToolError> {
    let contracts = root.join("contracts");
    let error_schema_path = contracts.join("errors/error-model-v0.schema.json");
    let event_schema_path = contracts.join("events/event-envelope-v0.schema.json");
    let runtime_schema_path = contracts.join("platform/runtime-config-v0.schema.json");
    let realtime_schema_path = contracts.join("realtime/realtime-v0.schema.json");

    let error_schema = read_json(&error_schema_path)?;
    let event_schema = read_json(&event_schema_path)?;
    let runtime_schema = read_json(&runtime_schema_path)?;
    let realtime_schema = read_json(&realtime_schema_path)?;
    for (name, schema) in [
        ("error-model-v0", &error_schema),
        ("event-envelope-v0", &event_schema),
        ("runtime-config-v0", &runtime_schema),
        ("realtime-v0", &realtime_schema),
    ] {
        jsonschema::meta::validate(schema)
            .map_err(|error| ToolError::InvalidSchema(name.to_owned(), error.to_string()))?;
    }

    let runtime_validator = validator(&runtime_schema, ContractRetriever::default())?;
    let mut config_examples = 0_usize;
    for (path, service) in [
        (
            contracts.join("platform/runtime-config-api.local.example.json"),
            ServiceName::LiqiApi,
        ),
        (
            contracts.join("platform/runtime-config-realtime.local.example.json"),
            ServiceName::LiqiRealtime,
        ),
        (
            contracts.join("platform/runtime-config-worker.local.example.json"),
            ServiceName::LiqiWorker,
        ),
    ] {
        let value = read_json(&path)?;
        validate_instance(&runtime_validator, &value, &path)?;
        let _ = RuntimeConfig::load(&path, service)?;
        config_examples += 1;
    }

    let error_validator = validator(&error_schema, ContractRetriever::default())?;
    let error_example_path = contracts.join("errors/examples/internal-error-v0.json");
    validate_instance(
        &error_validator,
        &read_json(&error_example_path)?,
        &error_example_path,
    )?;

    let event_validator = validator(&event_schema, ContractRetriever::default())?;
    let event_example_path = contracts.join("events/examples/platform-probe-requested-v0.json");
    validate_instance(
        &event_validator,
        &read_json(&event_example_path)?,
        &event_example_path,
    )?;

    let retriever = ContractRetriever::from_schemas([error_schema.clone(), event_schema.clone()])?;
    let realtime_validator = validator(&realtime_schema, retriever)?;
    let realtime_examples = contracts.join("realtime/examples");
    let mut realtime_example_count = 0_usize;
    for path in sorted_json_files(&realtime_examples)? {
        let value = read_json(&path)?;
        validate_instance(&realtime_validator, &value, &path)?;
        match value.get("connectionId") {
            Some(_) => {
                serde_json::from_value::<ServerFrame>(value)
                    .map_err(|error| ToolError::TypedProtocol(path.clone(), error))?;
            }
            None => {
                serde_json::from_value::<ClientFrame>(value)
                    .map_err(|error| ToolError::TypedProtocol(path.clone(), error))?;
            }
        }
        realtime_example_count += 1;
    }

    validate_error_registry(&contracts.join("errors/error-codes-v0.json"))?;
    validate_openapi(&contracts.join("openapi/platform-v0.yaml"))?;
    let provider_declarations = validate_provider_declarations(&contracts)?;
    scan_for_embedded_secrets(root)?;

    Ok(ValidationReport {
        valid: true,
        schemas: 4,
        config_examples,
        error_examples: 1,
        event_examples: 1,
        realtime_examples: realtime_example_count,
        openapi_version: "3.1.0",
        error_codes: ERROR_CODE_REGISTRY.len(),
        provider_declarations,
        secret_scan: "clean",
    })
}

fn validator(
    schema: &Value,
    retriever: ContractRetriever,
) -> Result<jsonschema::Validator, ToolError> {
    jsonschema::draft202012::options()
        .with_retriever(retriever)
        .should_validate_formats(true)
        .build(schema)
        .map_err(|error| ToolError::ValidatorBuild(error.to_string()))
}

fn validate_instance(
    validator: &jsonschema::Validator,
    instance: &Value,
    path: &Path,
) -> Result<(), ToolError> {
    validator
        .validate(instance)
        .map_err(|error| ToolError::InvalidInstance(path.to_path_buf(), error.to_string()))
}

fn validate_error_registry(path: &Path) -> Result<(), ToolError> {
    let value = read_json(path)?;
    let codes = value
        .get("codes")
        .and_then(Value::as_array)
        .ok_or_else(|| ToolError::ContractShape(path.to_path_buf(), "codes array is missing"))?;
    let mut declared = BTreeMap::<String, (u16, bool)>::new();
    for entry in codes {
        let code = entry
            .get("code")
            .and_then(Value::as_str)
            .ok_or_else(|| ToolError::ContractShape(path.to_path_buf(), "error code is missing"))?
            .to_owned();
        let status = entry
            .get("httpStatus")
            .or_else(|| entry.get("status"))
            .and_then(Value::as_u64)
            .and_then(|value| u16::try_from(value).ok())
            .ok_or_else(|| {
                ToolError::ContractShape(path.to_path_buf(), "HTTP status is missing")
            })?;
        let retryable = entry
            .get("retryable")
            .and_then(Value::as_bool)
            .ok_or_else(|| {
                ToolError::ContractShape(path.to_path_buf(), "retryable flag is missing")
            })?;
        if declared.insert(code, (status, retryable)).is_some() {
            return Err(ToolError::DuplicateErrorCode);
        }
    }
    let implemented = ERROR_CODE_REGISTRY
        .iter()
        .map(|code| {
            (
                code.as_str().to_owned(),
                (code.status().as_u16(), code.retryable()),
            )
        })
        .collect::<BTreeMap<_, _>>();
    let declared_codes = declared.keys().cloned().collect::<BTreeSet<_>>();
    let implemented_codes = implemented.keys().cloned().collect::<BTreeSet<_>>();
    if declared_codes != implemented_codes {
        return Err(ToolError::ErrorRegistryMismatch {
            declared: declared_codes,
            implemented: implemented_codes,
        });
    }
    if declared != implemented {
        return Err(ToolError::ErrorRegistrySemanticsMismatch {
            declared,
            implemented,
        });
    }
    Ok(())
}

fn validate_openapi(path: &Path) -> Result<(), ToolError> {
    let text = read_bounded(path)?;
    let document = oas3::from_yaml(&text)
        .map_err(|error| ToolError::OpenApi(path.to_path_buf(), error.to_string()))?;
    let version = document
        .validate_version()
        .map_err(|error| ToolError::OpenApi(path.to_path_buf(), error.to_string()))?;
    if version.major != 3 || version.minor != 1 {
        return Err(ToolError::OpenApi(
            path.to_path_buf(),
            format!("OpenAPI 3.1.x is required, found {version}"),
        ));
    }
    let value = serde_json::to_value(&document)
        .map_err(|error| ToolError::OpenApi(path.to_path_buf(), error.to_string()))?;
    for pointer in [
        "/paths/~1health~1live/get",
        "/paths/~1health~1ready/get",
        "/paths/~1health~1platform/get",
        "/paths/~1metrics/get",
        "/paths/~1platform~1v0~1metadata/get",
        "/paths/~1platform~1v0~1probes/post",
        "/components/schemas/ArtifactMetadata",
        "/components/schemas/CreateProbeRequest",
        "/components/schemas/CreateProbeResponse",
    ] {
        if value.pointer(pointer).is_none() {
            return Err(ToolError::OpenApi(
                path.to_path_buf(),
                format!("required OpenAPI node is missing: {pointer}"),
            ));
        }
    }
    Ok(())
}

fn validate_provider_declarations(contracts: &Path) -> Result<usize, ToolError> {
    let capacity_path = contracts.join("platform/runtime-capacity-budget-v0.json");
    let telemetry_paths = [
        (
            "liqi-api",
            contracts.join("platform/runtime-telemetry-api-v0.json"),
        ),
        (
            "liqi-realtime",
            contracts.join("platform/runtime-telemetry-realtime-v0.json"),
        ),
        (
            "liqi-worker",
            contracts.join("platform/runtime-telemetry-worker-v0.json"),
        ),
    ];
    let capacity = read_json(&capacity_path)?;
    if capacity.get("schema_version").and_then(Value::as_str) != Some("capacity-budget-v0")
        || capacity.get("provider").and_then(Value::as_str) != Some("runtime")
        || capacity.get("owner").and_then(Value::as_str) != Some("Senior 3")
    {
        return Err(ToolError::ContractShape(
            capacity_path,
            "runtime capacity provider identity is invalid",
        ));
    }

    let capacity_schema_path = contracts.join("operations/capacity-budget-v0.schema.json");
    if capacity_schema_path.is_file() {
        let capacity_schema = read_json(&capacity_schema_path)?;
        jsonschema::meta::validate(&capacity_schema).map_err(|error| {
            ToolError::InvalidSchema("capacity-budget-v0".to_owned(), error.to_string())
        })?;
        validate_instance(
            &validator(&capacity_schema, ContractRetriever::default())?,
            &capacity,
            &capacity_path,
        )?;
    }

    let telemetry_schema_path = contracts.join("operations/telemetry-v0.schema.json");
    let telemetry_validator = if telemetry_schema_path.is_file() {
        let schema = read_json(&telemetry_schema_path)?;
        jsonschema::meta::validate(&schema).map_err(|error| {
            ToolError::InvalidSchema("telemetry-v0".to_owned(), error.to_string())
        })?;
        Some(validator(&schema, ContractRetriever::default())?)
    } else {
        None
    };
    for (expected_service, path) in telemetry_paths {
        let document = read_json(&path)?;
        let service = document.get("service").and_then(Value::as_object);
        if document.get("schema_version").and_then(Value::as_str) != Some("telemetry-v0")
            || service
                .and_then(|value| value.get("name"))
                .and_then(Value::as_str)
                != Some(expected_service)
            || service
                .and_then(|value| value.get("owner"))
                .and_then(Value::as_str)
                != Some("Senior 3")
        {
            return Err(ToolError::ContractShape(
                path,
                "runtime telemetry provider identity is invalid",
            ));
        }
        if let Some(validator) = &telemetry_validator {
            validate_instance(validator, &document, &path)?;
        }
    }
    Ok(4)
}

fn scan_for_embedded_secrets(root: &Path) -> Result<(), ToolError> {
    let roots = [
        root.join("Cargo.toml"),
        root.join("rust-toolchain.toml"),
        root.join("crates"),
        root.join("services"),
        root.join("contracts"),
        root.join("docs/adr"),
    ];
    let forbidden = [
        ["-----BEGIN ", "PRIVATE KEY-----"].concat(),
        ["-----BEGIN RSA ", "PRIVATE KEY-----"].concat(),
        ["postgres", "://"].concat(),
        ["postgresql", "://"].concat(),
        ["ocid1", ".tenancy."].concat(),
        ["ocid1", ".user."].concat(),
        ["ocid1", ".key."].concat(),
    ];
    for root in roots {
        for path in text_files(&root)? {
            let content = read_bounded(&path)?;
            if let Some(pattern) = forbidden
                .iter()
                .find(|pattern| content.contains(pattern.as_str()))
            {
                return Err(ToolError::EmbeddedSecret(path, pattern.clone()));
            }
        }
    }
    Ok(())
}

fn read_json(path: &Path) -> Result<Value, ToolError> {
    let text = read_bounded(path)?;
    serde_json::from_str(&text).map_err(|error| ToolError::Json(path.to_path_buf(), error))
}

fn read_bounded(path: &Path) -> Result<String, ToolError> {
    let metadata =
        fs::metadata(path).map_err(|source| ToolError::Read(path.to_path_buf(), source))?;
    if metadata.len() > MAX_CONTRACT_BYTES {
        return Err(ToolError::FileTooLarge(path.to_path_buf(), metadata.len()));
    }
    fs::read_to_string(path).map_err(|source| ToolError::Read(path.to_path_buf(), source))
}

fn sorted_json_files(directory: &Path) -> Result<Vec<PathBuf>, ToolError> {
    let mut files = fs::read_dir(directory)
        .map_err(|source| ToolError::Read(directory.to_path_buf(), source))?
        .filter_map(Result::ok)
        .map(|entry| entry.path())
        .filter(|path| path.extension().and_then(|value| value.to_str()) == Some("json"))
        .collect::<Vec<_>>();
    files.sort();
    Ok(files)
}

fn text_files(path: &Path) -> Result<Vec<PathBuf>, ToolError> {
    if path.is_file() {
        return Ok(vec![path.to_path_buf()]);
    }
    if !path.exists() {
        return Ok(Vec::new());
    }
    let mut pending = vec![path.to_path_buf()];
    let mut files = Vec::new();
    while let Some(directory) = pending.pop() {
        for entry in
            fs::read_dir(&directory).map_err(|source| ToolError::Read(directory.clone(), source))?
        {
            let entry = entry.map_err(|source| ToolError::Read(directory.clone(), source))?;
            let path = entry.path();
            if path.is_dir() {
                if !matches!(
                    path.file_name().and_then(|value| value.to_str()),
                    Some(".git" | "target")
                ) {
                    pending.push(path);
                }
            } else if matches!(
                path.extension().and_then(|value| value.to_str()),
                Some("rs" | "toml" | "json" | "yaml" | "yml" | "md")
            ) {
                files.push(path);
            }
        }
    }
    files.sort();
    Ok(files)
}

fn write_json<T: Serialize>(value: &T) -> Result<(), ToolError> {
    let stdout = io::stdout();
    let mut locked = stdout.lock();
    serde_json::to_writer_pretty(&mut locked, value).map_err(ToolError::WriteJson)?;
    locked.write_all(b"\n").map_err(ToolError::Write)?;
    locked.flush().map_err(ToolError::Write)
}

#[derive(Debug, Default)]
struct ContractRetriever {
    schemas: BTreeMap<String, Value>,
}

impl ContractRetriever {
    fn from_schemas<const N: usize>(schemas: [Value; N]) -> Result<Self, ToolError> {
        let mut map = BTreeMap::new();
        for schema in schemas {
            let id = schema
                .get("$id")
                .and_then(Value::as_str)
                .ok_or(ToolError::MissingSchemaId)?
                .to_owned();
            map.insert(id, schema);
        }
        Ok(Self { schemas: map })
    }
}

impl Retrieve for ContractRetriever {
    fn retrieve(&self, uri: &Uri<String>) -> Result<Value, Box<dyn Error + Send + Sync>> {
        self.schemas
            .get(uri.as_str())
            .cloned()
            .ok_or_else(|| format!("contract reference is not registered: {uri}").into())
    }
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct ValidationReport {
    valid: bool,
    schemas: usize,
    config_examples: usize,
    error_examples: usize,
    event_examples: usize,
    realtime_examples: usize,
    openapi_version: &'static str,
    error_codes: usize,
    provider_declarations: usize,
    secret_scan: &'static str,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct RuntimeConfigReport {
    valid: bool,
    schema_version: String,
    service: String,
    environment: String,
    secret_scheme: String,
    required_migration_version: u64,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct ValidationManifest {
    schema_version: &'static str,
    contract_command: &'static str,
    workspace_test_command: &'static str,
    workspace_lint_command: &'static str,
    platform_probe_command: &'static str,
    platform_probe_result_schema: &'static str,
    artifacts: [ArtifactInterface; 3],
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct ArtifactInterface {
    name: &'static str,
    target_triple: &'static str,
    default_port: u16,
    steady_cpu_cores: f64,
    hard_cpu_cores: f64,
    hard_memory_mib: u32,
    failure_behavior: &'static str,
    liveness_path: &'static str,
    readiness_path: &'static str,
    platform_health_path: &'static str,
    metrics_path: &'static str,
    metadata_path: &'static str,
    metadata_command: &'static str,
}

impl ValidationManifest {
    fn v0() -> Self {
        Self {
            schema_version: "liqi.platform.runtime-validation/v0",
            contract_command: "cargo +1.97.1 run --locked -p liqi-platform-tool -- validate-contracts --root .",
            workspace_test_command: "cargo +1.97.1 test --workspace --all-targets --all-features --locked",
            workspace_lint_command: "cargo +1.97.1 clippy --workspace --all-targets --all-features --locked -- -D warnings",
            platform_probe_command: "liqi-platform-tool platform-probe --output {output}",
            platform_probe_result_schema: "platform-probe-result-v0",
            artifacts: [
                ArtifactInterface::new("liqi-api", 8080),
                ArtifactInterface::new("liqi-realtime", 8081),
                ArtifactInterface::new("liqi-worker", 8082),
            ],
        }
    }
}

impl ArtifactInterface {
    fn new(name: &'static str, default_port: u16) -> Self {
        let metadata_command = match name {
            "liqi-api" => "liqi-api --config /etc/liqi/api.json --print-artifact-metadata",
            "liqi-realtime" => {
                "liqi-realtime --config /etc/liqi/realtime.json --print-artifact-metadata"
            }
            _ => "liqi-worker --config /etc/liqi/worker.json --print-artifact-metadata",
        };
        let (steady_cpu_cores, hard_cpu_cores, hard_memory_mib, failure_behavior) = match name {
            "liqi-api" => (
                0.25,
                0.45,
                2048,
                "fail-closed-on-secret-database-or-migration-unavailability",
            ),
            "liqi-realtime" => (
                0.35,
                0.65,
                3072,
                "bounded-queue-disconnect-and-not-ready-without-committed-handoff",
            ),
            _ => (
                0.20,
                0.35,
                2048,
                "at-least-once-bounded-retry-and-dead-letter-after-eight-attempts",
            ),
        };
        Self {
            name,
            target_triple: "aarch64-unknown-linux-gnu",
            default_port,
            steady_cpu_cores,
            hard_cpu_cores,
            hard_memory_mib,
            failure_behavior,
            liveness_path: "/health/live",
            readiness_path: "/health/ready",
            platform_health_path: "/health/platform",
            metrics_path: "/metrics",
            metadata_path: "/platform/v0/metadata",
            metadata_command,
        }
    }
}

#[derive(Debug, Error)]
enum ToolError {
    #[error("file could not be read: {0}")]
    Read(PathBuf, #[source] io::Error),
    #[error("file exceeds the 4 MiB validation bound: {0} ({1} bytes)")]
    FileTooLarge(PathBuf, u64),
    #[error("JSON document is invalid: {0}")]
    Json(PathBuf, #[source] serde_json::Error),
    #[error("JSON Schema is invalid: {0}: {1}")]
    InvalidSchema(String, String),
    #[error("JSON Schema validator could not be built: {0}")]
    ValidatorBuild(String),
    #[error("contract example is invalid: {0}: {1}")]
    InvalidInstance(PathBuf, String),
    #[error("typed realtime protocol does not match schema: {0}")]
    TypedProtocol(PathBuf, #[source] serde_json::Error),
    #[error("contract shape is invalid: {0}: {1}")]
    ContractShape(PathBuf, &'static str),
    #[error("error registry contains duplicate codes")]
    DuplicateErrorCode,
    #[error("error registry differs from runtime implementation")]
    ErrorRegistryMismatch {
        declared: BTreeSet<String>,
        implemented: BTreeSet<String>,
    },
    #[error("error registry status/retryability differs from runtime implementation")]
    ErrorRegistrySemanticsMismatch {
        declared: BTreeMap<String, (u16, bool)>,
        implemented: BTreeMap<String, (u16, bool)>,
    },
    #[error("OpenAPI document is invalid: {0}: {1}")]
    OpenApi(PathBuf, String),
    #[error("a contract schema does not declare $id")]
    MissingSchemaId,
    #[error("possible embedded secret found in {0}: {1}")]
    EmbeddedSecret(PathBuf, String),
    #[error("runtime configuration is invalid")]
    RuntimeConfig(#[from] liqi_configuration::ConfigError),
    #[error("platform probe configuration or result failed")]
    PlatformProbe(#[from] platform_probe::PlatformProbeError),
    #[error("platform probe completed with failed evidence: {0}")]
    PlatformProbeFailed(PathBuf),
    #[error("platform probe per-step timeout must be between 1 and 15 seconds")]
    InvalidProbeTimeout,
    #[error("JSON output failed")]
    WriteJson(#[source] serde_json::Error),
    #[error("stdout write failed")]
    Write(#[source] io::Error),
}
