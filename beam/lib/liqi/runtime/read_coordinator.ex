defmodule Liqi.Runtime.ReadCoordinator do
  @moduledoc "Partitioned, bounded request coalescing for identical reads."
  use GenServer

  @max_waiters 64

  def start_link(opts), do: GenServer.start_link(__MODULE__, opts)

  def fetch(key, fun, timeout \\ 5_000) when is_function(fun, 0) do
    GenServer.call(via(key), {:fetch, key, fun}, timeout)
  catch
    :exit, {:timeout, _} -> {:error, :deadline_exceeded}
    :exit, _ -> {:error, :read_coordinator_unavailable}
  end

  defp via(key), do: {:via, PartitionSupervisor, {Liqi.Runtime.ReadPartitions, key}}

  @impl true
  def init(opts), do: {:ok, %{partition: Keyword.get(opts, :partition), inflight: %{}}}

  @impl true
  def handle_call({:fetch, key, fun}, from, state) do
    case state.inflight do
      %{^key => waiters} when length(waiters) >= @max_waiters ->
        {:reply, {:error, :coalescing_capacity}, state}

      %{^key => waiters} ->
        {:noreply, put_in(state.inflight[key], [from | waiters])}

      _ ->
        owner = self()

        case Task.Supervisor.start_child(Liqi.Runtime.TaskSupervisor, fn ->
               result = Liqi.Runtime.Budgets.with_permit(:task, fun)
               send(owner, {:read_result, key, result})
             end) do
          {:ok, _pid} -> {:noreply, put_in(state.inflight[key], [from])}
          {:error, :max_children} -> {:reply, {:error, :task_capacity}, state}
          {:error, reason} -> {:reply, {:error, reason}, state}
        end
    end
  end

  @impl true
  def handle_info({:read_result, key, result}, state) do
    {waiters, inflight} = Map.pop(state.inflight, key, [])
    Enum.each(waiters, &GenServer.reply(&1, result))
    {:noreply, %{state | inflight: inflight}}
  end
end
