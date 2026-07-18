defmodule Liqi.Realtime.Dispatcher do
  @moduledoc "Consumes only committed database handoff rows and fans out ephemeral notifications."
  use GenServer

  def start_link(opts), do: GenServer.start_link(__MODULE__, opts, name: __MODULE__)
  def poll_once, do: GenServer.call(__MODULE__, :poll_once, 5_000)
  def status, do: GenServer.call(__MODULE__, :status)

  def gap_read(after_cursor, limit) when is_integer(after_cursor) and after_cursor >= 0 do
    adapter().read_handoff(after_cursor, limit)
  end

  @impl true
  def init(opts) do
    {:ok, config} = Liqi.Runtime.Config.load()
    enabled = Keyword.get(opts, :enabled, true)
    state = %{cursor: 0, last_error: nil, config: config, enabled: enabled}
    if enabled, do: schedule(0)
    {:ok, state}
  end

  @impl true
  def handle_call(:poll_once, _from, state) do
    {reply, state} = do_poll(state)
    {:reply, reply, state}
  end

  def handle_call(:status, _from, state),
    do:
      {:reply, %{cursor: state.cursor, last_error: state.last_error, enabled: state.enabled}, state}

  @impl true
  def handle_info(:poll, state) do
    {_reply, state} = do_poll(state)
    schedule(state.config.handoff_poll_interval_ms)
    {:noreply, state}
  end

  defp do_poll(state) do
    result =
      Liqi.Runtime.Budgets.with_permit(:database, fn ->
        adapter().read_handoff(state.cursor, state.config.handoff_batch_size)
      end)

    case result do
      {:ok, events} ->
        Enum.each(events, &broadcast/1)
        cursor = Enum.reduce(events, state.cursor, &max(cursor(&1), &2))

        :telemetry.execute([:liqi, :realtime, :handoff], %{events: length(events)}, %{
          cursor: cursor
        })

        {{:ok, length(events)}, %{state | cursor: cursor, last_error: nil}}

      {:error, reason} ->
        :telemetry.execute([:liqi, :realtime, :handoff, :error], %{count: 1}, %{
          reason: inspect(reason)
        })

        {{:error, reason}, %{state | last_error: reason}}
    end
  end

  defp broadcast(event) do
    Phoenix.PubSub.local_broadcast(
      Liqi.PubSub,
      "liqi:realtime:" <> aggregate_key(event),
      {:committed_event, event}
    )
  end

  defp aggregate_key(event),
    do: Map.get(event, :aggregate_key) || Map.fetch!(event, "aggregate_key")

  defp cursor(event), do: Map.get(event, :handoff_id) || Map.fetch!(event, "handoff_id")
  defp adapter, do: Application.fetch_env!(:liqi_platform, :persistence_adapter)
  defp schedule(delay), do: Process.send_after(self(), :poll, delay)
end
