#![forbid(unsafe_code)]

use std::fmt;

pub const KERNEL_NAME: &str = "compact_sequence_diff";
pub const KERNEL_VERSION: &str = "1";
pub const INPUT_ENCODING: &str = "big-endian-u64";
pub const MAX_OBSERVED_SEQUENCES: usize = 2_048;
pub const MAX_INPUT_BYTES: usize = MAX_OBSERVED_SEQUENCES * size_of::<u64>();
pub const MAX_WINDOW_SPAN: u64 = 65_536;
pub const MAX_OUTPUT_RANGES: usize = MAX_OBSERVED_SEQUENCES + 1;
pub const MAX_NATIVE_OUTPUT_BYTES: usize = MAX_OUTPUT_RANGES * size_of::<SequenceRange>();

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SequenceRange {
    pub first: u64,
    pub last: u64,
}

impl SequenceRange {
    #[must_use]
    pub const fn new(first: u64, last: u64) -> Self {
        Self { first, last }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SequenceDiff {
    pub missing_ranges: Vec<SequenceRange>,
    pub observed_count: usize,
    pub unique_count: usize,
    pub duplicate_count: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SequenceDiffError {
    InvalidWindow {
        expected_first: u64,
        expected_last: u64,
    },
    WindowTooLarge {
        actual_span: u64,
        maximum_span: u64,
    },
    InputTooLarge {
        actual_bytes: usize,
        maximum_bytes: usize,
    },
    InvalidEncodingLength {
        actual_bytes: usize,
    },
    SequenceOutOfOrder {
        index: usize,
        previous: u64,
        actual: u64,
    },
    SequenceOutOfWindow {
        index: usize,
        actual: u64,
        expected_first: u64,
        expected_last: u64,
    },
}

impl SequenceDiffError {
    #[must_use]
    pub const fn code(&self) -> &'static str {
        match self {
            Self::InvalidWindow { .. } => "NATIVE_INVALID_WINDOW",
            Self::WindowTooLarge { .. } => "NATIVE_WINDOW_TOO_LARGE",
            Self::InputTooLarge { .. } => "NATIVE_INPUT_TOO_LARGE",
            Self::InvalidEncodingLength { .. } => "NATIVE_INVALID_ENCODING",
            Self::SequenceOutOfOrder { .. } => "NATIVE_SEQUENCE_OUT_OF_ORDER",
            Self::SequenceOutOfWindow { .. } => "NATIVE_SEQUENCE_OUT_OF_WINDOW",
        }
    }

    #[must_use]
    pub const fn detail(&self) -> &'static str {
        match self {
            Self::InvalidWindow { .. } => "expected_first_must_not_exceed_expected_last",
            Self::WindowTooLarge { .. } => "expected_window_exceeds_declared_span",
            Self::InputTooLarge { .. } => "observed_binary_exceeds_declared_bytes",
            Self::InvalidEncodingLength { .. } => {
                "observed_binary_must_contain_complete_u64_values"
            }
            Self::SequenceOutOfOrder { .. } => "observed_sequences_must_be_non_decreasing",
            Self::SequenceOutOfWindow { .. } => "observed_sequence_is_outside_expected_window",
        }
    }
}

impl fmt::Display for SequenceDiffError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.detail())
    }
}

impl std::error::Error for SequenceDiffError {}

