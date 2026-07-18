defmodule Liqi.Realtime.SessionActorTest do
  use ExUnit.Case, async: false

  setup do
    Liqi.Persistence.Fake.reset()
    :ok
  end

  test "reattaches, replays unacked events, acknowledges, and rejects another device" do
    session_id = Liqi.Runtime.Id.uuid4()
    device_id = Liqi.Runtime.Id.uuid4()
    {:ok, session} = Liqi.Runtime.ActorRouter.ensure_session(session_id)
    {:ok, connection1} = connection(session)

    assert {:ok, %{cursor: 0}} =
             Liqi.Realtime.SessionActor.attach(session, device_id, connection1, 0)

    event = event(1)
    assert :ok = Liqi.Realtime.SessionActor.deliver(session, event)
    assert_receive {:connection_frame, %{"kind" => "event", "payload" => %{"sequence" => 1}}}

    Liqi.Realtime.ConnectionProcess.close(connection1, :test_disconnect)
    assert_receive {:connection_close, :test_disconnect}
    Liqi.Realtime.SessionActor.detach(session, connection1)

    {:ok, connection2} = connection(session)

    assert {:ok, %{cursor: 0}} =
             Liqi.Realtime.SessionActor.attach(session, device_id, connection2, 0)

    assert_receive {:connection_frame, %{"payload" => %{"sequence" => 1}}}

    assert {:ok, %{cursor: 1, resume_token: token}} = Liqi.Realtime.SessionActor.ack(session, 1)
    assert {:ok, 1} = Liqi.Realtime.ResumeToken.verify(token, session_id, device_id)

    assert {:error, :device_binding_mismatch} =
             Liqi.Realtime.SessionActor.attach(session, Liqi.Runtime.Id.uuid4(), connection2, 1)
  end

  test "slow consumer is disconnected, queue is cleared, and gap repair is required" do
    old = Application.get_env(:liqi_platform, :runtime_config)

    config = %Liqi.Runtime.Config{
      environment: "test",
      release_id: "test",
      service_identity: "liqi-platform",
      session_queue_capacity: 2
    }

    Application.put_env(:liqi_platform, :runtime_config, config)
    on_exit(fn -> restore_config(old) end)

    session_id = Liqi.Runtime.Id.uuid4()
    {:ok, session} = Liqi.Runtime.ActorRouter.ensure_session(session_id)
    {:ok, connection} = connection(session)

    assert {:ok, _} =
             Liqi.Realtime.SessionActor.attach(session, Liqi.Runtime.Id.uuid4(), connection, 0)

    assert :ok = Liqi.Realtime.SessionActor.deliver(session, event(1))
    assert :ok = Liqi.Realtime.SessionActor.deliver(session, event(2))
    assert {:error, :slow_consumer} = Liqi.Realtime.SessionActor.deliver(session, event(3))
    assert_receive {:connection_frame, %{"kind" => "slow_consumer"}}
    assert_receive {:connection_close, :slow_consumer}

    assert {:ok, %{queue_size: 0, gap_required?: true}} =
             Liqi.Realtime.SessionActor.snapshot(session)
  end

  test "access revoke invalidates subscriptions and closes the transport" do
    {:ok, session} = Liqi.Runtime.ActorRouter.ensure_session(Liqi.Runtime.Id.uuid4())
    {:ok, connection} = connection(session)

    assert {:ok, _} =
             Liqi.Realtime.SessionActor.attach(session, Liqi.Runtime.Id.uuid4(), connection, 0)

    assert :ok = Liqi.Realtime.SessionActor.subscribe(session, "platform-probe:test")
    assert :ok = Liqi.Realtime.SessionActor.revoke(session)
    assert_receive {:connection_frame, %{"kind" => "access_revoked"}}
    assert_receive {:connection_close, :access_revoked}

    assert {:ok, %{revoked?: true, subscriptions: []}} =
             Liqi.Realtime.SessionActor.snapshot(session)
  end

  defp connection(session) do
    Liqi.Runtime.ActorRouter.start_connection(Liqi.Runtime.Id.uuid4(),
      channel_pid: self(),
      session_pid: session
    )
  end

  defp event(sequence) do
    id = Liqi.Runtime.Id.uuid4()

    %{
      handoff_id: sequence,
      event_id: id,
      event_type: "platform.probe.requested.v1",
      event_version: 0,
      occurred_at: DateTime.utc_now(),
      producer: "liqi-api",
      correlation_id: id,
      causation_id: id,
      aggregate_key: "platform-probe:test",
      ordering_key: "platform-probe:test",
      payload: %{"probeId" => id},
      metadata: %{}
    }
  end

  defp restore_config(nil), do: Application.delete_env(:liqi_platform, :runtime_config)
  defp restore_config(value), do: Application.put_env(:liqi_platform, :runtime_config, value)
end
