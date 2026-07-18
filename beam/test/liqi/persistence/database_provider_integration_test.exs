defmodule Liqi.Persistence.DatabaseProviderIntegrationTest do
  use ExUnit.Case, async: false

  @moduletag skip: System.get_env("LIQI_DATABASE_INTEGRATION") != "1"

  setup_all do
    previous_runtime_config = Application.get_env(:liqi_platform, :runtime_config)
    bundle_reference = System.fetch_env!("LIQI_TEST_DATABASE_ROLE_URLS_REF")

    Application.put_env(:liqi_platform, :runtime_config, %Liqi.Runtime.Config{
      environment: "test",
      release_id: "runtime-database-integration",
      service_identity: "liqi-platform",
      schema_version: "1",
      database_secret_ref: bundle_reference,
      database_credential_format: "role-url-bundle-v1",
      required_migration_version: 8,
      oban_concurrency: 6,
      native_mode: :disabled
    })

    started =
      Enum.map(
        [Liqi.Persistence.ApiRepo, Liqi.Persistence.RealtimeRepo, Liqi.Persistence.WorkerRepo],
        &start_repo!/1
      )

    oban = start_oban!()

    on_exit(fn ->
      stop_owned(oban)
      Enum.each(Enum.reverse(started), &stop_owned/1)

      case previous_runtime_config do
        nil -> Application.delete_env(:liqi_platform, :runtime_config)
        config -> Application.put_env(:liqi_platform, :runtime_config, config)
      end
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
      {:ok, pid} ->
        Process.unlink(pid)
        {pid, true}

      {:error, {:already_started, pid}} ->
        {pid, false}

      {:error, reason} ->
        raise "failed to start #{inspect(repo)}: #{inspect(reason)}"
    end
  end

  defp start_oban! do
    case Oban.start_link(LiqiJobs.Config.oban_options()) do
      {:ok, pid} ->
        Process.unlink(pid)
        {pid, true}

      {:error, {:already_started, pid}} ->
        {pid, false}

      {:error, reason} ->
        raise "failed to start Oban: #{inspect(reason)}"
    end
  end

  defp stop_owned({_pid, false}), do: :ok

  defp stop_owned({pid, true}) when is_pid(pid) do
    if Process.alive?(pid) do
      try do
        Supervisor.stop(pid, :normal, 5_000)
      catch
        :exit, {:noproc, _} -> :ok
        :exit, :noproc -> :ok
      end
    else
      :ok
    end
  end
end
