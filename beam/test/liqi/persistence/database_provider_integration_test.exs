defmodule Liqi.Persistence.DatabaseProviderIntegrationTest do
  use ExUnit.Case, async: false

  @moduletag skip: System.get_env("LIQI_DATABASE_INTEGRATION") != "1"

  setup_all do
    started =
      Enum.map(
        [Liqi.Persistence.ApiRepo, Liqi.Persistence.RealtimeRepo, Liqi.Persistence.WorkerRepo],
        &start_repo!/1
      )

    oban_pid = start_oban!()

    on_exit(fn ->
      if is_pid(oban_pid) and Process.alive?(oban_pid),
        do: Supervisor.stop(oban_pid, :normal, 5_000)

      Enum.each(Enum.reverse(started), fn pid ->
        if is_pid(pid) and Process.alive?(pid), do: Supervisor.stop(pid, :normal, 5_000)
      end)
    end)

    :ok
  end

  test "root resources consume migration-8 provider semantics end to end" do
    adapter = LiqiPersistence.RuntimeAdapter
    probe_id = Liqi.Runtime.Id.uuid4()
    idempotency_key = "runtime-integration-#{Liqi.Runtime.Id.uuid4()}"

    assert {:ok, envelope} =
             Liqi.Runtime.Envelope.new(
               message_id: Liqi.Runtime.Id.uuid4(),
               timeout_ms: 10_000,
               actor_key: "platform-probe:#{probe_id}",
               priority: :durable,
               payload_type: "platform.probe.requested",
               payload_version: 1,
               payload: %{"clientProbeId" => probe_id}
             )

    assert {:ok, command} =
             Liqi.Persistence.ProbeCommand.new(envelope, probe_id, idempotency_key)

    event_id = Liqi.Persistence.ProbeCommand.event_id(command)

    assert :ok = adapter.readiness(8)

    assert {:ok,
            %{
              probe_id: ^probe_id,
              event_id: ^event_id,
              aggregate_version: 1,
              duplicate: false,
              status: "accepted"
            }} = adapter.request_probe(command)

    assert {:ok, %{event_id: ^event_id, duplicate: true}} = adapter.request_probe(command)

    conflicting_probe_id = Liqi.Runtime.Id.uuid4()

    assert {:ok, conflicting_envelope} =
             Liqi.Runtime.Envelope.new(
               message_id: Liqi.Runtime.Id.uuid4(),
               timeout_ms: 10_000,
               actor_key: "platform-probe:#{conflicting_probe_id}",
               priority: :durable,
               payload_type: "platform.probe.requested",
               payload_version: 1,
               payload: %{"clientProbeId" => conflicting_probe_id}
             )

    assert {:ok, conflicting_command} =
             Liqi.Persistence.ProbeCommand.new(
               conflicting_envelope,
               conflicting_probe_id,
               idempotency_key
             )

    assert {:error, :idempotency_conflict} = adapter.request_probe(conflicting_command)

    assert {:ok, handoff} = adapter.read_handoff(0, 128)

    assert Enum.any?(handoff, fn event ->
             event["event_id"] == event_id and event["protocol_version"] == 1 and
               event["event_type"] == "platform.probe.requested.v1"
           end)

    consumer_id = "runtime-integration-#{Liqi.Runtime.Id.uuid4()}"
    assert {:ok, claimed} = adapter.claim_probe_events(consumer_id, 50)
    event = Enum.find(claimed, &(&1["event_id"] == event_id))
    assert event

    assert {:ok, "acked"} =
             adapter.apply_probe_effect(event_id, event["claim_token"], consumer_id)

    assert {:ok,
            %{
              probe_id: ^probe_id,
              event_id: ^event_id,
              probe_status: "completed",
              outbox_state: "succeeded",
              effect_applied: true,
              terminal: true,
              observed_at: %DateTime{}
            }} = adapter.observe_probe(probe_id, event_id)

    assert {:error, :probe_identity_mismatch} =
             adapter.observe_probe(probe_id, Liqi.Runtime.Id.uuid4())

    changeset =
      LiqiJobs.MaintenanceWorker.new(
        %{"operation" => "prune_v1"},
        schedule_in: 3_600
      )

    assert {:ok, %Oban.Job{queue: "cleanup", state: "scheduled"} = job} =
             Oban.insert(LiqiJobs.Oban, changeset)

    assert :ok = Oban.cancel_job(LiqiJobs.Oban, job.id)
  end

  defp start_repo!(repo) do
    case repo.start_link() do
      {:ok, pid} -> pid
      {:error, {:already_started, pid}} -> pid
      {:error, reason} -> raise "failed to start #{inspect(repo)}: #{inspect(reason)}"
    end
  end

  defp start_oban! do
    case Oban.start_link(LiqiJobs.Config.oban_options()) do
      {:ok, pid} -> pid
      {:error, {:already_started, pid}} -> pid
      {:error, reason} -> raise "failed to start Oban: #{inspect(reason)}"
    end
  end
end
