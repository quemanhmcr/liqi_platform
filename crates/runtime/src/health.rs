use liqi_application::{HealthRegistry, PlatformPersistence};
use liqi_configuration::RuntimeConfig;
use liqi_protocol::DependencyStatus;
use serde::Deserialize;
use std::{path::Path, sync::Arc, time::Duration};
use tokio::{fs, time};
use tokio_util::sync::CancellationToken;

const MAX_HOST_READINESS_BYTES: u64 = 65_536;
const READINESS_REFRESH_INTERVAL: Duration = Duration::from_secs(1);

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HostReadinessStatus {
    pub ready: bool,
    pub detail: &'static str,
}

#[derive(Debug, Deserialize)]
struct HostReadinessDocument {
    #[serde(rename = "schema")]
    schema: String,
    status: String,
}

pub async fn read_host_readiness(path: &Path, expected_schema: &str) -> HostReadinessStatus {
    let metadata = match fs::metadata(path).await {
        Ok(metadata) => metadata,
        Err(_) => {
            return HostReadinessStatus {
                ready: false,
                detail: "host-readiness-unavailable",
            };
        }
    };
    if metadata.len() > MAX_HOST_READINESS_BYTES {
        return HostReadinessStatus {
            ready: false,
            detail: "host-readiness-oversized",
        };
    }
    let bytes = match fs::read(path).await {
        Ok(bytes) => bytes,
        Err(_) => {
            return HostReadinessStatus {
                ready: false,
                detail: "host-readiness-unreadable",
            };
        }
    };
    let document: HostReadinessDocument = match serde_json::from_slice(&bytes) {
        Ok(document) => document,
        Err(_) => {
            return HostReadinessStatus {
                ready: false,
                detail: "host-readiness-invalid",
            };
        }
    };
    if document.schema != expected_schema {
        return HostReadinessStatus {
            ready: false,
            detail: "host-readiness-schema-mismatch",
        };
    }
    if document.status != "ready" {
        return HostReadinessStatus {
            ready: false,
            detail: "host-not-ready",
        };
    }
    HostReadinessStatus {
        ready: true,
        detail: "host-ready",
    }
}

pub async fn refresh_readiness(
    health: Arc<HealthRegistry>,
    config: Arc<RuntimeConfig>,
    persistence: Arc<dyn PlatformPersistence>,
    cancellation: CancellationToken,
) {
    let mut interval = time::interval(READINESS_REFRESH_INTERVAL);
    interval.set_missed_tick_behavior(time::MissedTickBehavior::Skip);
    loop {
        tokio::select! {
            () = cancellation.cancelled() => break,
            _ = interval.tick() => {
                let host = read_host_readiness(
                    &config.host.readiness_file,
                    &config.host.readiness_schema_version,
                ).await;
                let _ = health.set_check(
                    "host",
                    if host.ready { DependencyStatus::Up } else { DependencyStatus::Down },
                    Some(host.detail),
                ).await;
                match persistence.readiness().await {
                    Ok(readiness) => {
                        let _ = health.set_check(
                            "database",
                            if readiness.database_reachable { DependencyStatus::Up } else { DependencyStatus::Down },
                            Some(if readiness.database_reachable { "database-reachable" } else { "database-unavailable" }),
                        ).await;
                        let _ = health.set_check(
                            "migration",
                            if readiness.migration_ready { DependencyStatus::Up } else { DependencyStatus::Down },
                            Some(if readiness.migration_ready { "migration-ready" } else { "migration-not-ready" }),
                        ).await;
                    }
                    Err(_) => {
                        let _ = health.set_check("database", DependencyStatus::Down, Some("database-probe-failed")).await;
                        let _ = health.set_check("migration", DependencyStatus::Unknown, Some("migration-unknown")).await;
                    }
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{fs as std_fs, process};

    #[tokio::test]
    async fn consumes_senior_one_host_readiness_contract() {
        let path = std::env::temp_dir().join(format!(
            "liqi-host-ready-{}-{}.json",
            process::id(),
            time::Instant::now().elapsed().as_nanos()
        ));
        let document = r#"{
          "schema": "liqi.platform.host-readiness/v0",
          "status": "ready",
          "host": {"hostname": "test"},
          "checks": {}
        }"#;
        std_fs::write(&path, document)
            .unwrap_or_else(|error| unreachable!("host readiness fixture must write: {error}"));
        let status = read_host_readiness(&path, "liqi.platform.host-readiness/v0").await;
        let _ = std_fs::remove_file(&path);
        assert!(status.ready);
        assert_eq!(status.detail, "host-ready");
    }

    #[tokio::test]
    async fn malformed_or_wrong_schema_host_readiness_fails_closed() {
        let path = std::env::temp_dir().join(format!("liqi-host-not-ready-{}.json", process::id()));
        std_fs::write(&path, r#"{"schema":"wrong","status":"ready"}"#)
            .unwrap_or_else(|error| unreachable!("host readiness fixture must write: {error}"));
        let status = read_host_readiness(&path, "liqi.platform.host-readiness/v0").await;
        let _ = std_fs::remove_file(&path);
        assert!(!status.ready);
        assert_eq!(status.detail, "host-readiness-schema-mismatch");
    }
}
