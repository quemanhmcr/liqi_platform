defmodule Liqi.Persistence.Adapter do
  @moduledoc "Consumer port for database-owned durable semantics."

  @callback readiness(required_migration_version :: pos_integer()) :: :ok | {:error, term()}
  @callback request_probe(Liqi.Persistence.ProbeCommand.t()) :: {:ok, map()} | {:error, term()}
  @callback observe_probe(probe_id :: String.t(), event_id :: String.t()) ::
              {:ok, map()} | {:error, term()}
  @callback claim_probe_events(consumer_id :: String.t(), batch_size :: pos_integer()) ::
              {:ok, [map()]} | {:error, term()}
  @callback apply_probe_effect(
              event_id :: String.t(),
              claim_token :: String.t(),
              consumer_id :: String.t()
            ) :: {:ok, String.t()} | {:error, term()}
  @callback fail_event(
              event_id :: String.t(),
              claim_token :: String.t(),
              consumer_id :: String.t(),
              error_code :: String.t(),
              retry_at :: DateTime.t()
            ) :: {:ok, String.t()} | {:error, term()}
  @callback read_handoff(after_cursor :: non_neg_integer(), batch_size :: pos_integer()) ::
              {:ok, [map()]} | {:error, term()}
end
