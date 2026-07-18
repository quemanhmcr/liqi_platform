defmodule Liqi.Web.ErrorModel do
  @moduledoc false
  import Plug.Conn, only: [get_req_header: 2]

  @messages %{
    "auth.unauthorized" => "Authentication is required for this platform probe.",
    "validation.failed" => "The request is invalid.",
    "deadline.exceeded" => "The request deadline was exceeded.",
    "capacity.endpoint" => "The endpoint is at capacity.",
    "capacity.database" => "The database admission budget is exhausted.",
    "capacity.native" => "The native call admission budget is exhausted.",
    "runtime.draining" => "The runtime is draining.",
    "runtime.unavailable" => "The runtime is temporarily unavailable.",
    "database.unavailable" => "The durable authority is unavailable.",
    "idempotency.conflict" => "The idempotency key conflicts with existing durable content.",
    "internal.error" => "An internal error occurred."
  }

  def build(code, conn, opts \\ []) do
    request_id = List.first(get_req_header(conn, "x-request-id")) || Liqi.Runtime.Id.uuid4()

    %{
      "error" => %{
        "version" => "1",
        "code" => code,
        "message" => Map.get(@messages, code, @messages["internal.error"]),
        "requestId" => request_id,
        "retryable" => Keyword.get(opts, :retryable, false),
        "details" => Keyword.get(opts, :details, [])
      }
    }
  end
end
