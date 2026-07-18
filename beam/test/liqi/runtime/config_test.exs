defmodule Liqi.Runtime.ConfigTest do
  use ExUnit.Case, async: true

  test "loads the production bundle reference without plaintext secrets" do
    path = "contracts/runtime/examples/runtime-config-v1.json"
    assert {:ok, config} = Liqi.Runtime.Config.from_file(path)
    assert config.environment == "production"
    assert config.persistence_enabled
    assert config.dispatcher_enabled
    assert config.database_secret_ref == "systemd-credential://database-role-urls"
    assert config.database_credential_format == "role-url-bundle-v1"
    assert config.oban_concurrency == 6
    assert config.probe_token_ref == "systemd-credential://platform-probe-token"
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
    assert config.schema_version == "0"
    assert config.database_secret_ref == "file:///run/liqi/secrets/database-url"
    refute config.persistence_enabled
  end

  test "requires the V1 credential bundle format" do
    config = production_config(database_credential_format: "role-secret-refs-v1")

    assert {:error, :database_credential_format_invalid} =
             Liqi.Runtime.Config.validate(config)
  end

  test "requires a materialized database bundle in production" do
    config = production_config(database_secret_ref: nil)
    assert {:error, :database_secret_reference_required} = Liqi.Runtime.Config.validate(config)
  end

  test "requires a materialized platform-probe token in production" do
    config = production_config(probe_token_ref: nil)
    assert {:error, :probe_token_reference_required} = Liqi.Runtime.Config.validate(config)
  end

  test "Oban concurrency matches the provider active queue policy" do
    assert {:ok, config} = Liqi.Runtime.Config.from_environment()
    assert config.oban_concurrency == 6
    assert config.oban_concurrency == LiqiJobs.QueuePolicy.active_concurrency()

    assert {:error, :oban_concurrency_must_match_provider_policy} =
             Liqi.Runtime.Config.validate(%{config | oban_concurrency: 4})
  end

  defp production_config(overrides) do
    struct!(
      Liqi.Runtime.Config,
      Keyword.merge(
        [
          schema_version: "1",
          environment: "production",
          release_id: "liqi-v1-test",
          service_identity: "liqi-platform",
          database_secret_ref: "systemd-credential://database-role-urls",
          database_credential_format: "role-url-bundle-v1",
          endpoint_secret_ref: "systemd-credential://endpoint-secret",
          drain_token_ref: "systemd-credential://drain-token",
          probe_token_ref: "systemd-credential://platform-probe-token",
          oban_concurrency: 6
        ],
        overrides
      )
    )
  end
end
