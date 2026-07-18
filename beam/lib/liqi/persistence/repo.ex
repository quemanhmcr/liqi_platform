defmodule Liqi.Persistence.ApiRepo do
  use Ecto.Repo, otp_app: :liqi_platform, adapter: Ecto.Adapters.Postgres

  @impl true
  def init(_type, config), do: Liqi.Persistence.RepoConfig.init(:api, config)
end

defmodule Liqi.Persistence.RealtimeRepo do
  use Ecto.Repo, otp_app: :liqi_platform, adapter: Ecto.Adapters.Postgres

  @impl true
  def init(_type, config), do: Liqi.Persistence.RepoConfig.init(:realtime, config)
end

defmodule Liqi.Persistence.WorkerRepo do
  use Ecto.Repo, otp_app: :liqi_platform, adapter: Ecto.Adapters.Postgres

  @impl true
  def init(_type, config), do: Liqi.Persistence.RepoConfig.init(:worker, config)
end
