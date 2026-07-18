#![forbid(unsafe_code)]

use liqi_sequence_diff_core::{
    INPUT_ENCODING, KERNEL_NAME, KERNEL_VERSION, MAX_INPUT_BYTES, MAX_NATIVE_OUTPUT_BYTES,
    MAX_OBSERVED_SEQUENCES, MAX_OUTPUT_RANGES, MAX_WINDOW_SPAN, SequenceDiffError,
    compact_sequence_diff,
};
use rustler::{Binary, NifMap, NifTaggedEnum};
use std::panic::{AssertUnwindSafe, catch_unwind};

const NIF_ABI: &str = "2.15";
const SCHEDULER_CLASS: &str = "regular";

#[derive(Debug, NifMap)]
struct NativeRange {
    first: u64,
    last: u64,
}

#[derive(Debug, NifMap)]
struct NativeSequenceDiff {
    missing_ranges: Vec<NativeRange>,
    observed_count: u64,
    unique_count: u64,
    duplicate_count: u64,
}

#[derive(Debug, NifMap)]
struct NativeError {
    code: String,
    retryable: bool,
    detail: String,
}

#[derive(Debug, NifMap)]
struct NativeKernelInfo {
    kernel: String,
    kernel_version: String,
    nif_abi: String,
    scheduler_class: String,
    input_encoding: String,
    target_arch: String,
    target_os: String,
    max_input_bytes: u64,
    max_observed_sequences: u64,
    max_window_span: u64,
    max_output_ranges: u64,
    max_native_output_bytes: u64,
}

#[derive(Debug, NifTaggedEnum)]
enum NativeReply<T> {
    Ok(T),
    Error(NativeError),
}

#[rustler::nif(name = "compact_sequence_diff_v1", schedule = "Normal")]
fn compact_sequence_diff_v1(
    expected_first: u64,
    expected_last: u64,
    observed_big_endian: Binary<'_>,
) -> NativeReply<NativeSequenceDiff> {
    guarded(|| {
        compact_sequence_diff(
            expected_first,
            expected_last,
            observed_big_endian.as_slice(),
        )
        .map(to_native_diff)
        .map_err(|error| to_native_error(&error))
    })
}

#[rustler::nif(name = "kernel_info_v1", schedule = "Normal")]
fn kernel_info_v1() -> NativeKernelInfo {
    NativeKernelInfo {
        kernel: KERNEL_NAME.to_owned(),
        kernel_version: KERNEL_VERSION.to_owned(),
        nif_abi: NIF_ABI.to_owned(),
        scheduler_class: SCHEDULER_CLASS.to_owned(),
        input_encoding: INPUT_ENCODING.to_owned(),
        target_arch: std::env::consts::ARCH.to_owned(),
        target_os: std::env::consts::OS.to_owned(),
        max_input_bytes: usize_to_u64(MAX_INPUT_BYTES),
        max_observed_sequences: usize_to_u64(MAX_OBSERVED_SEQUENCES),
        max_window_span: MAX_WINDOW_SPAN,
        max_output_ranges: usize_to_u64(MAX_OUTPUT_RANGES),
        max_native_output_bytes: usize_to_u64(MAX_NATIVE_OUTPUT_BYTES),
    }
}

fn guarded<T, F>(operation: F) -> NativeReply<T>
where
    F: FnOnce() -> Result<T, NativeError>,
{
    match catch_unwind(AssertUnwindSafe(operation)) {
        Ok(Ok(value)) => NativeReply::Ok(value),
        Ok(Err(error)) => NativeReply::Error(error),
        Err(_) => NativeReply::Error(NativeError {
            code: "NATIVE_PANIC".to_owned(),
            retryable: false,
            detail: "native_kernel_panicked".to_owned(),
        }),
    }
}

fn to_native_diff(diff: liqi_sequence_diff_core::SequenceDiff) -> NativeSequenceDiff {
    NativeSequenceDiff {
        missing_ranges: diff
            .missing_ranges
            .into_iter()
            .map(|range| NativeRange {
                first: range.first,
                last: range.last,
            })
            .collect(),
        observed_count: usize_to_u64(diff.observed_count),
        unique_count: usize_to_u64(diff.unique_count),
        duplicate_count: usize_to_u64(diff.duplicate_count),
    }
}

fn to_native_error(error: &SequenceDiffError) -> NativeError {
    NativeError {
        code: error.code().to_owned(),
        retryable: false,
        detail: error.detail().to_owned(),
    }
}

const fn usize_to_u64(value: usize) -> u64 {
    value as u64
}

rustler::init!("Elixir.Liqi.Native.SequenceDiff.Nif");

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn panic_is_mapped_to_stable_error() {
        let reply = guarded::<(), _>(|| {
            assert!(std::hint::black_box(false), "synthetic panic");
            Ok(())
        });
        assert!(matches!(
            reply,
            NativeReply::Error(NativeError { code, retryable: false, detail })
                if code == "NATIVE_PANIC" && detail == "native_kernel_panicked"
        ));
    }

    #[test]
    fn validation_error_is_not_retryable() {
        let reply = guarded(|| {
            compact_sequence_diff(2, 1, &[])
                .map(to_native_diff)
                .map_err(|error| to_native_error(&error))
        });
        assert!(matches!(
            reply,
            NativeReply::Error(NativeError { code, retryable: false, .. })
                if code == "NATIVE_INVALID_WINDOW"
        ));
    }
}
