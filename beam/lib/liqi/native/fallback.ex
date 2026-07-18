defmodule Liqi.Native.Fallback do
  @moduledoc "Pure Elixir reference implementation for bounded resume sequence diff."
  @behaviour Liqi.Native.Adapter

  @hard_max_items 4096

  @impl true
  def readiness, do: :ok

  @impl true
  def sequence_diff(sequences, after_sequence, through_sequence)
      when is_list(sequences) and is_integer(after_sequence) and is_integer(through_sequence) do
    cond do
      length(sequences) > @hard_max_items ->
        {:error, :input_too_large}

      after_sequence < 0 or through_sequence < after_sequence ->
        {:error, :invalid_range}

      through_sequence - after_sequence > @hard_max_items ->
        {:error, :range_too_large}

      through_sequence == after_sequence ->
        {:ok, []}

      true ->
        present = MapSet.new(sequences)
        {:ok, Enum.reject((after_sequence + 1)..through_sequence, &MapSet.member?(present, &1))}
    end
  end

  def sequence_diff(_, _, _), do: {:error, :invalid_input}
end
