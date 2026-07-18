defmodule Liqi.Web.PlatformProbeE2ETest do
  use ExUnit.Case, async: false
  import Phoenix.ConnTest
  import Plug.Conn, only: [put_req_header: 3]
  import Phoenix.ChannelTest, except: [push: 3]

  @endpoint Liqi.Web.Endpoint

  setup do
    Liqi.Persistence.Fake.reset()
    :ok
  end

  test "HTTP commit reaches WebSocket, ACK is resumable, and duplicate command is stable" do
    Process.flag(:trap_exit, true)
    session_id = Liqi.Runtime.Id.uuid4()
    device_id = Liqi.Runtime.Id.uuid4()
    probe_id = Liqi.Runtime.Id.uuid4()
    actor_key = "platform-probe:#{probe_id}"

    socket =
      socket(Liqi.Web.Socket, "socket:#{session_id}", %{
        session_id: session_id,
        device_id: device_id
      })

    assert {:ok, %{"resumeCursor" => 0}, socket} =
             subscribe_and_join(socket, Liqi.Web.PlatformChannel, "platform:v1", %{
               "actorKey" => actor_key,
               "resumeCursor" => 0
             })

    conn =
      build_conn()
      |> put_req_header("x-liqi-probe-token", "liqi-test-probe-token")
      |> put_req_header("idempotency-key", "probe-#{probe_id}")
      |> post("/platform/v1/probes", %{"clientProbeId" => probe_id})

    response = json_response(conn, 202)
    assert response["probeId"] == probe_id
    event_id = response["eventId"]

    duplicate =
      build_conn()
      |> put_req_header("x-liqi-probe-token", "liqi-test-probe-token")
      |> put_req_header("idempotency-key", "probe-#{probe_id}")
      |> post("/platform/v1/probes", %{"clientProbeId" => probe_id})

    assert json_response(duplicate, 202)["eventId"] == event_id

    start_supervised!({Liqi.Runtime.OutboxWorker, enabled: false})
    assert {:ok, [{:ok, "acked"}]} = Liqi.Runtime.OutboxWorker.poll_once()
    assert MapSet.member?(Liqi.Persistence.Fake.effects(), event_id)

    start_supervised!({Liqi.Realtime.Dispatcher, enabled: false})
    assert {:ok, 1} = Liqi.Realtime.Dispatcher.poll_once()

    assert_push("frame", %{
      "kind" => "event",
      "payload" => %{
        "sequence" => 1,
        "event" => %{"eventId" => ^event_id}
      }
    })

    ref = Phoenix.ChannelTest.push(socket, "ack", %{"sequence" => 1})
    assert_reply(ref, :ok, %{cursor: 1, resume_token: token})

    leave_ref = leave(socket)
    assert_reply(leave_ref, :ok)

    resumed_socket =
      socket(Liqi.Web.Socket, "socket-resumed:#{session_id}", %{
        session_id: session_id,
        device_id: device_id
      })

    assert {:ok, %{"repair" => %{cursor: 1, delivered: 0}}, _resumed} =
             subscribe_and_join(resumed_socket, Liqi.Web.PlatformChannel, "platform:v1", %{
               "actorKey" => actor_key,
               "resumeCursor" => 1,
               "resumeToken" => token
             })
  end

  test "gap repair replays only subscribed committed events" do
    subscribed_probe = Liqi.Runtime.Id.uuid4()
    unrelated_probe = Liqi.Runtime.Id.uuid4()

    for probe_id <- [subscribed_probe, unrelated_probe] do
      id = Liqi.Runtime.Id.uuid4()

      assert {:ok, envelope} =
               Liqi.Runtime.Envelope.new(
                 message_id: id,
                 actor_key: "platform-probe:#{probe_id}",
                 payload_type: "platform.probe.requested"
               )

      assert {:ok, command} =
               Liqi.Persistence.ProbeCommand.new(envelope, probe_id, "gap-#{probe_id}")

      assert {:ok, _} = Liqi.Persistence.Fake.request_probe(command)
    end

    session_id = Liqi.Runtime.Id.uuid4()
    device_id = Liqi.Runtime.Id.uuid4()

    socket =
      socket(Liqi.Web.Socket, "gap:#{session_id}", %{session_id: session_id, device_id: device_id})

    assert {:ok, %{"repair" => %{scanned: 2, delivered: 1, cursor: 2}}, _socket} =
             subscribe_and_join(socket, Liqi.Web.PlatformChannel, "platform:v1", %{
               "actorKey" => "platform-probe:#{subscribed_probe}",
               "resumeCursor" => 0
             })

    assert_push("frame", %{
      "kind" => "event",
      "payload" => %{"event" => %{"payload" => %{"probeId" => ^subscribed_probe}}}
    })

    refute_push(
      "frame",
      %{"payload" => %{"event" => %{"payload" => %{"probeId" => ^unrelated_probe}}}},
      100
    )
  end
end