/// Computes compact missing ranges for a bounded, ordered sequence window.
///
/// The observed sequence binary must contain unsigned 64-bit integers in big-endian order and
/// must be non-decreasing. Duplicate adjacent values are counted and ignored for gap calculation.
///
/// # Errors
///
/// Returns a stable validation error when the window, input byte length, input count, ordering, or
/// sequence membership violates the declared kernel envelope.
pub fn compact_sequence_diff(
    expected_first: u64,
    expected_last: u64,
    observed_big_endian: &[u8],
) -> Result<SequenceDiff, SequenceDiffError> {
    validate_window(expected_first, expected_last)?;
    validate_input_bytes(observed_big_endian)?;

    let observed_count = observed_big_endian.len() / size_of::<u64>();
    let mut missing_ranges = Vec::with_capacity(observed_count.saturating_add(1));
    let mut unique_count = 0usize;
    let mut duplicate_count = 0usize;
    let mut previous = None;
    let mut next_missing = Some(expected_first);

    for (index, bytes) in observed_big_endian
        .chunks_exact(size_of::<u64>())
        .enumerate()
    {
        let sequence = u64::from_be_bytes(bytes.try_into().map_err(|_| {
            SequenceDiffError::InvalidEncodingLength {
                actual_bytes: observed_big_endian.len(),
            }
        })?);
        if sequence < expected_first || sequence > expected_last {
            return Err(SequenceDiffError::SequenceOutOfWindow {
                index,
                actual: sequence,
                expected_first,
                expected_last,
            });
        }
        if let Some(previous_sequence) = previous {
            if sequence < previous_sequence {
                return Err(SequenceDiffError::SequenceOutOfOrder {
                    index,
                    previous: previous_sequence,
                    actual: sequence,
                });
            }
            if sequence == previous_sequence {
                duplicate_count = duplicate_count.saturating_add(1);
                continue;
            }
        }

        unique_count = unique_count.saturating_add(1);
        if let Some(first_missing) = next_missing
            && first_missing < sequence
        {
            missing_ranges.push(SequenceRange::new(first_missing, sequence - 1));
        }
        next_missing = sequence.checked_add(1);
        previous = Some(sequence);
    }

    if let Some(first_missing) = next_missing
        && first_missing <= expected_last
    {
        missing_ranges.push(SequenceRange::new(first_missing, expected_last));
    }

    debug_assert!(missing_ranges.len() <= MAX_OUTPUT_RANGES);
    Ok(SequenceDiff {
        missing_ranges,
        observed_count,
        unique_count,
        duplicate_count,
    })
}

fn validate_window(expected_first: u64, expected_last: u64) -> Result<(), SequenceDiffError> {
    if expected_first > expected_last {
        return Err(SequenceDiffError::InvalidWindow {
            expected_first,
            expected_last,
        });
    }
    let actual_span = expected_last
        .saturating_sub(expected_first)
        .saturating_add(1);
    if actual_span > MAX_WINDOW_SPAN {
        return Err(SequenceDiffError::WindowTooLarge {
            actual_span,
            maximum_span: MAX_WINDOW_SPAN,
        });
    }
    Ok(())
}

fn validate_input_bytes(observed_big_endian: &[u8]) -> Result<(), SequenceDiffError> {
    if observed_big_endian.len() > MAX_INPUT_BYTES {
        return Err(SequenceDiffError::InputTooLarge {
            actual_bytes: observed_big_endian.len(),
            maximum_bytes: MAX_INPUT_BYTES,
        });
    }
    if !observed_big_endian.len().is_multiple_of(size_of::<u64>()) {
        return Err(SequenceDiffError::InvalidEncodingLength {
            actual_bytes: observed_big_endian.len(),
        });
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn encode(values: &[u64]) -> Vec<u8> {
        values
            .iter()
            .flat_map(|value| value.to_be_bytes())
            .collect()
    }

    #[test]
    fn compacts_missing_ranges_and_duplicates() {
        let result = compact_sequence_diff(10, 20, &encode(&[10, 11, 11, 14, 18, 20]));
        assert_eq!(
            result,
            Ok(SequenceDiff {
                missing_ranges: vec![
                    SequenceRange::new(12, 13),
                    SequenceRange::new(15, 17),
                    SequenceRange::new(19, 19),
                ],
                observed_count: 6,
                unique_count: 5,
                duplicate_count: 1,
            })
        );
    }

    #[test]
    fn handles_u64_max_without_overflow() {
        let result = compact_sequence_diff(u64::MAX - 2, u64::MAX, &encode(&[u64::MAX]));
        assert_eq!(
            result,
            Ok(SequenceDiff {
                missing_ranges: vec![SequenceRange::new(u64::MAX - 2, u64::MAX - 1)],
                observed_count: 1,
                unique_count: 1,
                duplicate_count: 0,
            })
        );
    }

    #[test]
    fn rejects_non_monotonic_input() {
        let result = compact_sequence_diff(1, 10, &encode(&[1, 4, 3]));
        assert!(matches!(
            result,
            Err(SequenceDiffError::SequenceOutOfOrder { index: 2, .. })
        ));
    }
}
