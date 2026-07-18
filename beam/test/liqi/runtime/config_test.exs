defmodule Liqi.Runtime.ConfigTest do
  use ExUnit.Case, async: true

  test "loads the published production example without plaintext secrets" do
    path = "contracts/runtime/examples/runtime-config-v1.json"
    assert {:ok, config} = Liqi.Runtime.Config.from_file(path)
    assert config.environment == "production"
    assert config.persistence_enabled
    assert config.dispatcher_enabled
    assert config.database_secret_ref == "systemd-credential://database-url"
    refute String.contains?(File.read!(path), "password")
  end

  test "accepts the documented one-window V0 field aliases" do
    assert {:ok, config} =
             Liqi.Runtime.Config.from_map(%{
               "schemaVersion" => "0",
               "environment" => "development",
               "service" => %{
                 "name" => "liqi-api",
                 "version" => "0.6.0",
                 "listen" => %{"port" => 4100}
               },
               "database" => %{
                 "secretRef" => "file:///run/liqi/secrets/database-url",
                 "requiredMigrationVersion" => 8
               }
             })

    assert config.release_id == "0.6.0"
    assert config.http_port == 4100
    refute config.persistence_enabled
  end

  test "fails closed for production plaintext or absent secret references" do
    config = %Liqi.Runtime.Config{
      environment: "production",
      release_id: "v1",
      service_identity: "liqi-platform",
      database_secret_ref: "postgres://plaintext",
      endpoint_secret_ref: nil
    }

    assert {:error, :database_secret_reference_required} = Liqi.Runtime.Config.validate(config)
  end
end
