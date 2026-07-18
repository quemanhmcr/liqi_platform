defmodule Liqi.Web.Socket do
  use Phoenix.Socket

  channel("platform:v1", Liqi.Web.PlatformChannel)

  @impl true
  def connect(params, socket, _connect_info) do
    session_id = params["sessionId"]
    device_id = params["deviceId"]

    with "1" <- params["protocolVersion"],
         true <- Liqi.Runtime.Id.valid_uuid?(session_id),
         true <- Liqi.Runtime.Id.valid_uuid?(device_id),
         :ok <- Liqi.Runtime.AdmissionController.admit(session_id, :reconnect),
         result <-
           Liqi.Runtime.Budgets.with_permit(:reconnect, fn ->
             {:ok, assign(socket, session_id: session_id, device_id: device_id)}
           end) do
      case result do
        {:ok, _socket} = ok -> ok
        {:error, :capacity} -> :error
      end
    else
      _ -> :error
    end
  end

  @impl true
  def id(socket), do: "session_socket:#{socket.assigns.session_id}"
end
