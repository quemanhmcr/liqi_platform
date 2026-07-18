defmodule Liqi.Runtime.Supervisor do
  @moduledoc false
  use Supervisor

  def start_link(opts),
    do: Supervisor.start_link(__MODULE__, opts, name: Keyword.fetch!(opts, :name))

  @impl true
  def init(_opts) do
    {:ok, config} = Liqi.Runtime.Config.load()

    children =
      [
        Liqi.Runtime.State,
        {Liqi.Runtime.Budgets, config},
        Liqi.Telemetry.Store,
        {Phoenix.PubSub, name: Liqi.PubSub, pool_size: config.actor_partitions},
        registry(Liqi.SessionRegistry, config.actor_partitions),
        registry(Liqi.ActorRegistry, config.actor_partitions),
        {Task.Supervisor, name: Liqi.Runtime.TaskSupervisor, max_children: config.bounded_tasks},
        actor_partitions(config),
        admission_partitions(config),
        read_partitions(config)
      ] ++ persistence_children(config) ++ runtime_consumers(config) ++ [Liqi.Web.Endpoint]

    Supervisor.init(children, strategy: :one_for_one, max_restarts: 10, max_seconds: 30)
  end

  defp registry(name, partitions),
    do: {Registry, keys: :unique, name: name, partitions: partitions}

  defp actor_partitions(config) do
    {PartitionSupervisor,
     child_spec: {DynamicSupervisor, strategy: :one_for_one, max_children: 10_000},
     name: Liqi.Runtime.ActorPartitions,
     partitions: config.actor_partitions}
  end

  defp admission_partitions(config) do
    {PartitionSupervisor,
     child_spec: Liqi.Runtime.AdmissionController,
     name: Liqi.Runtime.AdmissionPartitions,
     partitions: config.actor_partitions}
  end

  defp read_partitions(config) do
    {PartitionSupervisor,
     child_spec: Liqi.Runtime.ReadCoordinator,
     name: Liqi.Runtime.ReadPartitions,
     partitions: config.actor_partitions}
  end

  defp persistence_children(config) do
    if Application.get_env(:liqi_platform, :start_persistence, config.persistence_enabled) do
      [Liqi.Persistence.ApiRepo, Liqi.Persistence.RealtimeRepo, Liqi.Persistence.WorkerRepo]
    else
      fake_child()
    end
  end

  defp fake_child do
    case Application.fetch_env!(:liqi_platform, :persistence_adapter) do
      Liqi.Persistence.Fake -> [Liqi.Persistence.Fake]
      _ -> []
    end
  end

  defp runtime_consumers(config) do
    []
    |> maybe_add(
      Application.get_env(:liqi_platform, :start_outbox_worker, config.outbox_worker_enabled),
      {Liqi.Runtime.OutboxWorker, enabled: true}
    )
    |> maybe_add(
      Application.get_env(:liqi_platform, :start_dispatcher, config.dispatcher_enabled),
      {Liqi.Realtime.Dispatcher, enabled: true}
    )
    |> maybe_add(
      Application.get_env(:liqi_platform, :start_oban, config.oban_enabled),
      oban_child(config)
    )
  end

  defp oban_child(_config), do: {Oban, LiqiJobs.Config.oban_options()}

  defp maybe_add(children, true, child), do: children ++ [child]
  defp maybe_add(children, false, _child), do: children
end
