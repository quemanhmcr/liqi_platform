defmodule Liqi.Realtime.ConnectionProcess do
  @moduledoc "Transport lifecycle owner. Logical session state remains in SessionActor."
  use GenServer, restart: :temporary

  def start_link(opts), do: GenServer.start_link(__MODULE__, opts)
  def frame(pid, frame), do: GenServer.cast(pid, {:frame, frame})
  def close(pid, reason), do: GenServer.cast(pid, {:close, reason})

  @impl true
  def init(opts) do
    channel_pid = Keyword.fetch!(opts, :channel_pid)
    monitor = Process.monitor(channel_pid)

    {:ok,
     %{
       connection_id: Keyword.fetch!(opts, :connection_id),
       channel_pid: channel_pid,
       session_pid: Keyword.fetch!(opts, :session_pid),
       monitor: monitor
     }}
  end

  @impl true
  def handle_cast({:frame, frame}, state) do
    with {:message_queue_len, length} <- Process.info(state.channel_pid, :message_queue_len),
         {:ok, config} <- Liqi.Runtime.Config.load() do
      if length >= config.actor_mailbox_reject do
        send(state.channel_pid, {:connection_close, :slow_consumer})
        {:stop, :normal, state}
      else
        send(state.channel_pid, {:connection_frame, frame})
        {:noreply, state}
      end
    else
      _ -> {:stop, :normal, state}
    end
  end

  def handle_cast({:close, reason}, state) do
    send(state.channel_pid, {:connection_close, reason})
    {:stop, :normal, state}
  end

  @impl true
  def handle_info({:DOWN, monitor, :process, _pid, _reason}, %{monitor: monitor} = state) do
    Liqi.Realtime.SessionActor.detach(state.session_pid, self())
    {:stop, :normal, state}
  end
end
