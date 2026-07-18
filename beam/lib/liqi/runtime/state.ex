defmodule Liqi.Runtime.State do
  @moduledoc false
  use GenServer

  def start_link(_opts), do: GenServer.start_link(__MODULE__, :ok, name: __MODULE__)
  def draining?, do: GenServer.call(__MODULE__, :draining?)
  def begin_drain, do: GenServer.call(__MODULE__, :begin_drain)

  @impl true
  def init(:ok), do: {:ok, %{draining?: false, started_at: System.monotonic_time(:millisecond)}}

  @impl true
  def handle_call(:draining?, _from, state), do: {:reply, state.draining?, state}

  def handle_call(:begin_drain, _from, state) do
    :telemetry.execute([:liqi, :runtime, :drain], %{count: 1}, %{})
    {:reply, :ok, %{state | draining?: true}}
  end
end
