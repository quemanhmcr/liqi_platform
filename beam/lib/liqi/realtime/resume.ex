defmodule Liqi.Realtime.Resume do
  @moduledoc false

  def repair(session_pid, after_cursor) do
    with {:ok, config} <- Liqi.Runtime.Config.load(),
         {:ok, events} <-
           Liqi.Realtime.Dispatcher.gap_read(after_cursor, config.session_queue_capacity),
         :ok <- verify_sequence_window(events, after_cursor),
         {:ok, snapshot} <- Liqi.Realtime.SessionActor.snapshot(session_pid),
         relevant =
           Enum.filter(
             events,
             &(&1 |> aggregate_key() |> then(fn key -> key in snapshot.subscriptions end))
           ),
         :ok <- deliver_all(session_pid, relevant) do
      {:ok,
       %{
         delivered: length(relevant),
         scanned: length(events),
         cursor: last_cursor(events, after_cursor)
       }}
    end
  end

  defp verify_sequence_window([], _after_cursor), do: :ok

  defp verify_sequence_window(events, after_cursor) do
    sequences = Enum.map(events, &(Map.get(&1, :handoff_id) || Map.fetch!(&1, "handoff_id")))
    through = List.last(sequences)

    case Liqi.Native.Kernel.sequence_diff(sequences, after_cursor, through) do
      {:ok, []} -> :ok
      {:ok, missing} -> {:error, {:handoff_gap, missing}}
      {:error, reason} -> {:error, {:sequence_kernel, reason}}
    end
  end

  defp deliver_all(session_pid, events) do
    Enum.reduce_while(events, :ok, fn event, :ok ->
      case Liqi.Realtime.SessionActor.deliver(session_pid, event) do
        result when result in [:ok, :duplicate] -> {:cont, :ok}
        {:error, reason} -> {:halt, {:error, reason}}
      end
    end)
  end

  defp aggregate_key(event),
    do: Map.get(event, :aggregate_key) || Map.fetch!(event, "aggregate_key")

  defp last_cursor([], fallback), do: fallback

  defp last_cursor(events, _fallback) do
    event = List.last(events)
    Map.get(event, :handoff_id) || Map.fetch!(event, "handoff_id")
  end
end
