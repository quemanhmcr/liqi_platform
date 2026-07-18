defmodule Liqi.Telemetry.Store do
  @moduledoc "Bounded in-memory metric counters; telemetry is never durable authority."
  use GenServer

  @events [
    [:liqi, :runtime, :drain],
    [:liqi, :actor, :mailbox, :warning],
    [:liqi, :actor, :mailbox, :reject],
    [:liqi, :session, :slow_consumer],
    [:liqi, :realtime, :handoff],
    [:liqi, :realtime, :handoff, :error],
    [:phoenix, :endpoint, :stop]
  ]

  def start_link(_opts), do: GenServer.start_link(__MODULE__, :ok, name: __MODULE__)
  def snapshot, do: GenServer.call(__MODULE__, :snapshot)

  @impl true
  def init(:ok) do
    :telemetry.attach_many({__MODULE__, self()}, @events, &__MODULE__.handle_event/4, self())
    {:ok, %{counters: %{}, last_measurements: %{}}}
  end

  def handle_event(event, measurements, _metadata, pid) do
    send(pid, {:telemetry_event, event, measurements})
  end

  @impl true
  def handle_info({:telemetry_event, event, measurements}, state) do
    key = Enum.join(event, ".")
    counters = Map.update(state.counters, key, 1, &(&1 + 1))
    last = Map.put(state.last_measurements, key, bounded_measurements(measurements))
    {:noreply, %{state | counters: counters, last_measurements: last}}
  end

  @impl true
  def handle_call(:snapshot, _from, state), do: {:reply, state, state}

  @impl true
  def terminate(_reason, _state) do
    :telemetry.detach({__MODULE__, self()})
    :ok
  end

  defp bounded_measurements(measurements) do
    measurements
    |> Enum.take(8)
    |> Map.new(fn {key, value} -> {key, value} end)
  end
end
