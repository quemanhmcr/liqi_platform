defmodule Liqi.Persistence.RepoConfigTest do
  use ExUnit.Case, async: false

  @compatibility_refs [
    "LIQI_API_DATABASE_SECRET_REF",
    "LIQI_REALTIME_DATABASE_SECRET_REF",
    "LIQI_WORKER_DATABASE_SECRET_REF"
  ]

  setup do
    previous = Application.get_env(:liqi_platform, :runtime_config)
    old_env = Map.new(@compatibility_refs, &{&1, System.get_env(&1)})
    Enum.each(@compatibility_refs, &System.delete_env/1)

    on_exit(fn ->
      if previous,
        do: Application.put_env(:liqi_platform, :runtime_config, previous),
        else: Application.delete_env(:liqi_platform, :runtime_config)

      Enum.each(old_env, fn {name, value} ->
        if value, do: System.put_env(name, value), else: System.delete_env(name)
      end)
    end)

    :ok
  end

  test "one V1 secret bundle yields three least-privilege Repo URLs" do
    with_bundle(valid_bundle(), fn ->
      assert {:ok, api} = Liqi.Persistence.RepoConfig.init(:api, [])
      assert {:ok, realtime} = Liqi.Persistence.RepoConfig.init(:realtime, [])
      assert {:ok, worker} = Liqi.Persistence.RepoConfig.init(:worker, [])
      assert api[:url] =~ "liqi_api"
      assert realtime[:url] =~ "liqi_realtime"
      assert worker[:url] =~ "liqi_worker"
      assert Enum.map([api, realtime, worker], & &1[:pool_size]) == [12, 4, 6]
      assert Enum.all?([api, realtime, worker], &(&1[:prepare] == :unnamed))
    end)
  end

  test "missing, extra, or swapped roles fail closed" do
    for bundle <- [
          Map.delete(valid_bundle(), "worker"),
          Map.put(valid_bundle(), "admin", "postgresql://liqi_owner:x@127.0.0.1/liqi"),
          Map.put(valid_bundle(), "worker", valid_bundle()["command"])
        ] do
      with_bundle(bundle, fn ->
        assert {:error, {:database_secret_unavailable, :worker, _reason}} =
                 Liqi.Persistence.RepoConfig.init(:worker, [])
      end)
    end
  end

  test "production requires loopback PgBouncer and the liqi database" do
    bundle =
      Map.put(
        valid_bundle(),
        "command",
        "postgresql://liqi_api:secret@database.internal:5432/liqi"
      )

    with_bundle(bundle, fn ->
      config = Application.fetch_env!(:liqi_platform, :runtime_config)

      Application.put_env(:liqi_platform, :runtime_config, %{
        config
        | environment: "production",
          endpoint_secret_ref: "systemd-credential://endpoint-secret",
          drain_token_ref: "systemd-credential://drain-token",
          probe_token_ref: "systemd-credential://probe-token"
      })

      assert {:error,
              {:database_secret_unavailable, :api, :database_endpoint_not_loopback_pgbouncer}} =
               Liqi.Persistence.RepoConfig.init(:api, [])
    end)
  end

  defp with_bundle(bundle, fun) do
    path = Path.join(System.tmp_dir!(), "liqi-db-bundle-#{System.unique_integer([:positive])}.json")
    File.write!(path, Jason.encode!(bundle))

    Application.put_env(:liqi_platform, :runtime_config, %Liqi.Runtime.Config{
      environment: "test",
      release_id: "test",
      service_identity: "liqi-platform",
      schema_version: "1",
      database_secret_ref: file_reference(path),
      database_credential_format: "role-url-bundle-v1",
      endpoint_secret_ref: "systemd-credential://endpoint-secret",
      drain_token_ref: "systemd-credential://drain-token",
      probe_token_ref: "systemd-credential://platform-probe-token",
      oban_concurrency: 6
    })

    try do
      fun.()
    after
      File.rm(path)
    end
  end

  defp valid_bundle do
    %{
      "command" => "postgresql://liqi_api:secret@127.0.0.1:6432/liqi",
      "realtime" => "postgresql://liqi_realtime:secret@127.0.0.1:6432/liqi",
      "worker" => "postgresql://liqi_worker:secret@127.0.0.1:6432/liqi"
    }
  end

  defp file_reference(path) do
    normalized = path |> Path.expand() |> String.replace("\\", "/")
    "file://#{normalized}"
  end
end
