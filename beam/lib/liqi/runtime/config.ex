defmodule Liqi.Runtime.Config do
  @moduledoc "Loads bounded runtime settings without materializing plaintext secrets."

  @enforce_keys [:environment, :release_id, :service_identity]
  defstruct environment: "local",
            release_id: "dev",
            service_identity: "liqi-platform",
            http_port: 4100,
            websocket_path: "/platform/v1/socket",
            endpoint_secret_ref: nil,
            database_secret_ref: nil,
            required_migration_version: 8,
            actor_partitions: 4,
            actor_idle_ttl_ms: 60_000,
            actor_mailbox_warn: 64,
            actor_mailbox_reject: 128,
            session_queue_capacity: 128,
            session_queue_max_bytes: 1_048_576,
            session_queue_max_age_ms: 30_000,
            max_realtime_message_bytes: 65_536,
            resume_window_ms: 300_000,
            request_body_bytes: 1_048_576,
            request_timeout_ms: 5_000,
            endpoint_concurrency: 256,
            database_concurrency: 24,
            reconnect_concurrency: 64,
            native_concurrency: 2,
            bounded_tasks: 32,
            native_mode: :optional,
            telemetry_endpoint: nil,
            shutdown_deadline_ms: 20_000,
            drain_token_ref: nil,
            handoff_poll_interval_ms: 50,
            handoff_batch_size: 64,
            oban_concurrency: 4,
            persistence_enabled: false,
            dispatcher_enabled: false,
            outbox_worker_enabled: false,
            oban_enabled: false

  @type t :: %__MODULE__{}

  @spec load() :: {:ok, t()} | {:error, term()}
  def load do
    case Application.get_env(:liqi_platform, :runtime_config) do
      %__MODULE__{} = config -> validate(config)
      nil -> load_external()
    end
  end

  @spec from_file(Path.t()) :: {:ok, t()} | {:error, term()}
  def from_file(path) when is_binary(path) do
    with {:ok, contents} <- File.read(path),
         {:ok, decoded} <- Jason.decode(contents),
         {:ok, config} <- from_map(decoded) do
      validate(config)
    else
      {:error, %Jason.DecodeError{}} -> {:error, :invalid_runtime_config_json}
      {:error, reason} -> {:error, reason}
    end
  end

  @spec from_map(map()) :: {:ok, t()} | {:error, term()}
  def from_map(%{"schemaVersion" => version} = map) when version in ["1", "0"] do
    config = %__MODULE__{
      environment: value(map, ["environment"], "local"),
      release_id: value(map, ["releaseId"], value(map, ["service", "version"], "dev")),
      service_identity:
        value(map, ["serviceIdentity"], value(map, ["service", "name"], "liqi-platform")),
      http_port: value(map, ["http", "port"], value(map, ["service", "listen", "port"], 4100)),
      websocket_path: value(map, ["http", "websocketPath"], "/platform/v1/socket"),
      endpoint_secret_ref: value(map, ["http", "secretRef"], nil),
      database_secret_ref:
        value(map, ["database", "secretRef"], value(map, ["databaseSecretRef"], nil)),
      required_migration_version: value(map, ["database", "requiredMigrationVersion"], 8),
      actor_partitions: value(map, ["actors", "partitions"], 4),
      actor_idle_ttl_ms: value(map, ["actors", "idleTtlMs"], 60_000),
      actor_mailbox_warn: value(map, ["actors", "mailboxWarning"], 64),
      actor_mailbox_reject: value(map, ["actors", "mailboxReject"], 128),
      session_queue_capacity: value(map, ["sessions", "queueCapacity"], 128),
      session_queue_max_bytes: value(map, ["sessions", "queueMaxBytes"], 1_048_576),
      session_queue_max_age_ms: value(map, ["sessions", "queueMaxAgeMs"], 30_000),
      resume_window_ms: value(map, ["sessions", "resumeWindowMs"], 300_000),
      reconnect_concurrency: value(map, ["sessions", "reconnectConcurrency"], 64),
      max_realtime_message_bytes: value(map, ["realtime", "maxMessageBytes"], 65_536),
      handoff_poll_interval_ms: value(map, ["realtime", "handoffPollIntervalMs"], 50),
      handoff_batch_size: value(map, ["realtime", "handoffBatchSize"], 64),
      request_body_bytes: value(map, ["requests", "bodyBytes"], 1_048_576),
      request_timeout_ms: value(map, ["requests", "timeoutMs"], 5_000),
      endpoint_concurrency: value(map, ["requests", "endpointConcurrency"], 256),
      bounded_tasks: value(map, ["requests", "boundedTasks"], 32),
      database_concurrency: value(map, ["database", "admissionConcurrency"], 24),
      native_mode: native_mode(value(map, ["native", "mode"], "optional")),
      native_concurrency: value(map, ["native", "concurrency"], 2),
      telemetry_endpoint: value(map, ["telemetry", "endpoint"], nil),
      shutdown_deadline_ms: value(map, ["shutdown", "deadlineMs"], 20_000),
      drain_token_ref: value(map, ["shutdown", "drainTokenRef"], nil),
      oban_concurrency: value(map, ["oban", "concurrency"], 4),
      persistence_enabled: value(map, ["features", "persistence"], version == "1"),
      dispatcher_enabled: value(map, ["features", "realtimeDispatcher"], version == "1"),
      outbox_worker_enabled: value(map, ["features", "outboxWorker"], version == "1"),
      oban_enabled: value(map, ["oban", "enabled"], false)
    }

    {:ok, config}
  rescue
    error in ArgumentError -> {:error, {:invalid_runtime_config, Exception.message(error)}}
  end

  def from_map(_), do: {:error, :unsupported_runtime_config_version}

  @spec from_environment() :: {:ok, t()} | {:error, term()}
  def from_environment do
    config = %__MODULE__{
      environment: env("LIQI_ENVIRONMENT", "local"),
      release_id: env("LIQI_RELEASE_ID", "dev"),
      service_identity: env("LIQI_SERVICE_IDENTITY", "liqi-platform"),
      http_port: int_env("LIQI_HTTP_PORT", 4100),
      endpoint_secret_ref: System.get_env("LIQI_ENDPOINT_SECRET_REF"),
      database_secret_ref: System.get_env("LIQI_DATABASE_SECRET_REF"),
      required_migration_version: int_env("LIQI_REQUIRED_MIGRATION_VERSION", 8),
      actor_partitions: int_env("LIQI_ACTOR_PARTITIONS", 4),
      actor_idle_ttl_ms: int_env("LIQI_ACTOR_IDLE_TTL_MS", 60_000),
      actor_mailbox_warn: int_env("LIQI_ACTOR_MAILBOX_WARN", 64),
      actor_mailbox_reject: int_env("LIQI_ACTOR_MAILBOX_REJECT", 128),
      session_queue_capacity: int_env("LIQI_SESSION_QUEUE_CAPACITY", 128),
      session_queue_max_bytes: int_env("LIQI_SESSION_QUEUE_MAX_BYTES", 1_048_576),
      session_queue_max_age_ms: int_env("LIQI_SESSION_QUEUE_MAX_AGE_MS", 30_000),
      max_realtime_message_bytes: int_env("LIQI_MAX_REALTIME_MESSAGE_BYTES", 65_536),
      request_body_bytes: int_env("LIQI_REQUEST_BODY_BYTES", 1_048_576),
      request_timeout_ms: int_env("LIQI_REQUEST_TIMEOUT_MS", 5_000),
      resume_window_ms: int_env("LIQI_RESUME_WINDOW_MS", 300_000),
      endpoint_concurrency: int_env("LIQI_ENDPOINT_CONCURRENCY", 256),
      database_concurrency: int_env("LIQI_DATABASE_CONCURRENCY", 24),
      reconnect_concurrency: int_env("LIQI_RECONNECT_CONCURRENCY", 64),
      native_concurrency: int_env("LIQI_NATIVE_CONCURRENCY", 2),
      bounded_tasks: int_env("LIQI_BOUNDED_TASKS", 32),
      native_mode: native_mode(env("LIQI_RUST_NATIVE_MODE", "optional")),
      telemetry_endpoint: System.get_env("LIQI_TELEMETRY_ENDPOINT"),
      shutdown_deadline_ms: int_env("LIQI_SHUTDOWN_DEADLINE_MS", 20_000),
      drain_token_ref: System.get_env("LIQI_DRAIN_TOKEN_REF"),
      oban_concurrency: int_env("LIQI_OBAN_CONCURRENCY", 4),
      persistence_enabled: bool_env("LIQI_START_PERSISTENCE", false),
      dispatcher_enabled: bool_env("LIQI_START_REALTIME_DISPATCHER", false),
      outbox_worker_enabled: bool_env("LIQI_START_OUTBOX_WORKER", false),
      oban_enabled: bool_env("LIQI_START_OBAN", false)
    }

    validate(config)
  rescue
    error in ArgumentError -> {:error, {:invalid_environment, Exception.message(error)}}
  end

  @spec validate(t()) :: {:ok, t()} | {:error, term()}
  def validate(%__MODULE__{} = config) do
    cond do
      config.actor_partitions not in 1..64 ->
        {:error, :invalid_actor_partitions}

      config.actor_mailbox_warn < 1 ->
        {:error, :invalid_mailbox_warning}

      config.actor_mailbox_reject <= config.actor_mailbox_warn ->
        {:error, :invalid_mailbox_reject}

      config.session_queue_capacity not in 1..4096 ->
        {:error, :invalid_session_queue_capacity}

      config.session_queue_max_bytes not in 65_536..8_388_608 ->
        {:error, :invalid_session_queue_bytes}

      config.session_queue_max_age_ms not in 1_000..120_000 ->
        {:error, :invalid_session_queue_age}

      config.max_realtime_message_bytes not in 1_024..1_048_576 ->
        {:error, :invalid_realtime_message_size}

      config.request_body_bytes != 1_048_576 ->
        {:error, :request_body_limit_must_match_endpoint}

      config.endpoint_concurrency not in 1..4096 ->
        {:error, :invalid_endpoint_concurrency}

      config.database_concurrency not in 1..35 ->
        {:error, :invalid_database_concurrency}

      config.required_migration_version < 8 ->
        {:error, :invalid_migration_version}

      config.handoff_batch_size not in 1..128 ->
        {:error, :invalid_handoff_batch_size}

      config.environment == "production" and not secret_ref?(config.database_secret_ref) ->
        {:error, :database_secret_reference_required}

      config.environment == "production" and not secret_ref?(config.endpoint_secret_ref) ->
        {:error, :endpoint_secret_reference_required}

      config.environment == "production" and not secret_ref?(config.drain_token_ref) ->
        {:error, :drain_token_reference_required}

      true ->
        {:ok, config}
    end
  end

  defp load_external do
    case System.get_env("LIQI_RUNTIME_CONFIG_PATH") do
      nil -> from_environment()
      path -> from_file(path)
    end
  end

  defp value(map, path, default), do: get_in(map, path) || default
  defp env(name, default), do: System.get_env(name, default)

  defp int_env(name, default) do
    name |> System.get_env(Integer.to_string(default)) |> String.to_integer()
  end

  defp bool_env(name, default) do
    case System.get_env(name) do
      nil -> default
      value when value in ["1", "true"] -> true
      value when value in ["0", "false"] -> false
      value -> raise ArgumentError, "invalid boolean #{name}=#{inspect(value)}"
    end
  end

  defp native_mode(value) when value in [:disabled, :optional, :required], do: value
  defp native_mode("disabled"), do: :disabled
  defp native_mode("optional"), do: :optional
  defp native_mode("required"), do: :required
  defp native_mode(other), do: raise(ArgumentError, "invalid native mode: #{inspect(other)}")

  defp secret_ref?(value) when is_binary(value),
    do: String.starts_with?(value, ["file://", "oci-vault://", "systemd-credential://"])

  defp secret_ref?(_), do: false
end
