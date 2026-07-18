defmodule LiqiPersistence.Error do
  @moduledoc "Stable persistence errors exposed to runtime consumers."
  defexception [:code, :retryable, :details]

  @type t :: %__MODULE__{code: atom(), retryable: boolean(), details: map()}

  @impl true
  def message(%__MODULE__{code: code, retryable: retryable}) do
    "persistence error #{code} (retryable=#{retryable})"
  end

  @spec from_exception(Exception.t()) :: t()
  def from_exception(%Postgrex.Error{postgres: postgres}) when is_map(postgres) do
    pg_code = Map.get(postgres, :pg_code) || Map.get(postgres, "pg_code") || "unknown"

    case pg_code do
      "LQ001" -> new(:idempotency_conflict, false, postgres)
      "LQ002" -> new(:stale_aggregate_version, false, postgres)
      "LQ003" -> new(:probe_identity_mismatch, false, postgres)
      "LQ004" -> new(:realtime_cursor_gap, false, postgres)
      "40001" -> new(:serialization_failure, true, postgres)
      "40P01" -> new(:deadlock_detected, true, postgres)
      "55P03" -> new(:lock_unavailable, true, postgres)
      "57014" -> new(:query_cancelled, true, postgres)
      "23505" -> new(:unique_violation, false, postgres)
      "23503" -> new(:foreign_key_violation, false, postgres)
      "23514" -> new(:check_violation, false, postgres)
      _ -> new(:database_error, false, %{pg_code: pg_code})
    end
  end

  def from_exception(%DBConnection.ConnectionError{}),
    do: new(:database_unavailable, true, %{})

  def from_exception(exception),
    do: new(:database_error, false, %{exception: inspect(exception.__struct__)})

  defp new(code, retryable, details),
    do: %__MODULE__{code: code, retryable: retryable, details: sanitize(details)}

  defp sanitize(details) do
    details
    |> Map.take([
      :code,
      :pg_code,
      :constraint,
      :table,
      :schema,
      :detail,
      "code",
      "pg_code",
      "constraint",
      "table",
      "schema",
      "detail"
    ])
    |> Map.new(fn {key, value} -> {to_string(key), value} end)
  end
end
