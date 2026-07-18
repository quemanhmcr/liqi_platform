defmodule Liqi.Native.Benchmark.SequenceDiff do
  @moduledoc false

  alias Liqi.Native.SequenceDiff.Nif

  @target_p99_us 500.0
  @hard_budget_us 1_000.0
  @max_probe_delay_ms 25.0

  @spec run(pos_integer(), pos_integer()) :: map()
  def run(samples \\ 20_000, probe_samples \\ 2_000)
      when samples >= 1_000 and probe_samples >= 100 do
    expected_info = %{
      kernel: "compact_sequence_diff",
      kernel_version: "1",
      nif_abi: "2.15",
      scheduler_class: "regular"
    }

    info = Nif.kernel_info_v1()

    unless Map.take(info, Map.keys(expected_info)) == expected_info do
      raise "native capability mismatch"
    end

    observed = for offset <- 0..2_047, into: <<>>, do: <<(offset * 2)::unsigned-big-integer-size(64)>>

    Enum.each(1..2_000, fn _ ->
      {:ok, _result} = Nif.compact_sequence_diff_v1(0, 4_095, observed)
    end)

    parent = self()

    spawn_link(fn ->
      delays =
        Enum.map(1..probe_samples, fn _ ->
          started = System.monotonic_time(:nanosecond)
          Process.sleep(1)
          System.monotonic_time(:nanosecond) - started
        end)

      send(parent, {:scheduler_probe, delays})
    end)

    latencies =
      1..samples
      |> Task.async_stream(
        fn _ ->
          started = System.monotonic_time(:nanosecond)
          {:ok, _result} = Nif.compact_sequence_diff_v1(0, 4_095, observed)
          System.monotonic_time(:nanosecond) - started
        end,
        max_concurrency: 2,
        ordered: false,
        timeout: 5_000,
        on_timeout: :kill_task
      )
      |> Enum.map(fn {:ok, latency} -> latency end)
      |> Enum.sort()

    probe_delays =
      receive do
        {:scheduler_probe, delays} -> Enum.sort(delays)
      after
        30_000 -> raise "scheduler probe did not complete"
      end

    latency_us = quantiles(latencies, 1_000.0)
    probe_ms = quantiles(probe_delays, 1_000_000.0)

    status =
      if latency_us.p99 < @target_p99_us and latency_us.maximum < @hard_budget_us and
           probe_ms.maximum < @max_probe_delay_ms do
        :passed
      else
        :failed
      end

    %{
      status: status,
      samples: samples,
      case: %{
        observed_sequences: 2_048,
        input_bytes: byte_size(observed),
        window_span: 4_096,
        expected_output_ranges: 2_048
      },
      latency_us:
        Map.merge(latency_us, %{target_p99: @target_p99_us, hard_budget: @hard_budget_us}),
      scheduler_impact: %{
        starvation_tested: true,
        max_probe_delay_ms: probe_ms.maximum,
        status: if(probe_ms.maximum < @max_probe_delay_ms, do: :passed, else: :failed)
      },
      environment: %{
        beam_schedulers: :erlang.system_info(:schedulers_online),
        dirty_cpu_schedulers: :erlang.system_info(:dirty_cpu_schedulers_online),
        dirty_io_schedulers: :erlang.system_info(:dirty_io_schedulers),
        async_threads: :erlang.system_info(:thread_pool_size)
      },
      kernel_info: info
    }
  end

  defp quantiles(sorted_ns, divisor) do
    %{
      p50: percentile(sorted_ns, 50) / divisor,
      p95: percentile(sorted_ns, 95) / divisor,
      p99: percentile(sorted_ns, 99) / divisor,
      maximum: List.last(sorted_ns) / divisor
    }
  end

  defp percentile(sorted, percentile) do
    index = max(div(length(sorted) * percentile + 99, 100) - 1, 0)
    Enum.at(sorted, min(index, length(sorted) - 1), 0)
  end
end
