defmodule Liqi.Persistence.RepoConfig do
  @moduledoc false

  @pool_sizes %{api: 12, realtime: 4, worker: 6}

  def init(role, config) do
    reference = System.get_env(reference_env(role))

    with {:ok, url} <- Liqi.Persistence.SecretResolver.resolve(reference) do
      {:ok,
       Keyword.merge(config,
         url: url,
         pool_size: Map.fetch!(@pool_sizes, role),
         queue_target: 50,
         queue_interval: 1_000,
         prepare: :unnamed,
         timeout: timeout(role),
         connect_timeout: 3_000,
         show_sensitive_data_on_connection_error: false,
         log: false
       )}
    else
      {:error, reason} -> {:error, {:database_secret_unavailable, role, reason}}
    end
  end

  defp timeout(:api), do: 5_000
  defp timeout(:realtime), do: 3_000
  defp timeout(:worker), do: 30_000

  defp reference_env(:api), do: "LIQI_API_DATABASE_SECRET_REF"
  defp reference_env(:realtime), do: "LIQI_REALTIME_DATABASE_SECRET_REF"
  defp reference_env(:worker), do: "LIQI_WORKER_DATABASE_SECRET_REF"
end
