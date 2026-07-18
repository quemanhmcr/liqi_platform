#![no_main]

use libfuzzer_sys::fuzz_target;
use liqi_sequence_diff_core::{SequenceDiff, SequenceRange, compact_sequence_diff};
use std::collections::BTreeSet;

fuzz_target!(|data: &[u8]| {
    if data.len() < 16 {
        return;
    }
    let first = u64::from_be_bytes(data[0..8].try_into().unwrap_or([0; 8]));
    let requested_span = u64::from_be_bytes(data[8..16].try_into().unwrap_or([0; 8]));
    let span = requested_span % 256 + 1;
    let Some(last) = first.checked_add(span - 1) else {
        return;
    };

    let mut observed = data[16..]
        .chunks_exact(2)
        .take(256)
        .map(|chunk| first + u64::from(u16::from_be_bytes([chunk[0], chunk[1]])) % span)
        .collect::<Vec<_>>();
    observed.sort_unstable();
    let encoded = observed
        .iter()
        .flat_map(|sequence| sequence.to_be_bytes())
        .collect::<Vec<_>>();

    let optimized = compact_sequence_diff(first, last, &encoded);
    assert_eq!(optimized, Ok(reference(first, last, &observed)));
});

fn reference(first: u64, last: u64, observed: &[u64]) -> SequenceDiff {
    let unique = observed.iter().copied().collect::<BTreeSet<_>>();
    let mut missing_ranges = Vec::new();
    let mut open = None;
    for sequence in first..=last {
        if unique.contains(&sequence) {
            if let Some(range_first) = open.take() {
                missing_ranges.push(SequenceRange::new(range_first, sequence - 1));
            }
        } else if open.is_none() {
            open = Some(sequence);
        }
    }
    if let Some(range_first) = open {
        missing_ranges.push(SequenceRange::new(range_first, last));
    }
    SequenceDiff {
        missing_ranges,
        observed_count: observed.len(),
        unique_count: unique.len(),
        duplicate_count: observed.len() - unique.len(),
    }
}
