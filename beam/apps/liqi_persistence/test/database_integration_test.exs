defmodule LiqiPersistence.DatabaseIntegrationTest do
  use ExUnit.Case, async: false

  @moduletag skip: System.get_env("LIQI_DATABASE_INTEGRATION") != "1"

  alias LiqiPersistence.{Probe, RuntimeAdapter}

  defmodule TestCommand do
    @enforce_keys [
      :probe_id,
      :event_id,
      :idempotency_scope,
      :idempotency_key,
      :request_fingerprint,
      :expected_version,
      :envelope
    ]
    defstruct @enforce_keys

    def event_id(%__MODULE__{event_id: event_id}), do: event_id
  end

  test "runtime callback preserves identity, idempotency, deadline, and terminal effect" do
    now_ms = System.system_time(:millisecond)
    probe_id = Ecto.UUID.generate()
    event_id = Ecto.UUID.generate()
    idempotency_key = "ecto-provider-" <> Ecto.UUID.generate()

    command = %TestCommand{
      probe_id: probe_id,
      event_id: event_id,
      idempotency_scope: "platform.probe.create.v1",
      idempotency_key: idempotency_key,
      request_fingerprint: String.duplicate("a", 64),
      expected_version: 0,
      envelope: %{
        message_id: Ecto.UUID.generate(),
        correlation_id: Ecto.UUID.generate(),
        causation_id: Ecto.UUID.generate(),
        trace_context: %{
          "traceparent" => "00-11111111111111111111111111111111-2222222222222222-01"
        },
        deadline: now_ms + 5_000
      }
    }

    assert {:ok,
            %{
              probe_id: ^probe_id,
              event_id: ^event_id,
              aggregate_version: 1,
              duplicate: false,
              status: "accepted"
            }} = RuntimeAdapter.request_probe(command)

    assert {:ok, %{event_id: ^event_id, duplicate: true, status: "accepted"}} =
             RuntimeAdapter.request_probe(command)

    conflicting = %{command | request_fingerprint: String.duplicate("b", 64)}
    assert {:error, :idempotency_conflict} = RuntimeAdapter.request_probe(conflicting)

    stale = %{
      command
      | event_id: Ecto.UUID.generate(),
        idempotency_key: "stale-" <> Ecto.UUID.generate(),
        request_fingerprint: String.duplicate("c", 64)
    }

    assert {:error, :stale_aggregate_version} = RuntimeAdapter.request_probe(stale)

    assert :ok = RuntimeAdapter.readiness(8)

    expired = %{
      command
      | probe_id: Ecto.UUID.generate(),
        event_id: Ecto.UUID.generate(),
        idempotency_key: "expired-" <> Ecto.UUID.generate(),
        request_fingerprint: String.duplicate("d", 64),
        envelope: %{command.envelope | deadline: now_ms - 1}
    }

    assert {:error, :deadline_exceeded} = RuntimeAdapter.request_probe(expired)

    assert {:ok, handoffs} = RuntimeAdapter.read_handoff(0, 128)
    assert Enum.any?(handoffs, &(&1["event_id"] == event_id and &1["protocol_version"] == 1))

    consumer_id = "ecto-provider-" <> Ecto.UUID.generate()
    assert {:ok, claimed} = RuntimeAdapter.claim_probe_events(consumer_id, 50)
    event = Enum.find(claimed, &(&1["event_id"] == event_id))
    assert event
    assert DateTime.to_unix(event["deadline_at"], :millisecond) == command.envelope.deadline
    assert event["event_type"] == event["payload_type"]
    assert event["aggregate_key"] == event["actor_key"]

    assert {:ok, "acked"} =
             RuntimeAdapter.apply_probe_effect(
               event["event_id"],
               event["claim_token"],
               consumer_id
             )

    assert {:ok,
            %{
              probe_id: ^probe_id,
              event_id: ^event_id,
              aggregate_version: 1,
              probe_status: "completed",
              outbox_state: "succeeded",
              effect_applied: true,
              terminal: true,
              observed_at: %DateTime{}
            }} = RuntimeAdapter.observe_probe(probe_id, event_id)

    assert {:error, :not_found} =
             RuntimeAdapter.observe_probe(Ecto.UUID.generate(), Ecto.UUID.generate())

    assert {:error, :probe_identity_mismatch} =
             RuntimeAdapter.observe_probe(probe_id, Ecto.UUID.generate())

    assert {:ok,
            %{
              "event_id" => ^event_id,
              "aggregate_version" => 1,
              "probe_status" => "completed",
              "outbox_state" => "succeeded",
              "effect_applied" => true,
              "terminal" => true
            }} = Probe.observe(probe_id, event_id)
  end
end
