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

  test "Postgres adapter consumes provider functions and never publishes from process memory" do
    v1 = File.read!("beam/lib/liqi/persistence/postgres_v1.ex")
    assert v1 =~ "provider_contract_incomplete"
    refute v1 =~ "platform.request_probe_v0"

    compatibility = File.read!("beam/lib/liqi/persistence/postgres.ex")
    assert compatibility =~ "PostgresV0Compatibility"
    assert compatibility =~ "platform.request_probe_v0"
    refute compatibility =~ "INSERT INTO platform."

    dispatcher = File.read!("beam/lib/liqi/realtime/dispatcher.ex")
    assert dispatcher =~ "read_handoff"
    refute dispatcher =~ "enqueue_outbox"
  end

  test "test fake is not a production default" do
    config = File.read!("beam/config/config.exs")
    prod = File.read!("beam/config/prod.exs")
    assert config =~ "persistence_adapter: Liqi.Persistence.PostgresV1"
    refute prod =~ "Liqi.Persistence.Fake"
  end
end
