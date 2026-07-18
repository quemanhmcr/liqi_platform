defmodule LiqiJobs.DatabaseIntegrationTest do
  use ExUnit.Case, async: false

  @moduletag skip: System.get_env("LIQI_DATABASE_INTEGRATION") != "1"

  test "Oban persists bounded scheduled maintenance work in migration 14 storage" do
    changeset =
      LiqiJobs.MaintenanceWorker.new(
        %{"operation" => "prune_v1"},
        schedule_in: 3_600
      )

    assert {:ok,
            %Oban.Job{
              queue: "cleanup",
              worker: "LiqiJobs.MaintenanceWorker",
              max_attempts: 5,
              state: "scheduled"
            } = job} = Oban.insert(LiqiJobs.Oban, changeset)

    assert :ok = Oban.cancel_job(LiqiJobs.Oban, job.id)
  end
end
