defmodule Liqi.Native.Kernel do
  @moduledoc "BEAM-owned admission and telemetry around the Senior 3 native provider API."

  @spec readiness() :: :ok | {:error, term()}
  def readiness do
    with {:ok, config} <- Liqi.Runtime.Config.load() do
      status = Liqi.Native.SequenceDiff.readiness(policy(config.native_mode))

      if status.ready do
        :ok
      else
        {:error, {:native_unavailable, status.reason}}
      end
    end
  end

  @spec sequence_diff([non_neg_integer()], non_neg_integer(), non_neg_integer()) ::
          {:ok, [map()]} | {:error, term()}
  def sequence_diff(_sequences, after_sequence, through_sequence)
      when through_sequence == after_sequence,
      do: {:ok, []}

  def sequence_diff(sequences, after_sequence, through_sequence)
      when is_list(sequences) and is_integer(after_sequence) and is_integer(through_sequence) and
             after_sequence >= 0 and through_sequence > after_sequence do
    with {:ok, config} <- Liqi.Runtime.Config.load() do
      Liqi.Runtime.Budgets.with_permit(:native, fn ->
        observed = encode_sequences(sequences)
        expected_first = after_sequence + 1

        case Liqi.Native.SequenceDiff.compact(
               expected_first,
               through_sequence,
               observed,
               policy(config.native_mode)
             ) do
          {:ok, result, execution} ->
            emit_execution(execution, length(sequences), length(result.missing_ranges))
            {:ok, result.missing_ranges}

          {:error, error, execution} ->
            emit_execution(execution, length(sequences), 0)
            {:error, {:native_kernel, error.code, error.retryable}}
        end
      end)
      |> case do
        {:error, :capacity} -> {:error, :native_capacity}
        result -> result
      end
    end
  rescue
    ArgumentError -> {:error, :invalid_sequence}
  end

  def sequence_diff(_, _, _), do: {:error, :invalid_input}

  defp encode_sequences(sequences) do
    Enum.reduce(sequences, <<>>, fn sequence, encoded ->
      if is_integer(sequence) and sequence >= 0 and sequence <= 18_446_744_073_709_551_615 do
        <<encoded::binary, sequence::unsigned-big-integer-size(64)>>
      else
        raise ArgumentError, "sequence must be an unsigned 64-bit integer"
      end
    end)
  end

  defp policy(:disabled), do: :reference
  defp policy(:optional), do: :native_preferred
  defp policy(:required), do: :native_required

  defp emit_execution(execution, observed_count, missing_range_count) do
    :telemetry.execute(
      [:liqi, :native, :sequence_diff],
      %{observed_count: observed_count, missing_range_count: missing_range_count},
      %{
        implementation: execution.implementation,
        fallback: execution.fallback,
        fallback_reason: execution.fallback_reason,
        kernel: execution.kernel,
        kernel_version: execution.kernel_version
      }
    )
  end
end
