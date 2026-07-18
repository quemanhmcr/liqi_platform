defmodule Liqi.Persistence.PostgresV1 do
  @moduledoc """
  Fail-closed V1 provider adapter.

  Senior 2 has published durable semantics and migration 8, but no callable SQL function seam is
  present in the reviewed provider commit. Returning an explicit unavailable result prevents V0
  functions from becoming the hidden production path for protocol-v1 commands or handoff rows.
  """
  @behaviour Liqi.Persistence.Adapter

  @reason {:provider_contract_incomplete, :database_v1_callable_seam}

  @impl true
  def readiness(_required_migration_version), do: {:error, @reason}
  @impl true
  def request_probe(_command), do: {:error, @reason}
  @impl true
  def observe_probe(_probe_id, _event_id), do: {:error, @reason}
  @impl true
  def claim_probe_events(_consumer_id, _batch_size), do: {:error, @reason}
  @impl true
  def apply_probe_effect(_event_id, _claim_token, _consumer_id), do: {:error, @reason}
  @impl true
  def fail_event(_event_id, _claim_token, _consumer_id, _error_code, _retry_at),
    do: {:error, @reason}

  @impl true
  def read_handoff(_after_cursor, _batch_size), do: {:error, @reason}
end
