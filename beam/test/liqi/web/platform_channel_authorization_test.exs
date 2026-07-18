defmodule Liqi.Web.PlatformChannelAuthorizationTest do
  use ExUnit.Case, async: false
  import Phoenix.ChannelTest, except: [push: 3]

  @endpoint Liqi.Web.Endpoint

  test "join and subscribe reject actor keys outside the platform probe scope" do
    session_id = Liqi.Runtime.Id.uuid4()
    device_id = Liqi.Runtime.Id.uuid4()

    socket =
      socket(Liqi.Web.Socket, "authz:#{session_id}", %{session_id: session_id, device_id: device_id})

    assert {:error, %{reason: "actor_key_unauthorized"}} =
             subscribe_and_join(socket, Liqi.Web.PlatformChannel, "platform:v1", %{
               "actorKey" => "conversation:#{Liqi.Runtime.Id.uuid4()}",
               "resumeCursor" => 0
             })

    probe_key = "platform-probe:#{Liqi.Runtime.Id.uuid4()}"

    assert {:ok, _reply, joined} =
             subscribe_and_join(socket, Liqi.Web.PlatformChannel, "platform:v1", %{
               "actorKey" => probe_key,
               "resumeCursor" => 0
             })

    ref =
      Phoenix.ChannelTest.push(joined, "subscribe", %{
        "actorKey" => "other:#{Liqi.Runtime.Id.uuid4()}"
      })

    assert_reply(ref, :error, %{reason: "actor_key_unauthorized"})
  end
end
