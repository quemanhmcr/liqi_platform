use liqi_sequence_diff_core::{
    MAX_OBSERVED_SEQUENCES, MAX_WINDOW_SPAN, SequenceDiff, SequenceRange, compact_sequence_diff,
};
use proptest::prelude::*;
use std::collections::BTreeSet;

fn encode(values: &[u64]) -> Vec<u8> {
    values
        .iter()
        .flat_map(|value| value.to_be_bytes())
        .collect()
}

fn reference(expected_first: u64, expected_last: u64, observed: &[u64]) -> SequenceDiff {
    let unique = observed.iter().copied().collect::<BTreeSet<_>>();
    let mut missing_ranges = Vec::new();
    let mut open = None;
    for sequence in expected_first..=expected_last {
        if unique.contains(&sequence) {
            if let Some(first) = open.take() {
                missing_ranges.push(SequenceRange::new(first, sequence - 1));
            }
        } else if open.is_none() {
            open = Some(sequence);
        }
    }
    if let Some(first) = open {
        missing_ranges.push(SequenceRange::new(first, expected_last));
    }
    SequenceDiff {
        missing_ranges,
        observed_count: observed.len(),
        unique_count: unique.len(),
        duplicate_count: observed.len() - unique.len(),
    }
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(2_000))]

    #[test]
    fn optimized_kernel_matches_reference(
        start in 0u64..1_000_000,
        span in 1u64..=256,
        raw in prop::collection::vec(0u16..=255, 0..256),
    ) {
        let end = start + span - 1;
        let mut observed = raw
            .into_iter()
            .map(|offset| start + u64::from(offset) % span)
            .collect::<Vec<_>>();
        observed.sort_unstable();
        let optimized = compact_sequence_diff(start, end, &encode(&observed));
        prop_assert_eq!(optimized, Ok(reference(start, end, &observed)));
    }
}

#[test]
fn maximum_declared_input_remains_bounded() {
    let observed = vec![7u64; MAX_OBSERVED_SEQUENCES];
    let result = compact_sequence_diff(7, 7 + MAX_WINDOW_SPAN - 1, &encode(&observed));
    assert!(result.is_ok());
    if let Ok(result) = result {
        assert_eq!(result.observed_count, MAX_OBSERVED_SEQUENCES);
        assert_eq!(result.unique_count, 1);
        assert_eq!(result.duplicate_count, MAX_OBSERVED_SEQUENCES - 1);
        assert_eq!(result.missing_ranges.len(), 1);
    }
}
