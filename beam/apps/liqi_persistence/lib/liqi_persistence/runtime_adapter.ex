defmodule LiqiPersistence.RuntimeAdapter do
  @moduledoc """
  Direct runtime adapter for the Senior 1 persistence callback surface.

  The consumer remains owner of command identity and supervision. This module dynamically invokes
  the consumer command module's `event_id/1` function and never duplicates that identity algorithm.
  """

  alias LiqiPersistence.{Error, Outbox, Probe, Readiness, RealtimeHandoff, Transaction}

  @spec readiness(pos_integer()) :: :ok | {:error, term()}
  def readiness(required_migration_version) do
    case Readiness.check(required_migration_version) do
      {:ok, %{"ready" => true, "write_ready" => true}} -> :ok
      {:ok, result} -> {:error, readiness_reason(result)}
      {:error, error} -> normalize_error(error)
    end
  end

  @spec request_probe(map()) :: {:ok, map()} | {:error, term()}
  def request_probe(command) when is_map(command) do
    with {:ok, event_id} <- event_id(command),
         {:ok, deadline_at} <- deadline_at(fetch(command, :envelope)),
         {:ok, result} <-
           Transaction.request_probe(%{
             probe_id: fetch(command, :probe_id),
             event_id: event_id,
             idempotency_scope: fetch(command, :idempotency_scope),
             idempotency_key: fetch(command, :idempotency_key),
             request_fingerprint: fetch(command, :request_fingerprint),
             expected_version: fetch(command, :expected_version),
             correlation_id: fetch(fetch(command, :envelope), :correlation_id),
             causation_id: fetch(fetch(command, :envelope), :causation_id),
             trace_context: fetch(fetch(command, :envelope), :trace_context) || %{},
             deadline_at: deadline_at,
             metadata: %{
               "commandMessageId" => fetch(fetch(command, :envelope), :message_id)
             }
           }) do
      {:ok,
       %{
         probe_id: result["probe_id"],
         event_id: result["event_id"],
         aggregate_version: result["aggregate_version"],
         handoff_cursor: result["handoff_cursor"],
         duplicate: result["duplicate"],
         status: result["status"]
       }}
    else
      {:error, error} -> normalize_error(error)
    end
  end

  def request_probe(_), do: {:error, :validation_failed}

  @spec observe_probe(String.t(), String.t()) :: {:ok, map()} | {:error, term()}
  def observe_probe(probe_id, event_id) do
    case Probe.observe(probe_id, event_id) do
      {:ok, nil} ->
        {:error, :not_found}

      {:ok, result} ->
        {:ok,
         %{
           probe_id: result["probe_id"],
           event_id: result["event_id"],
           probe_status: result["probe_status"],
           outbox_state: result["outbox_state"],
           effect_applied: result["effect_applied"],
           terminal: result["terminal"],
           aggregate_version: result["aggregate_version"],
           handoff_cursor: result["handoff_cursor"],
           observed_at: result["observed_at"]
         }}

      {:error, error} ->
        normalize_error(error)
    end
  end

  @spec claim_probe_events(String.t(), pos_integer()) :: {:ok, [map()]} | {:error, term()}
  def claim_probe_events(consumer_id, batch_size) do
    case Outbox.claim(consumer_id, batch_size, 30) do
      {:ok, events} -> {:ok, events}
      {:error, error} -> normalize_error(error)
    end
  end

  @spec apply_probe_effect(String.t(), String.t(), String.t()) ::
          {:ok, String.t()} | {:error, term()}
  def apply_probe_effect(event_id, claim_token, consumer_id) do
    case Outbox.apply_probe_effect(event_id, claim_token, consumer_id) do
      {:ok, status} -> {:ok, status}
      {:error, error} -> normalize_error(error)
    end
  end

  @spec fail_event(String.t(), String.t(), String.t(), String.t(), DateTime.t()) ::
          {:ok, String.t()} | {:error, term()}
  def fail_event(event_id, claim_token, consumer_id, error_code, retry_at) do
    case Outbox.fail(event_id, claim_token, consumer_id, error_code, retry_at) do
      {:ok, status} -> {:ok, status}
      {:error, error} -> normalize_error(error)
    end
  end

  @spec read_handoff(non_neg_integer(), pos_integer()) :: {:ok, [map()]} | {:error, term()}
  def read_handoff(after_cursor, batch_size) do
    case RealtimeHandoff.read(after_cursor, batch_size) do
      {:ok, events} -> {:ok, events}
      {:error, error} -> normalize_error(error)
    end
  end

  defp event_id(%{__struct__: module} = command) when is_atom(module) do
    if function_exported?(module, :event_id, 1) do
      case apply(module, :event_id, [command]) do
        value when is_binary(value) -> {:ok, value}
        _ -> {:error, :validation_failed}
      end
    else
      event_id_from_map(command)
    end
  end

  defp event_id(command), do: event_id_from_map(command)

  defp event_id_from_map(command) do
    case fetch(command, :event_id) do
      value when is_binary(value) -> {:ok, value}
      _ -> {:error, :validation_failed}
    end
  end

  defp deadline_at(envelope) when is_map(envelope) do
    with {:ok, deadline} <- normalize_deadline(fetch(envelope, :deadline)),
         :gt <- DateTime.compare(deadline, DateTime.utc_now()) do
      {:ok, deadline}
    else
      :lt -> {:error, :deadline_exceeded}
      :eq -> {:error, :deadline_exceeded}
      {:error, _} = error -> error
    end
  end

  defp deadline_at(_), do: {:error, :validation_failed}

  defp normalize_deadline(value) when is_integer(value),
    do: DateTime.from_unix(value, :millisecond)

  defp normalize_deadline(%DateTime{} = value), do: {:ok, value}
  defp normalize_deadline(_), do: {:error, :validation_failed}

  defp readiness_reason(result) do
    {:database_not_ready,
     %{
       reason: result["reason"],
       current_version: result["current_version"],
       expected_version: result["expected_version"],
       write_ready: result["write_ready"]
     }}
  end

  defp normalize_error(%Error{code: code}), do: {:error, code}
  defp normalize_error(error) when is_atom(error), do: {:error, error}
  defp normalize_error(error), do: {:error, error}

  defp fetch(map, key) when is_map(map),
    do: Map.get(map, key) || Map.get(map, Atom.to_string(key))
end
