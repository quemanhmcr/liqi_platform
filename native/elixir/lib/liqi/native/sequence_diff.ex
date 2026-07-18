defmodule Liqi.Native.SequenceDiff do
  @moduledoc """
  Stable optional-native API for compact sequence diff.

  The caller chooses one of three explicit policies:

    * `:native_preferred` uses the regular NIF when capability negotiation succeeds and falls
      back to the pure Elixir reference only for native availability, version, or panic failures;
    * `:native_required` fails closed when the exact native capability is unavailable;
    * `:reference` never attempts to load native code.

  The execution metadata is deliberately returned to the caller so Senior 1 can emit the shared
  telemetry conventions without this provider creating a competing telemetry abstraction.
  """

  alias Liqi.Native.Reference.SequenceDiff, as: Reference
  alias Liqi.Native.SequenceDiff.Nif

  @kernel "compact_sequence_diff"
  @kernel_version "1"
  @nif_abi "2.15"
  @scheduler_class "regular"
  @optional_failure_codes MapSet.new([
                            "NATIVE_UNAVAILABLE",
                            "NATIVE_VERSION_MISMATCH",
                            "NATIVE_PANIC"
                          ])

  @type mode :: :native_preferred | :native_required | :reference
  @type execution :: %{
          implementation: :native | :reference | :none,
          fallback: boolean(),
          fallback_reason: String.t() | nil,
          kernel: String.t(),
          kernel_version: String.t()
        }

  @spec compact(integer(), integer(), binary(), mode()) ::
          {:ok, Reference.result(), execution()}
          | {:error, Reference.native_error(), execution()}
  def compact(expected_first, expected_last, observed_big_endian, mode \\ :native_preferred) do
    compact_with_native(expected_first, expected_last, observed_big_endian, mode, Nif)
  end

  @doc false
  @spec compact_with_native(integer(), integer(), binary(), mode(), module()) ::
          {:ok, Reference.result(), execution()}
          | {:error, Reference.native_error(), execution()}
  def compact_with_native(expected_first, expected_last, observed_big_endian, mode, native_module)
      when mode in [:native_preferred, :native_required, :reference] do
    case mode do
      :reference ->
        reference_reply(expected_first, expected_last, observed_big_endian, false, nil)

      native_mode ->
        case native_reply(native_module, expected_first, expected_last, observed_big_endian) do
          {:ok, result} ->
            {:ok, result, execution(:native, false, nil)}

          {:error, %{code: code} = error} when native_mode == :native_preferred ->
            if MapSet.member?(@optional_failure_codes, code) do
              reference_reply(expected_first, expected_last, observed_big_endian, true, code)
            else
              {:error, error, execution(:none, false, nil)}
            end

          {:error, error} ->
            {:error, error, execution(:none, false, nil)}
        end
    end
  end

  def compact_with_native(_expected_first, _expected_last, _observed_big_endian, _mode, _module) do
    error = %{
      code: "NATIVE_ADMISSION_REJECTED",
      retryable: false,
      detail: "native_mode_is_not_supported"
    }

    {:error, error, execution(:none, false, nil)}
  end

  @spec readiness(mode()) :: map()
  def readiness(mode \\ :native_preferred), do: readiness_with_native(mode, Nif)

  @doc false
  @spec readiness_with_native(mode(), module()) :: map()
  def readiness_with_native(mode, native_module) do
    case negotiate(native_module) do
      {:ok, info} ->
        %{
          ready: true,
          required: mode == :native_required,
          native_available: true,
          reason: nil,
          kernel_info: info
        }

      {:error, error} ->
        %{
          ready: mode != :native_required,
          required: mode == :native_required,
          native_available: false,
          reason: error.code,
          kernel_info: nil
        }
    end
  end

  defp native_reply(native_module, expected_first, expected_last, observed_big_endian) do
    with {:ok, _info} <- negotiate(native_module) do
      try do
        native_module.compact_sequence_diff_v1(
          expected_first,
          expected_last,
          observed_big_endian
        )
      rescue
        _exception -> unavailable("native_call_failed")
      catch
        :error, :nif_not_loaded -> unavailable("native_artifact_not_loaded")
        :error, :undef -> unavailable("native_module_or_function_unavailable")
        :exit, _reason -> unavailable("native_call_exited")
      end
    end
  end

  defp negotiate(native_module) do
    try do
      case native_module.kernel_info_v1() do
        %{
          kernel: @kernel,
          kernel_version: @kernel_version,
          nif_abi: @nif_abi,
          scheduler_class: @scheduler_class
        } = info ->
          {:ok, info}

        _other ->
          {:error,
           %{
             code: "NATIVE_VERSION_MISMATCH",
             retryable: false,
             detail: "native_capability_does_not_match_required_kernel_contract"
           }}
      end
    rescue
      _exception -> unavailable("native_capability_probe_failed")
    catch
      :error, :nif_not_loaded -> unavailable("native_artifact_not_loaded")
      :error, :undef -> unavailable("native_module_or_function_unavailable")
      :exit, _reason -> unavailable("native_capability_probe_exited")
    end
  end

  defp reference_reply(expected_first, expected_last, observed_big_endian, fallback, reason) do
    case Reference.compact(expected_first, expected_last, observed_big_endian) do
      {:ok, result} -> {:ok, result, execution(:reference, fallback, reason)}
      {:error, error} -> {:error, error, execution(:reference, fallback, reason)}
    end
  end

  defp unavailable(detail) do
    {:error, %{code: "NATIVE_UNAVAILABLE", retryable: true, detail: detail}}
  end

  defp execution(implementation, fallback, fallback_reason) do
    %{
      implementation: implementation,
      fallback: fallback,
      fallback_reason: fallback_reason,
      kernel: @kernel,
      kernel_version: @kernel_version
    }
  end
end
