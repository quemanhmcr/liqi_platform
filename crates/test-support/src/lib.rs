#![forbid(unsafe_code)]

#[cfg(any(test, feature = "dev-fakes"))]
mod fake_platform;

#[cfg(any(test, feature = "dev-fakes"))]
pub use fake_platform::FakePlatformStore;
