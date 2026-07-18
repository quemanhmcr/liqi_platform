defmodule Liqi.Runtime.ContractSourceTest do
  use ExUnit.Case, async: true

  @required_contracts [
    "contracts/runtime/runtime-config-v1.schema.json",
    "contracts/runtime/runtime-capacity-v1.json",
    "contracts/runtime/internal-envelope-v1.schema.json",
    "contracts/openapi/platform-v1.yaml",
    "contracts/realtime/gateway-v1.schema.json",
    "contracts/realtime/session-resume-v1.schema.json",
    "contracts/errors/error-model-v1.schema.json",
    "contracts/events/wire/event-envelope-v1.schema.json"
  ]

  test "all minimum contracts are committed and error codes are unique" do
    assert Enum.all?(@required_contracts, &File.regular?/1)

    %{"errors" => errors} =
      "contracts/errors/error-codes-v1.json" |> File.read!() |> Jason.decode!()

    codes = Enum.map(errors, & &1["code"])
    assert length(codes) == length(Enum.uniq(codes))
  end

  test "persistence provider consumes callable functions and never writes authority tables directly" do
    adapter =
      File.read!("beam/apps/liqi_persistence/lib/liqi_persistence/runtime_adapter.ex")

    transaction =
      File.read!("beam/apps/liqi_persistence/lib/liqi_persistence/transaction.ex")

    outbox = File.read!("beam/apps/liqi_persistence/lib/liqi_persistence/outbox.ex")

    handoff =
      File.read!("beam/apps/liqi_persistence/lib/liqi_persistence/realtime_handoff.ex")

    observation = File.read!("beam/apps/liqi_persistence/lib/liqi_persistence/probe.ex")

    assert adapter =~ "Transaction.request_probe"
    assert transaction =~ "platform.request_probe_v1"
    assert outbox =~ "platform.claim_outbox_v1"
    assert outbox =~ "platform.apply_probe_effect_and_ack_v1"
    assert handoff =~ "platform.read_realtime_handoff_v1"
    assert observation =~ "platform.observe_probe_v1"

    refute File.exists?("beam/lib/liqi/persistence/postgres_v1.ex")

    refute Enum.any?([adapter, transaction, outbox, handoff, observation], fn source ->
             source =~ "INSERT INTO platform." or source =~ "UPDATE platform." or
               source =~ "DELETE FROM platform."
           end)

    compatibility = File.read!("beam/lib/liqi/persistence/postgres.ex")
    assert compatibility =~ "PostgresV0Compatibility"
    assert compatibility =~ "platform.request_probe_v0"
    refute compatibility =~ "INSERT INTO platform."

    dispatcher = File.read!("beam/lib/liqi/realtime/dispatcher.ex")
    assert dispatcher =~ "read_handoff"
    refute dispatcher =~ "enqueue_outbox"
  end

  test "operator probe authorization is explicit on HTTP and WebSocket seams" do
    openapi = File.read!("contracts/openapi/platform-v1.yaml")
    realtime = File.read!("contracts/realtime/gateway-v1.schema.json")

    assert openapi =~ "ProbeToken"
    assert openapi =~ "x-liqi-probe-token"
    assert openapi =~ "/platform/v1/probes/native"
    assert realtime =~ "x-liqi-probe-token"
    assert realtime =~ "queryParametersForbidden"
  end

  test "test fake is not a production default" do
    config = File.read!("beam/config/config.exs")
    prod = File.read!("beam/config/prod.exs")
    assert config =~ "persistence_adapter: LiqiPersistence.RuntimeAdapter"
    refute prod =~ "Liqi.Persistence.Fake"
  end

  test "release provider commands are direct, versioned, and fail closed" do
    assert File.read!("rel/overlays/bin/liqi-health") == File.read!("beam/scripts/health.sh")
    assert File.read!("rel/overlays/bin/liqi-drain") == File.read!("beam/scripts/drain.sh")

    assert File.exists?("beam/scripts/validate-v1-source.sh")
    assert File.exists?("beam/scripts/run-v1-integration.sh")
    assert File.exists?("beam/scripts/verify-v1-release.sh")
    assert File.exists?("docs/adr/1001-v1-provider-contract-mismatches.md")

    source_gate = File.read!("beam/scripts/validate-v1-source.sh")
    assert source_gate =~ "mix deps.get --locked"
    assert source_gate =~ "shutil.rmtree('_build/test'"
    refute source_gate =~ "deps.clean --all"

    for path <- [
          "contracts/runtime/runtime-source-result-v1.schema.json",
          "contracts/runtime/runtime-integration-result-v1.schema.json",
          "contracts/runtime/runtime-artifact-result-v1.schema.json",
          "beam/release/mix-release-provider-v1.example.json"
        ] do
      assert {:ok, _} = path |> File.read!() |> Jason.decode()
    end
  end

  test "live platform probe is direct, bounded, and keeps credentials out of argv and query" do
    executable = File.read!("beam/bin/platform-probe")
    implementation = File.read!("beam/scripts/platform_probe.py")
    source_gate = File.read!("beam/scripts/validate-v1-source.sh")
    runtime_config = File.read!("beam/config/runtime.exs")

    assert executable =~ "beam.scripts.platform_probe"
    assert implementation =~ "LIQI_PROBE_AUTH_TOKEN"
    assert implementation =~ "x-liqi-probe-token"
    assert implementation =~ "MAX_WS_FRAME"
    assert implementation =~ "socket.create_connection"
    assert implementation =~ "ssl.create_default_context"
    refute implementation =~ "--auth-token"
    refute implementation =~ "?token="
    refute implementation =~ "&token="
    refute implementation =~ "authToken"
    assert source_gate =~ "python -m unittest discover -s beam/tests"
    assert runtime_config =~ "CREDENTIALS_DIRECTORY"
    assert runtime_config =~ "LIQI_CREDENTIALS_DIRECTORY"
  end

  test "database provider uses consumer-owned resource lifecycle" do
    config = File.read!("beam/config/config.exs")
    supervisor = File.read!("beam/lib/liqi/runtime/supervisor.ex")
    mix = File.read!("mix.exs")

    assert mix =~ ~s({:liqi_persistence, path: "beam/apps/liqi_persistence"})
    assert mix =~ ~s({:liqi_jobs, path: "beam/apps/liqi_jobs"})
    assert config =~ "start_repos: false"
    assert config =~ "start_oban: false"
    assert config =~ "command: Liqi.Persistence.ApiRepo"
    assert config =~ "realtime: Liqi.Persistence.RealtimeRepo"
    assert config =~ "worker: Liqi.Persistence.WorkerRepo"
    assert supervisor =~ "LiqiJobs.Config.oban_options()"
    refute supervisor =~ "name: Liqi.Oban"
    runtime_example = File.read!("contracts/runtime/examples/runtime-config-v1.json")
    assert runtime_example =~ "role-url-bundle-v1"
    assert runtime_example =~ "database-role-urls"
  end
end
