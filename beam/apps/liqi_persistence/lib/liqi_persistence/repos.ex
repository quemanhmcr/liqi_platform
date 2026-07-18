defmodule LiqiPersistence.CommandRepo do
  use Ecto.Repo, otp_app: :liqi_persistence, adapter: Ecto.Adapters.Postgres

  @impl true
  def init(_context, config),
    do: {:ok, Keyword.merge(config, LiqiPersistence.Config.repo_options(:command))}
end

defmodule LiqiPersistence.RealtimeRepo do
  use Ecto.Repo, otp_app: :liqi_persistence, adapter: Ecto.Adapters.Postgres

  @impl true
  def init(_context, config),
    do: {:ok, Keyword.merge(config, LiqiPersistence.Config.repo_options(:realtime))}
end

defmodule LiqiPersistence.WorkerRepo do
  use Ecto.Repo, otp_app: :liqi_persistence, adapter: Ecto.Adapters.Postgres

  @impl true
  def init(_context, config),
    do: {:ok, Keyword.merge(config, LiqiPersistence.Config.repo_options(:worker))}
end

defmodule LiqiPersistence.Repos do
  @moduledoc "Configurable Repo seam so the runtime owner may keep a single supervised pool set."

  @defaults %{
    command: LiqiPersistence.CommandRepo,
    realtime: LiqiPersistence.RealtimeRepo,
    worker: LiqiPersistence.WorkerRepo
  }

  @spec command() :: module()
  def command, do: fetch!(:command)

  @spec realtime() :: module()
  def realtime, do: fetch!(:realtime)

  @spec worker() :: module()
  def worker, do: fetch!(:worker)

  @spec provider_children() :: [module()]
  def provider_children do
    :liqi_persistence
    |> Application.get_env(:repos, @defaults)
    |> Map.values()
    |> Enum.uniq()
  end

  defp fetch!(role) do
    :liqi_persistence
    |> Application.get_env(:repos, @defaults)
    |> Map.fetch!(role)
  end
end
