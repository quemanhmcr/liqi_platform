defmodule LiqiPersistence.Transaction do
  @moduledoc "Transaction API for V1 durable commands."
  alias LiqiPersistence.{Error, Query, Repos}

  @probe_sql """
  SELECT
    probe_id::text,
    event_id::text,
    aggregate_version,
    handoff_cursor,
    duplicate,
    status,
    outcome
  FROM platform.request_probe_v1(
    $1::text::uuid,
    $2::text::uuid,
    $3::text,
    $4::text,
    $5::text,
    $6::bigint,
    $7::timestamptz,
    $8::text::uuid,
    $9::text::uuid,
    $10::jsonb,
    $11::timestamptz,
    $12::jsonb
  )
  """

  @spec request_probe(map()) :: {:ok, map()} | {:error, Error.t() | term()}
  def request_probe(attributes) when is_map(attributes) do
    repo = Repos.command()

    with {:ok, params} <- probe_params(attributes) do
      case repo.transaction(
             fn ->
               case Query.one(repo, @probe_sql, params, timeout: 5_000) do
                 {:ok, result} -> result
                 {:error, error} -> repo.rollback(error)
               end
             end,
             timeout: 5_000
           ) do
        {:ok, result} -> {:ok, result}
        {:error, error} -> {:error, error}
      end
    end
  end

  def request_probe(_), do: {:error, :invalid_attributes}

  @spec transaction((-> term()), keyword()) :: {:ok, term()} | {:error, term()}
  def transaction(fun, opts \\ []) when is_function(fun, 0),
    do: Repos.command().transaction(fun, opts)

  defp probe_params(attributes) do
    required = [
      :probe_id,
      :event_id,
      :idempotency_scope,
      :idempotency_key,
      :request_fingerprint,
      :expected_version
    ]

    if Enum.all?(required, &Map.has_key?(attributes, &1)) do
      with {:ok, probe_id} <- uuid(attributes.probe_id),
           {:ok, event_id} <- uuid(attributes.event_id),
           {:ok, correlation_id} <- optional_uuid(Map.get(attributes, :correlation_id)),
           {:ok, causation_id} <- optional_uuid(Map.get(attributes, :causation_id)),
           true <- valid_fingerprint?(attributes.request_fingerprint) do
        {:ok,
         [
           probe_id,
           event_id,
           attributes.idempotency_scope,
           attributes.idempotency_key,
           attributes.request_fingerprint,
           attributes.expected_version,
           Map.get(attributes, :occurred_at, DateTime.utc_now()),
           correlation_id,
           causation_id,
           Map.get(attributes, :trace_context, %{}),
           Map.get(attributes, :deadline_at),
           Map.get(attributes, :metadata, %{})
         ]}
      else
        false -> {:error, :invalid_request_fingerprint}
        error -> error
      end
    else
      {:error, :missing_required_attribute}
    end
  end

  defp valid_fingerprint?(value) when is_binary(value),
    do: String.match?(value, ~r/\A[0-9a-f]{64}\z/)

  defp valid_fingerprint?(_), do: false
  defp optional_uuid(nil), do: {:ok, nil}
  defp optional_uuid(value), do: uuid(value)
  defp uuid(value), do: Ecto.UUID.cast(value)
end
