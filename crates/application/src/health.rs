use liqi_protocol::{DependencyCheck, DependencyStatus, HealthResponse, HealthStatus};
use std::{
    collections::BTreeMap,
    sync::atomic::{AtomicBool, Ordering},
};
use tokio::sync::RwLock;

const MAX_HEALTH_CHECKS: usize = 16;
const MAX_DETAIL_CHARS: usize = 128;

#[derive(Debug)]
pub struct HealthRegistry {
    service: String,
    version: String,
    draining: AtomicBool,
    checks: RwLock<BTreeMap<String, DependencyCheck>>,
}

impl HealthRegistry {
    #[must_use]
    pub fn new(service: impl Into<String>, version: impl Into<String>) -> Self {
        Self {
            service: service.into(),
            version: version.into(),
            draining: AtomicBool::new(false),
            checks: RwLock::new(BTreeMap::new()),
        }
    }

    #[must_use]
    pub fn liveness(&self) -> HealthResponse {
        HealthResponse {
            status: if self.draining.load(Ordering::Acquire) {
                HealthStatus::Draining
            } else {
                HealthStatus::Live
            },
            service: self.service.clone(),
            version: self.version.clone(),
            checks: Vec::new(),
        }
    }

    pub async fn readiness(&self) -> HealthResponse {
        let checks: Vec<_> = self.checks.read().await.values().cloned().collect();
        let status = if self.draining.load(Ordering::Acquire) {
            HealthStatus::Draining
        } else if !checks.is_empty()
            && checks
                .iter()
                .all(|check| check.status == DependencyStatus::Up)
        {
            HealthStatus::Ready
        } else {
            HealthStatus::NotReady
        };
        HealthResponse {
            status,
            service: self.service.clone(),
            version: self.version.clone(),
            checks,
        }
    }

    pub async fn set_check(
        &self,
        name: &str,
        status: DependencyStatus,
        detail: Option<&str>,
    ) -> bool {
        let mut checks = self.checks.write().await;
        if !checks.contains_key(name) && checks.len() >= MAX_HEALTH_CHECKS {
            return false;
        }
        checks.insert(
            name.chars().take(64).collect(),
            DependencyCheck {
                name: name.chars().take(64).collect(),
                status,
                detail: detail.map(|value| value.chars().take(MAX_DETAIL_CHARS).collect()),
            },
        );
        true
    }

    pub fn mark_draining(&self) {
        self.draining.store(true, Ordering::Release);
    }

    #[must_use]
    pub fn is_draining(&self) -> bool {
        self.draining.load(Ordering::Acquire)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn liveness_stays_live_while_readiness_is_not_ready() {
        let health = HealthRegistry::new("liqi-api", "test");
        let _ = health
            .set_check(
                "database",
                DependencyStatus::Down,
                Some("database-unavailable"),
            )
            .await;
        assert_eq!(health.liveness().status, HealthStatus::Live);
        assert_eq!(health.readiness().await.status, HealthStatus::NotReady);
    }

    #[tokio::test]
    async fn draining_overrides_dependency_readiness() {
        let health = HealthRegistry::new("liqi-api", "test");
        let _ = health
            .set_check("database", DependencyStatus::Up, Some("database-ready"))
            .await;
        assert_eq!(health.readiness().await.status, HealthStatus::Ready);
        health.mark_draining();
        assert_eq!(health.liveness().status, HealthStatus::Draining);
        assert_eq!(health.readiness().await.status, HealthStatus::Draining);
    }
}
