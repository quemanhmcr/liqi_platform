defmodule LiqiJobs.Config do
  @moduledoc "Oban configuration compatible with PgBouncer transaction pooling on one BEAM node."

  def oban_options do
    [
      name: LiqiJobs.Oban,
      repo: LiqiPersistence.Repos.worker(),
      prefix: "oban",
      engine: Oban.Engines.Basic,
      notifier: {Oban.Notifiers.PG, namespace: :liqi_jobs_v1},
      peer: Oban.Peers.Database,
      queues: LiqiJobs.QueuePolicy.queues(),
      stage_interval: 1_000,
      shutdown_grace_period: 60_000,
      plugins: [
        {Oban.Plugins.Pruner, max_age: 2_592_000, limit: 500},
        {Oban.Plugins.Lifeline, rescue_after: 1_800}
      ]
    ]
  end
end
