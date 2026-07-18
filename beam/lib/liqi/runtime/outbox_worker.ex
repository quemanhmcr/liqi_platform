defmodule Liqi.Runtime.OutboxWorker do
  @moduledoc "Bounded V1 worker consumer of database-owned claim/terminal-effect semantics."
  use GenServer

  @consumer_id "liqi-beam-platform-probe-v1"

  def start_link(opts), do: GenServer.start_link(__MODULE__, opts, name: __MODULE__)
  def poll_once, do: GenServer.call(__MODULE__, :poll_once, 5_000)

  @impl true
  def init(opts) do
    enabled = Keyword.get(opts, :enabled, true)
    state = %{enabled: enabled, last_error: nil}
    if enabled, do: schedule(0)
    {:ok, state}
  end

  @impl true
  def handle_call(:poll_once, _from, state) do
    {reply, state} = do_poll(state)
    {:reply, reply, state}
  end

  @impl true
  def handle_info(:poll, state) do
    {_reply, state} = do_poll(state)
    schedule(100)
    {:noreply, state}
  end

  defp do_poll(state) do
    result =
      Liqi.Runtime.Budgets.with_permit(:database, fn ->
        adapter().claim_probe_events(@consumer_id, 10)
      end)

    case result do
      {:ok, events} ->
        outcomes = Enum.map(events, &process/1)
        {{:ok, outcomes}, %{state | last_error: nil}}

      {:error, reason} ->
        {{:error, reason}, %{state | last_error: reason}}
    end
  end

  defp process(event) do
    event_id = fetch(event, :event_id)
    claim_token = fetch(event, :claim_token)

    case fetch(event, :event_type) do
      "platform.probe.requested.v1" ->
        adapter().apply_probe_effect(event_id, claim_token, @consumer_id)

      _ ->
        retry_at = DateTime.add(DateTime.utc_now(), 5, :second)
        adapter().fail_event(event_id, claim_token, @consumer_id, "unsupported.event", retry_at)
    end
  end

  defp fetch(map, key), do: Map.get(map, key) || Map.get(map, Atom.to_string(key))
  defp adapter, do: Application.fetch_env!(:liqi_platform, :persistence_adapter)
  defp schedule(delay), do: Process.send_after(self(), :poll, delay)
end
