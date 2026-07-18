defmodule LiqiJobs.MaintenanceWorker do
  @moduledoc "Bounded retention work; Oban is scheduling authority only, not domain event authority."
  use Oban.Worker,
    queue: :cleanup,
    max_attempts: 5,
    unique: [period: 3_600, keys: [:operation], states: :incomplete]

  @impl Oban.Worker
  def timeout(_job), do: :timer.minutes(1)

  @impl Oban.Worker
  def perform(%Oban.Job{args: %{"operation" => "prune_v1"}}) do
    now = DateTime.utc_now()

    with {:ok, _} <-
           LiqiPersistence.Maintenance.prune_idempotency(DateTime.add(now, -30 * 86_400, :second)),
         {:ok, _} <-
           LiqiPersistence.Maintenance.prune_realtime(DateTime.add(now, -7 * 86_400, :second)),
         {:ok, _} <-
           LiqiPersistence.Maintenance.prune_outbox(
             DateTime.add(now, -30 * 86_400, :second),
             DateTime.add(now, -30 * 86_400, :second)
           ) do
      :ok
    end
  end

  def perform(%Oban.Job{}), do: {:discard, :unsupported_maintenance_operation}
end
