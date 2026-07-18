defmodule Liqi.Persistence.Fake do
  @moduledoc "Test-only compatibility fake; never a production default."
  @behaviour Liqi.Persistence.Adapter
  use Agent

  def start_link(_opts), do: Agent.start_link(fn -> initial() end, name: __MODULE__)

  def reset do
    ensure_started()
    Agent.update(__MODULE__, fn _ -> initial() end)
  end

  def set_readiness(value) do
    ensure_started()
    Agent.update(__MODULE__, &Map.put(&1, :readiness, value))
  end

  @impl true
  def readiness(_required), do: get(& &1.readiness)

  @impl true
  def request_probe(%Liqi.Persistence.ProbeCommand{} = command) do
    ensure_started()

    Agent.get_and_update(__MODULE__, fn state ->
      identity = {command.idempotency_scope, command.idempotency_key}

      case state.idempotency do
        %{^identity => %{fingerprint: fingerprint, outcome: outcome}}
        when fingerprint == command.request_fingerprint ->
          {{:ok, outcome}, state}

        %{^identity => _different_request} ->
          {{:error, :idempotency_conflict}, state}

        _ ->
          create_probe(command, identity, state)
      end
    end)
  end

  @impl true
  def claim_probe_events(_consumer_id, batch_size) do
    ensure_started()

    Agent.get_and_update(__MODULE__, fn state ->
      {claimed, rest} = Enum.split(state.claims, batch_size)
      {{:ok, claimed}, %{state | claims: rest}}
    end)
  end

  @impl true
  def apply_probe_effect(event_id, _claim_token, _consumer_id) do
    ensure_started()

    Agent.get_and_update(__MODULE__, fn state ->
      if MapSet.member?(state.effects, event_id) do
        {{:ok, "already_succeeded"}, state}
      else
        {{:ok, "acked"}, %{state | effects: MapSet.put(state.effects, event_id)}}
      end
    end)
  end

  @impl true
  def fail_event(_event_id, _claim_token, _consumer_id, _error_code, _retry_at),
    do: {:ok, "retry_scheduled"}

  @impl true
  def read_handoff(after_cursor, batch_size) do
    events =
      get(fn state ->
        state.events |> Enum.filter(&(&1.handoff_id > after_cursor)) |> Enum.take(batch_size)
      end)

    {:ok, events}
  end

  def effects, do: get(& &1.effects)

  defp create_probe(command, identity, state) do
    if Map.has_key?(state.probes, command.probe_id) do
      {{:error, :stale_aggregate_version}, state}
    else
      event_id = Liqi.Persistence.ProbeCommand.event_id(command)
      envelope = command.envelope

      event = %{
        handoff_id: state.next_handoff,
        event_id: event_id,
        schema_version: 1,
        event_type: "platform.probe.requested.v1",
        event_version: 1,
        occurred_at: DateTime.utc_now(),
        producer: "liqi-beam",
        correlation_id: envelope.correlation_id,
        causation_id: envelope.causation_id,
        aggregate_key: "platform-probe:#{command.probe_id}",
        ordering_key: "platform-probe:#{command.probe_id}",
        payload: %{"probeId" => command.probe_id, "aggregateVersion" => 1},
        metadata: %{
          "protocolVersion" => 1,
          "messageId" => envelope.message_id,
          "actorKey" => envelope.actor_key,
          "priority" => Atom.to_string(envelope.priority),
          "payloadType" => envelope.payload_type,
          "payloadVersion" => envelope.payload_version
        },
        recorded_at: DateTime.utc_now()
      }

      outcome = %{
        probe_id: command.probe_id,
        event_id: event_id,
        aggregate_version: 1,
        status: "accepted"
      }

      claim = Map.merge(event, %{claim_token: Liqi.Runtime.Id.uuid4(), attempt_no: 1})

      new_state = %{
        state
        | probes: Map.put(state.probes, command.probe_id, outcome),
          idempotency:
            Map.put(state.idempotency, identity, %{
              fingerprint: command.request_fingerprint,
              outcome: outcome
            }),
          events: state.events ++ [event],
          claims: state.claims ++ [claim],
          next_handoff: state.next_handoff + 1
      }

      {{:ok, outcome}, new_state}
    end
  end

  defp initial do
    %{
      readiness: :ok,
      probes: %{},
      idempotency: %{},
      events: [],
      claims: [],
      effects: MapSet.new(),
      next_handoff: 1
    }
  end

  defp ensure_started do
    case Process.whereis(__MODULE__) do
      nil ->
        case start_link([]) do
          {:ok, _} -> :ok
          {:error, {:already_started, _}} -> :ok
        end

      _ ->
        :ok
    end
  end

  defp get(fun) do
    ensure_started()
    Agent.get(__MODULE__, fun)
  end
end
