defmodule Liqi.Runtime.Drain do
  @moduledoc "Transitions runtime admission and durable work fetching to draining semantics."

  def begin do
    begin_runtime_drain()

    case pause_oban() do
      :ok ->
        :ok

      {:error, reason} ->
        :telemetry.execute([:liqi, :runtime, :drain, :error], %{count: 1}, %{
          component: :oban,
          reason: inspect(reason, limit: 4, printable_limit: 64)
        })

        {:error, {:oban_pause_failed, reason}}
    end
  end

  defp begin_runtime_drain do
    case Process.whereis(Liqi.Runtime.State) do
      nil -> :ok
      _pid -> Liqi.Runtime.State.begin_drain()
    end
  end

  defp pause_oban do
    case Oban.whereis(LiqiJobs.Oban) do
      nil -> :ok
      _pid -> Oban.pause_all_queues(LiqiJobs.Oban, local_only: true)
    end
  rescue
    error -> {:error, error.__struct__}
  catch
    :exit, reason -> {:error, {:exit, reason}}
  end
end
