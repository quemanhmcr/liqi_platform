defmodule Liqi.Runtime.HealthTest do
  use ExUnit.Case, async: false

  defmodule UnavailableNative do
    @behaviour Liqi.Native.Adapter
    def readiness, do: {:error, :artifact_unavailable}
    def sequence_diff(_, _, _), do: {:error, :artifact_unavailable}
  end

  setup do
    old_config = Application.get_env(:liqi_platform, :runtime_config)
    old_native = Application.get_env(:liqi_platform, :native_adapter)
    old_state = :sys.get_state(Liqi.Runtime.State)

    on_exit(fn ->
      restore(:runtime_config, old_config)
      restore(:native_adapter, old_native)
      :sys.replace_state(Liqi.Runtime.State, fn _ -> old_state end)
      Liqi.Persistence.Fake.set_readiness(:ok)
    end)

    :ok
  end

  test "liveness remains green while database readiness is red" do
    Liqi.Persistence.Fake.set_readiness({:error, :database_unavailable})
    assert %{status: "live"} = Liqi.Runtime.Health.live()
    assert %{status: "not_ready", checks: checks} = Liqi.Runtime.Health.ready()
    assert %{name: "database", status: "down"} = Enum.find(checks, &(&1.name == "database"))
  end

  test "required native capability fails readiness closed" do
    config = %Liqi.Runtime.Config{
      environment: "test",
      release_id: "test",
      service_identity: "liqi-platform",
      native_mode: :required
    }

    Application.put_env(:liqi_platform, :runtime_config, config)
    Application.put_env(:liqi_platform, :native_adapter, UnavailableNative)

    assert %{status: "not_ready", checks: checks} = Liqi.Runtime.Health.ready()
    assert %{name: "native", status: "down"} = Enum.find(checks, &(&1.name == "native"))
  end

  test "drain rejects new work while liveness stays green" do
    assert :ok = Liqi.Runtime.Drain.begin()
    assert %{status: "draining"} = Liqi.Runtime.Health.ready()
    assert %{status: "live"} = Liqi.Runtime.Health.live()
    assert {:error, :draining} = Liqi.Runtime.AdmissionController.admit(:probe, :endpoint)
  end

  test "release metadata exposes exact BEAM and contract identity without secrets" do
    metadata = Liqi.Runtime.Health.metadata()
    assert metadata.artifact == "liqi-platform"
    assert metadata.contracts.platformApi == "1"
    assert is_binary(metadata.beam.elixir)
    refute inspect(metadata) =~ "database-url"
  end

  defp restore(key, nil), do: Application.delete_env(:liqi_platform, key)
  defp restore(key, value), do: Application.put_env(:liqi_platform, key, value)
end
