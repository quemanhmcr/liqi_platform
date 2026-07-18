defmodule LiqiPersistence.Application do
  @moduledoc false
  use Application

  @impl true
  def start(_type, _args) do
    children =
      if Application.get_env(:liqi_persistence, :start_repos, false) do
        LiqiPersistence.Repos.provider_children()
      else
        []
      end

    Supervisor.start_link(children, strategy: :one_for_one, name: LiqiPersistence.Supervisor)
  end
end
