defmodule Liqi.Native.Benchmark.A1Evidence do
  @moduledoc false

  alias Liqi.Native.Benchmark.SequenceDiff, as: Benchmark
  alias Liqi.Native.Reference.SequenceDiff, as: Reference
  alias Liqi.Native.SequenceDiff
  alias Liqi.Native.SequenceDiff.Nif

  defmodule MissingNative do
    def kernel_info_v1, do: :erlang.nif_error(:nif_not_loaded)
  end

  def run do
    source_revision = required_env!("LIQI_SOURCE_REVISION", ~r/^[0-9a-f]{40}$/)
    artifact_sha256 = required_env!("LIQI_ARTIFACT_SHA256", ~r/^[0-9a-f]{64}$/)
    output_path = System.fetch_env!("LIQI_BENCHMARK_OUTPUT")
    environment_class = System.get_env("LIQI_ENVIRONMENT_CLASS", "oci-a1-flex-4ocpu-24gib")

    unless environment_class == "oci-a1-flex-4ocpu-24gib" do
      raise "LIQI_ENVIRONMENT_CLASS must be oci-a1-flex-4ocpu-24gib"
    end

    host_cpu = positive_integer_env!("LIQI_HOST_CPU", 4)
    host_memory_mib = positive_integer_env!("LIQI_HOST_MEMORY_MIB", 24_576)
    samples = positive_integer_env!("LIQI_BENCHMARK_SAMPLES", 20_000)
    probe_samples = positive_integer_env!("LIQI_SCHEDULER_PROBE_SAMPLES", 2_000)
    started_at = DateTime.utc_now()

    native_info = Nif.kernel_info_v1()

    unless native_info.target_arch == "aarch64" and native_info.target_os == "linux" do
      raise "benchmark requires the installed Linux AArch64 NIF"
    end

    observed = for offset <- 0..2_047, into: <<>>, do: <<(offset * 2)::unsigned-big-integer-size(64)>>
    {:ok, native_result} = Nif.compact_sequence_diff_v1(0, 4_095, observed)
    {:ok, reference_result} = Reference.compact(0, 4_095, observed)

    parity_verified = native_result == reference_result

    fallback_verified =
      match?(
        {:ok, ^reference_result,
         %{
           implementation: :reference,
           fallback: true,
           fallback_reason: "NATIVE_UNAVAILABLE"
         }},
        SequenceDiff.compact_with_native(
          0,
          4_095,
          observed,
          :native_preferred,
          MissingNative
        )
      )

    result = Benchmark.run(samples, probe_samples)
    completed_at = DateTime.utc_now()

    passed = result.status == :passed and parity_verified and fallback_verified

    evidence = %{
      schema_version: "native-benchmark-v1",
      status: if(passed, do: "passed", else: "failed"),
      kernel: "compact_sequence_diff",
      kernel_version: "1",
      source_revision: source_revision,
      artifact_sha256: artifact_sha256,
      execution_path: "beam-rustler-nif",
      environment: %{
        class: environment_class,
        target_triple: "aarch64-unknown-linux-gnu",
        host_cpu: host_cpu,
        host_memory_mib: host_memory_mib,
        beam_schedulers: result.environment.beam_schedulers,
        dirty_cpu_schedulers: result.environment.dirty_cpu_schedulers,
        dirty_io_schedulers: result.environment.dirty_io_schedulers,
        async_threads: result.environment.async_threads
      },
      case: result.case,
      samples: result.samples,
      latency_us: result.latency_us,
      scheduler_impact: result.scheduler_impact,
      fallback_verified: fallback_verified,
      command: [
        "cd native/elixir",
        "mix run ../bench/run-a1-nif-benchmark.exs"
      ],
      started_at: DateTime.to_iso8601(started_at),
      completed_at: DateTime.to_iso8601(completed_at),
      notes: [
        "parity_verified=#{parity_verified}",
        "native_artifact_identity=#{native_info.kernel}:#{native_info.kernel_version}:nif-#{native_info.nif_abi}",
        "feature_default_remains_disabled_until_readiness_acceptance"
      ]
    }

    output_path
    |> Path.dirname()
    |> File.mkdir_p!()

    File.write!(output_path, JSON.encode!(evidence) <> "\n")
    IO.puts(JSON.encode!(%{validation: "native-a1-benchmark-v1", status: evidence.status, output: output_path}))

    unless passed do
      System.halt(1)
    end
  end

  defp required_env!(name, pattern) do
    value = System.fetch_env!(name)
    unless Regex.match?(pattern, value), do: raise("#{name} has an invalid format")
    value
  end

  defp positive_integer_env!(name, default) do
    value = name |> System.get_env(Integer.to_string(default)) |> String.to_integer()
    unless value > 0, do: raise("#{name} must be positive")
    value
  end
end

Liqi.Native.Benchmark.A1Evidence.run()
