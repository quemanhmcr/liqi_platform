defmodule Liqi.Persistence.ProviderIntegrationTest do
  use ExUnit.Case, async: true

  test "provider repos map to the single root-supervised pool set" do
    assert File.read!("beam/config/config.exs") =~
             "persistence_adapter: LiqiPersistence.RuntimeAdapter"

    assert Application.fetch_env!(:liqi_platform, :persistence_adapter) == Liqi.Persistence.Fake

    assert Application.get_env(:liqi_persistence, :start_repos) == false

    assert %{
             command: Liqi.Persistence.ApiRepo,
             realtime: Liqi.Persistence.RealtimeRepo,
             worker: Liqi.Persistence.WorkerRepo
           } = Application.fetch_env!(:liqi_persistence, :repos)

    assert LiqiPersistence.Repos.command() == Liqi.Persistence.ApiRepo
    assert LiqiPersistence.Repos.realtime() == Liqi.Persistence.RealtimeRepo
    assert LiqiPersistence.Repos.worker() == Liqi.Persistence.WorkerRepo

    assert MapSet.new(LiqiPersistence.Repos.provider_children()) ==
             MapSet.new([
               Liqi.Persistence.ApiRepo,
               Liqi.Persistence.RealtimeRepo,
               Liqi.Persistence.WorkerRepo
             ])

    assert Enum.sum(Map.values(LiqiPersistence.Config.pool_sizes())) == 22
  end

  test "provider applications own semantics but no resource lifecycle" do
    assert Application.get_env(:liqi_jobs, :start_oban) == false
    assert Process.whereis(Liqi.Persistence.ApiRepo) == nil
    assert Process.whereis(Liqi.Persistence.RealtimeRepo) == nil
    assert Process.whereis(Liqi.Persistence.WorkerRepo) == nil
    assert Oban.whereis(LiqiJobs.Oban) == nil
  end

  test "provider queue policy is bounded and recovery starts paused" do
    options = LiqiJobs.Config.oban_options()

    assert options[:repo] == Liqi.Persistence.WorkerRepo
    assert options[:name] == LiqiJobs.Oban
    assert options[:prefix] == "oban"
    assert options[:stage_interval] == 1_000
    assert options[:shutdown_grace_period] == 60_000
    assert LiqiJobs.QueuePolicy.active_concurrency() == 6
    assert LiqiJobs.QueuePolicy.configured_concurrency() == 7
    assert {:recovery, [limit: 1, paused: true]} in options[:queues]
  end
end
