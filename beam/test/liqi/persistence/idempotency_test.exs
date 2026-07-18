defmodule Liqi.Persistence.IdempotencyTest do
  use ExUnit.Case, async: false

  setup do
    Liqi.Persistence.Fake.reset()
    :ok
  end

  test "same key and fingerprint returns original durable outcome" do
    probe_id = Liqi.Runtime.Id.uuid4()
    assert {:ok, command} = command(probe_id, "same-key")
    assert {:ok, first} = Liqi.Persistence.Fake.request_probe(command)
    assert {:ok, ^first} = Liqi.Persistence.Fake.request_probe(command)
    assert {:ok, [event]} = Liqi.Persistence.Fake.read_handoff(0, 10)
    assert event.event_id == first.event_id
  end

  test "same key with another request fingerprint conflicts" do
    assert {:ok, first} = command(Liqi.Runtime.Id.uuid4(), "shared-key")
    assert {:ok, second} = command(Liqi.Runtime.Id.uuid4(), "shared-key")
    assert {:ok, _} = Liqi.Persistence.Fake.request_probe(first)
    assert {:error, :idempotency_conflict} = Liqi.Persistence.Fake.request_probe(second)
  end

  test "V1 production adapter publishes the complete consumer callback surface" do
    callbacks = Liqi.Persistence.Adapter.behaviour_info(:callbacks)
    exported = LiqiPersistence.RuntimeAdapter.__info__(:functions)

    assert Enum.all?(callbacks, fn callback -> callback in exported end)

    assert Liqi.Persistence.ProbeCommand.event_id(
             elem(command(Liqi.Runtime.Id.uuid4(), "stable"), 1)
           )
  end

  defp command(probe_id, key) do
    id = Liqi.Runtime.Id.uuid4()

    with {:ok, envelope} <-
           Liqi.Runtime.Envelope.new(
             message_id: id,
             actor_key: "platform-probe:#{probe_id}",
             payload_type: "platform.probe.requested",
             payload_version: 1,
             payload: %{"clientProbeId" => probe_id}
           ) do
      Liqi.Persistence.ProbeCommand.new(envelope, probe_id, key)
    end
  end
end
