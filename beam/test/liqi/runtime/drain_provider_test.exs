defmodule Liqi.Runtime.DrainProviderTest do
  use ExUnit.Case, async: false

  test "drain succeeds when Oban is disabled and closes runtime admission" do
    on_exit(fn -> restart_runtime_state!() end)

    refute Oban.whereis(LiqiJobs.Oban)
    refute Liqi.Runtime.State.draining?()
    assert :ok = Liqi.Runtime.Drain.begin()
    assert Liqi.Runtime.State.draining?()
  end

  defp restart_runtime_state! do
    case Supervisor.terminate_child(Liqi.Runtime.Supervisor, Liqi.Runtime.State) do
      :ok -> :ok
      {:error, :not_found} -> :ok
    end

    case Supervisor.restart_child(Liqi.Runtime.Supervisor, Liqi.Runtime.State) do
      {:ok, _pid} -> :ok
      {:ok, _pid, _info} -> :ok
      {:error, :running} -> :ok
      {:error, reason} -> raise "failed to restart runtime state: #{inspect(reason)}"
    end

    refute Liqi.Runtime.State.draining?()
  end
end
