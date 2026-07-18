defmodule Liqi.Application do
  @moduledoc false
  use Application

  @impl true
  def start(_type, _args) do
    Liqi.Runtime.Supervisor.start_link(name: Liqi.Runtime.Supervisor)
  end

  @impl true
  def prep_stop(state) do
    Liqi.Runtime.Drain.begin()
    state
  end
end
