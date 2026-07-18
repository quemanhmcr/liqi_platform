defmodule Liqi.Runtime.PlatformProbeActor do
  @moduledoc "Short-lived serialized command owner; durable idempotency remains database-owned."
  use GenServer, restart: :transient

  @idle_timeout 30_000

  def start_link(opts) do
    probe_id = Keyword.fetch!(opts, :probe_id)
    GenServer.start_link(__MODULE__, opts, name: via(probe_id))
  end

  def execute(probe_id, envelope, idempotency_key) do
    with {:ok, pid} <- ensure_started(probe_id) do
      Liqi.Runtime.ActorRouter.call(
        pid,
        {:execute, envelope, idempotency_key},
        Liqi.Runtime.Envelope.remaining_ms(envelope)
      )
    end
  end

  defp ensure_started(probe_id) do
    case Registry.lookup(Liqi.ActorRegistry, {:platform_probe, probe_id}) do
      [{pid, _}] ->
        {:ok, pid}

      [] ->
        supervisor = {:via, PartitionSupervisor, {Liqi.Runtime.ActorPartitions, probe_id}}

        case DynamicSupervisor.start_child(supervisor, {__MODULE__, probe_id: probe_id}) do
          {:ok, pid} -> {:ok, pid}
          {:error, {:already_started, pid}} -> {:ok, pid}
          other -> other
        end
    end
  end

  defp via(probe_id), do: {:via, Registry, {Liqi.ActorRegistry, {:platform_probe, probe_id}}}

  @impl true
  def init(opts), do: {:ok, %{probe_id: Keyword.fetch!(opts, :probe_id)}, @idle_timeout}

  @impl true
  def handle_call({:execute, envelope, idempotency_key}, _from, state) do
    result = Liqi.Runtime.PlatformProbe.execute(envelope, state.probe_id, idempotency_key)
    {:reply, result, state, @idle_timeout}
  end

  @impl true
  def handle_info(:timeout, state), do: {:stop, :normal, state}
end
