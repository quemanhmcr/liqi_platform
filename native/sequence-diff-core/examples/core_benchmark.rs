use liqi_sequence_diff_core::compact_sequence_diff;
use serde_json::json;
use std::{hint::black_box, io::Write, time::Duration, time::Instant};

const DEFAULT_SAMPLES: usize = 20_000;
const WARMUP_SAMPLES: usize = 2_000;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let samples = std::env::args()
        .nth(1)
        .and_then(|value| value.parse::<usize>().ok())
        .filter(|value| *value >= 1_000)
        .unwrap_or(DEFAULT_SAMPLES);
    let observed = (0u64..2_048).map(|offset| offset * 2).collect::<Vec<_>>();
    let encoded = observed
        .iter()
        .flat_map(|sequence| sequence.to_be_bytes())
        .collect::<Vec<_>>();

    for _ in 0..WARMUP_SAMPLES {
        let result = compact_sequence_diff(0, 4_095, black_box(&encoded));
        assert!(result.is_ok());
    }

    let mut latencies = Vec::with_capacity(samples);
    for _ in 0..samples {
        let started = Instant::now();
        let result = compact_sequence_diff(0, 4_095, black_box(&encoded));
        black_box(result).unwrap_or_else(|error| unreachable!("benchmark input is valid: {error}"));
        latencies.push(started.elapsed());
    }
    latencies.sort_unstable();

    let output = json!({
        "case": {
            "observed_sequences": observed.len(),
            "input_bytes": encoded.len(),
            "window_span": 4096,
            "expected_output_ranges": 2048
        },
        "samples": samples,
        "warmup_samples": WARMUP_SAMPLES,
        "latency_us": {
            "p50": duration_us(percentile(&latencies, 50)),
            "p95": duration_us(percentile(&latencies, 95)),
            "p99": duration_us(percentile(&latencies, 99)),
            "maximum": duration_us(latencies.last().copied().unwrap_or_default())
        }
    });
    writeln!(std::io::stdout().lock(), "{output}")?;
    Ok(())
}

fn percentile(sorted: &[Duration], percentile: usize) -> Duration {
    let index = sorted
        .len()
        .saturating_mul(percentile)
        .div_ceil(100)
        .saturating_sub(1)
        .min(sorted.len().saturating_sub(1));
    sorted.get(index).copied().unwrap_or_default()
}

fn duration_us(duration: Duration) -> f64 {
    duration.as_secs_f64() * 1_000_000.0
}
