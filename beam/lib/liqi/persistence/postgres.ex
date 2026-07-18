defmodule Liqi.Persistence.PostgresV0Compatibility do
  @moduledoc """
  Route-scoped V0 rollback adapter. It is never the production default for V1.

  Removal condition: delete after the V0 rollback window closes and Senior 2's callable V1 seam is
  integrated. This adapter cannot claim V1 durable idempotency or the V1 handoff cursor.
  """
  @behaviour Liqi.Persistence.Adapter

  alias Ecto.Adapters.SQL

  @impl true
  def readiness(required_version) do
    query = "SELECT ready, current_version FROM platform.database_readiness_v0($1)"

    case SQL.query(Liqi.Persistence.ApiRepo, query, [required_version], timeout: 2_000) do
      {:ok, %{rows: [[true, version]]}} when version >= required_version -> :ok
      {:ok, %{rows: [[false, version]]}} -> {:error, {:migration_not_ready, version}}
      {:ok, result} -> {:error, {:unexpected_readiness_result, result.num_rows}}
      {:error, error} -> {:error, classify(error)}
    end
  end

  @impl true
  def request_probe(%Liqi.Persistence.ProbeCommand{} = command) do
    envelope = command.envelope
    probe_id = command.probe_id
    event_id = Liqi.Persistence.ProbeCommand.event_id(command)

    query = """
    SELECT platform.request_probe_v0($1::uuid, $2::uuid, $3::timestamptz,
      $4::uuid, $5::uuid, $6::jsonb)
    """

    params = [
      probe_id,
      event_id,
      DateTime.utc_now(),
      envelope.correlation_id,
      envelope.causation_id,
      %{
        "protocolVersion" => envelope.protocol_version,
        "messageId" => envelope.message_id,
        "deadline" => envelope.deadline
      }
    ]

    Liqi.Runtime.Budgets.with_permit(:database, fn ->
      Liqi.Persistence.ApiRepo.transaction(fn ->
        case SQL.query(Liqi.Persistence.ApiRepo, query, params, timeout: timeout(envelope)) do
          {:ok, %{rows: [[^event_id]]}} ->
            %{probe_id: probe_id, event_id: event_id, status: "accepted"}

          {:ok, %{rows: [[returned]]}} ->
            Liqi.Persistence.ApiRepo.rollback({:unexpected_event_id, returned})

          {:error, error} ->
            Liqi.Persistence.ApiRepo.rollback(classify(error))
        end
      end)
    end)
    |> normalize_transaction()
  end

  @impl true
  def claim_probe_events(consumer_id, batch_size) do
    query = "SELECT * FROM platform.claim_outbox_v0($1, $2, 30)"

    case SQL.query(Liqi.Persistence.WorkerRepo, query, [consumer_id, batch_size], timeout: 3_000) do
      {:ok, result} -> {:ok, rows(result)}
      {:error, error} -> {:error, classify(error)}
    end
  end

  @impl true
  def apply_probe_effect(event_id, claim_token, consumer_id) do
    query = "SELECT platform.apply_probe_effect_and_ack_v0($1::uuid, $2::uuid, $3)"

    case SQL.query(Liqi.Persistence.WorkerRepo, query, [event_id, claim_token, consumer_id],
           timeout: 3_000
         ) do
      {:ok, %{rows: [[status]]}} -> {:ok, status}
      {:error, error} -> {:error, classify(error)}
    end
  end

  @impl true
  def fail_event(event_id, claim_token, consumer_id, error_code, retry_at) do
    query = "SELECT platform.fail_outbox_v0($1::uuid, $2::uuid, $3, $4, $5::timestamptz)"

    case SQL.query(
           Liqi.Persistence.WorkerRepo,
           query,
           [event_id, claim_token, consumer_id, error_code, retry_at],
           timeout: 3_000
         ) do
      {:ok, %{rows: [[status]]}} -> {:ok, status}
      {:error, error} -> {:error, classify(error)}
    end
  end

  @impl true
  def read_handoff(after_cursor, batch_size) do
    query = "SELECT * FROM platform.read_realtime_handoff_v0($1, $2)"

    case SQL.query(Liqi.Persistence.RealtimeRepo, query, [after_cursor, batch_size], timeout: 3_000) do
      {:ok, result} -> {:ok, rows(result)}
      {:error, error} -> {:error, classify(error)}
    end
  end

  @known_columns %{
    "handoff_id" => :handoff_id,
    "event_id" => :event_id,
    "claim_token" => :claim_token,
    "attempt_no" => :attempt_no,
    "schema_version" => :schema_version,
    "event_type" => :event_type,
    "event_version" => :event_version,
    "occurred_at" => :occurred_at,
    "producer" => :producer,
    "correlation_id" => :correlation_id,
    "causation_id" => :causation_id,
    "aggregate_key" => :aggregate_key,
    "ordering_key" => :ordering_key,
    "payload" => :payload,
    "metadata" => :metadata,
    "lease_expires_at" => :lease_expires_at,
    "recorded_at" => :recorded_at
  }

  defp rows(%{columns: columns, rows: rows}) do
    with {:ok, keys} <- map_columns(columns) do
      Enum.map(rows, &Map.new(Enum.zip(keys, &1)))
    else
      {:error, column} -> raise ArgumentError, "unexpected database provider column: #{column}"
    end
  end

  defp map_columns(columns) do
    Enum.reduce_while(columns, {:ok, []}, fn column, {:ok, acc} ->
      case Map.fetch(@known_columns, column) do
        {:ok, key} -> {:cont, {:ok, [key | acc]}}
        :error -> {:halt, {:error, column}}
      end
    end)
    |> case do
      {:ok, reversed} -> {:ok, Enum.reverse(reversed)}
      error -> error
    end
  end

  defp timeout(envelope), do: max(min(Liqi.Runtime.Envelope.remaining_ms(envelope), 5_000), 1)
  defp normalize_transaction({:ok, result}), do: {:ok, result}
  defp normalize_transaction({:error, :capacity}), do: {:error, :database_capacity}
  defp normalize_transaction({:error, reason}), do: {:error, reason}

  defp classify(%Postgrex.Error{postgres: %{code: :unique_violation}}), do: :idempotency_conflict
  defp classify(%Postgrex.Error{postgres: %{code: code}}), do: {:postgres, code}
  defp classify(%DBConnection.ConnectionError{}), do: :database_unavailable
  defp classify(_), do: :database_error
end
