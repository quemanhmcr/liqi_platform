defmodule LiqiJobs.Application do
  @moduledoc false
  use Application

  @impl true
  def start(_type, _args) do
    children =
      if Application.get_env(:liqi_jobs, :start_oban, false) do
        [{Oban, LiqiJobs.Config.oban_options()}]
      else
        []
      end

    Supervisor.start_link(children, strategy: :one_for_one, name: LiqiJobs.Supervisor)
  end
end
