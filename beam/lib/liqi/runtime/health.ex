defmodule Liqi.Runtime.Health do
  @moduledoc false

  def live do
    {:ok, config} = Liqi.Runtime.Config.load()

    %{
      status: "live",
      service: config.service_identity,
      releaseId: config.release_id,
      checks: [%{name: "beam", status: "up"}]
    }
  end

  def ready do
    {:ok, config} = Liqi.Runtime.Config.load()

    checks = [
      dependency_check("database", fn -> adapter().readiness(config.required_migration_version) end),
      dependency_check("native", &Liqi.Native.Kernel.readiness/0),
      process_check("runtime_supervisor", Liqi.Runtime.Supervisor)
    ]

    cond do
      Liqi.Runtime.State.draining?() -> response("draining", config, checks)
      Enum.all?(checks, &(&1.status == "up")) -> response("ready", config, checks)
      true -> response("not_ready", config, checks)
    end
  end

  def metadata do
    {:ok, config} = Liqi.Runtime.Config.load()

    %{
      artifact: "liqi-platform",
      version: Application.spec(:liqi_platform, :vsn) |> to_string(),
      releaseId: config.release_id,
      environment: config.environment,
      sourceRevision: System.get_env("LIQI_SOURCE_REVISION"),
      builtAt: System.get_env("LIQI_BUILT_AT"),
      beam: %{
        elixir: System.version(),
        otp: System.otp_release(),
        schedulers: System.schedulers_online(),
        dirtyCpuSchedulers: :erlang.system_info(:dirty_cpu_schedulers),
        dirtyIoSchedulers: :erlang.system_info(:dirty_io_schedulers),
        asyncThreads: :erlang.system_info(:thread_pool_size)
      },
      contracts: %{
        runtimeConfig: "1",
        internalEnvelope: "1",
        platformApi: "1",
        realtimeGateway: "1",
        sessionResume: "1",
        errorModel: "1",
        eventEnvelope: "1"
      }
    }
  end

  defp response(status, config, checks),
    do: %{
      status: status,
      service: config.service_identity,
      releaseId: config.release_id,
      checks: checks
    }

  defp dependency_check(name, fun) do
    case safe(fun) do
      :ok -> %{name: name, status: "up"}
      {:error, reason} -> %{name: name, status: "down", detail: safe_detail(reason)}
    end
  end

  defp process_check(name, process) do
    if Process.whereis(process),
      do: %{name: name, status: "up"},
      else: %{name: name, status: "down"}
  end

  defp safe(fun) do
    fun.()
  rescue
    _ -> {:error, :exception}
  catch
    :exit, _ -> {:error, :exit}
  end

  defp safe_detail(reason),
    do: reason |> inspect(limit: 4, printable_limit: 64) |> String.slice(0, 128)

  defp adapter, do: Application.fetch_env!(:liqi_platform, :persistence_adapter)
end
