defmodule LiqiPersistence.Config do
  @moduledoc "Runtime-only PostgreSQL configuration for transaction-pooled Ecto repositories."

  @roles %{
    command: %{
      username: "liqi_api",
      password_env: "LIQI_DATABASE_API_PASSWORD_FILE",
      pool_size: 12,
      timeout: 5_000
    },
    realtime: %{
      username: "liqi_realtime",
      password_env: "LIQI_DATABASE_REALTIME_PASSWORD_FILE",
      pool_size: 4,
      timeout: 3_000
    },
    worker: %{
      username: "liqi_worker",
      password_env: "LIQI_DATABASE_WORKER_PASSWORD_FILE",
      pool_size: 6,
      timeout: 30_000
    }
  }

  @spec repo_options(:command | :realtime | :worker) :: keyword()
  def repo_options(role) do
    role_config = Map.fetch!(@roles, role)

    [
      hostname: System.get_env("LIQI_DATABASE_HOST", "127.0.0.1"),
      port: integer_env!("LIQI_DATABASE_PORT", 6432),
      database: System.get_env("LIQI_DATABASE_NAME", "liqi"),
      username: role_config.username,
      pool_size: role_config.pool_size,
      prepare: :unnamed,
      queue_target: 50,
      queue_interval: 1_000,
      timeout: role_config.timeout,
      connect_timeout: 5_000,
      log: false,
      show_sensitive_data_on_connection_error: false,
      telemetry_prefix: [:liqi, :persistence, role]
    ]
    |> Keyword.put(:password, read_secret!(role_config.password_env))
  end

  @spec pool_sizes() :: %{command: 12, realtime: 4, worker: 6}
  def pool_sizes, do: %{command: 12, realtime: 4, worker: 6}

  defp integer_env!(name, default) do
    case Integer.parse(System.get_env(name, Integer.to_string(default))) do
      {value, ""} when value > 0 -> value
      _ -> raise ArgumentError, "#{name} must be a positive integer"
    end
  end

  defp read_secret!(environment_name) do
    path = System.fetch_env!(environment_name)

    case File.read(path) do
      {:ok, value} ->
        case String.trim(value) do
          "" -> raise ArgumentError, "database credential file is empty"
          secret -> secret
        end

      {:error, reason} ->
        raise ArgumentError, "database credential file is unavailable: #{inspect(reason)}"
    end
  end
end
