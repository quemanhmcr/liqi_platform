defmodule Liqi.Runtime.AdmissionController do
  @moduledoc false
  use GenServer

  def start_link(opts), do: GenServer.start_link(__MODULE__, opts)

  def admit(router_key, category) do
    GenServer.call(via(router_key), {:admit, category}, 100)
  catch
    :exit, _ -> {:error, :admission_unavailable}
  end

  defp via(key), do: {:via, PartitionSupervisor, {Liqi.Runtime.AdmissionPartitions, key}}

  @impl true
  def init(opts), do: {:ok, %{partition: Keyword.get(opts, :partition), rejected: 0}}

  @impl true
  def handle_call({:admit, category}, _from, state) do
    reply =
      cond do
        category not in [:endpoint, :database, :reconnect, :native, :task] ->
          {:error, :invalid_category}

        Liqi.Runtime.State.draining?() ->
          {:error, :draining}

        true ->
          :ok
      end

    state = if match?({:error, _}, reply), do: %{state | rejected: state.rejected + 1}, else: state
    {:reply, reply, state}
  end
end
