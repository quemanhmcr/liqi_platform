defmodule Liqi.Runtime.Drain do
  @moduledoc "Transitions runtime admission to fail-closed draining semantics."

  def begin do
    case Process.whereis(Liqi.Runtime.State) do
      nil -> :ok
      _pid -> Liqi.Runtime.State.begin_drain()
    end
  end
end
