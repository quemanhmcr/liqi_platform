defmodule Liqi.Runtime.EnvelopeTest do
  use ExUnit.Case, async: true

  test "round-trips the shared envelope and propagates a bounded deadline" do
    id = Liqi.Runtime.Id.uuid4()
    now = System.system_time(:millisecond)

    assert {:ok, envelope} =
             Liqi.Runtime.Envelope.new(
               message_id: id,
               correlation_id: id,
               causation_id: id,
               actor_key: "platform-probe:#{id}",
               priority: :durable,
               payload_type: "platform.probe.requested",
               payload: %{"probeId" => id},
               deadline: now + 250
             )

    assert {:ok, ^envelope} =
             envelope |> Liqi.Runtime.Envelope.to_map() |> Liqi.Runtime.Envelope.from_map()

    assert Liqi.Runtime.Envelope.remaining_ms(envelope, now) == 250
    refute Liqi.Runtime.Envelope.expired?(envelope, now + 249)
    assert Liqi.Runtime.Envelope.expired?(envelope, now + 250)
  end

  test "rejects invalid priority and unbounded actor keys" do
    id = Liqi.Runtime.Id.uuid4()

    assert {:error, :invalid_priority} =
             Liqi.Runtime.Envelope.new(
               message_id: id,
               actor_key: "probe",
               priority: :unknown,
               payload_type: "probe"
             )

    assert {:error, :invalid_actor_key} =
             Liqi.Runtime.Envelope.new(
               message_id: id,
               actor_key: String.duplicate("x", 161),
               payload_type: "probe"
             )
  end
end
