defmodule Liqi.Web.Endpoint do
  use Phoenix.Endpoint, otp_app: :liqi_platform

  socket("/platform/v1/socket", Liqi.Web.Socket,
    websocket: [connect_info: [:peer_data, :x_headers]],
    longpoll: false
  )

  plug(Plug.RequestId)
  plug(Plug.Telemetry, event_prefix: [:phoenix, :endpoint])
  plug(Plug.MethodOverride)
  plug(Plug.Head)

  plug(Plug.Parsers,
    parsers: [:urlencoded, :json],
    pass: ["application/json"],
    json_decoder: Phoenix.json_library(),
    length: 1_048_576,
    read_length: 65_536,
    read_timeout: 5_000
  )

  plug(Liqi.Web.AdmissionPipeline)
end
