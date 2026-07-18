defmodule Liqi.Native.Kernel do
  @moduledoc "Feature-flagged native boundary with fail-closed required mode and pure fallback."

  def readiness do
    with {:ok, config} <- Liqi.Runtime.Config.load() do
      case config.native_mode do
        :disabled -> :ok
        :optional -> :ok
        :required -> adapter().readiness()
      end
    end
  end

  def sequence_diff(sequences, after_sequence, through_sequence) do
    with {:ok, config} <- Liqi.Runtime.Config.load() do
      Liqi.Runtime.Budgets.with_permit(:native, fn ->
        call_adapter(config.native_mode, sequences, after_sequence, through_sequence)
      end)
      |> case do
        {:error, :capacity} -> {:error, :native_capacity}
        result -> result
      end
    end
  end

  defp call_adapter(:disabled, sequences, after_sequence, through_sequence),
    do: Liqi.Native.Fallback.sequence_diff(sequences, after_sequence, through_sequence)

  defp call_adapter(mode, sequences, after_sequence, through_sequence) do
    case adapter().sequence_diff(sequences, after_sequence, through_sequence) do
      {:ok, _} = result -> result
      {:error, _} = error when mode == :required -> error
      {:error, _} -> Liqi.Native.Fallback.sequence_diff(sequences, after_sequence, through_sequence)
    end
  rescue
    _error ->
      if mode == :required do
        {:error, :native_crash}
      else
        Liqi.Native.Fallback.sequence_diff(sequences, after_sequence, through_sequence)
      end
  catch
    :exit, _reason ->
      if mode == :required do
        {:error, :native_exit}
      else
        Liqi.Native.Fallback.sequence_diff(sequences, after_sequence, through_sequence)
      end
  end

  defp adapter, do: Application.fetch_env!(:liqi_platform, :native_adapter)
end
