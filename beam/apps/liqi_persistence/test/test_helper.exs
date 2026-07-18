ExUnit.start()

if System.get_env("LIQI_DATABASE_INTEGRATION") == "1" do
  {:ok, _} = Application.ensure_all_started(:ecto_sql)

  for repo <- LiqiPersistence.Repos.provider_children() do
    case repo.start_link() do
      {:ok, _pid} -> :ok
      {:error, {:already_started, _pid}} -> :ok
    end
  end
end
