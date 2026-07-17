use serde::{Deserialize, Serialize};

use crate::{
    ERROR_MODEL_VERSION, EVENT_ENVELOPE_VERSION, PLATFORM_API_VERSION, REALTIME_PROTOCOL_VERSION,
    RUNTIME_CONFIG_VERSION, RUST_TOOLCHAIN_VERSION,
};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ContractVersions {
    pub platform_api: String,
    pub error_model: String,
    pub event_envelope: String,
    pub realtime: String,
    pub runtime_config: String,
}

impl Default for ContractVersions {
    fn default() -> Self {
        Self {
            platform_api: PLATFORM_API_VERSION.to_owned(),
            error_model: ERROR_MODEL_VERSION.to_owned(),
            event_envelope: EVENT_ENVELOPE_VERSION.to_owned(),
            realtime: REALTIME_PROTOCOL_VERSION.to_owned(),
            runtime_config: RUNTIME_CONFIG_VERSION.to_owned(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ArtifactMetadata {
    pub artifact: String,
    pub version: String,
    pub source_revision: Option<String>,
    pub built_at: Option<String>,
    pub rust_toolchain: String,
    pub contract_versions: ContractVersions,
}

impl ArtifactMetadata {
    #[must_use]
    pub fn current(artifact: &str, version: &str) -> Self {
        Self {
            artifact: artifact.to_owned(),
            version: version.to_owned(),
            source_revision: option_env!("LIQI_SOURCE_REVISION").map(str::to_owned),
            built_at: option_env!("LIQI_BUILT_AT").map(str::to_owned),
            rust_toolchain: RUST_TOOLCHAIN_VERSION.to_owned(),
            contract_versions: ContractVersions::default(),
        }
    }
}
