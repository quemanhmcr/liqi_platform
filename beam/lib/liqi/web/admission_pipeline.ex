defmodule Liqi.Web.AdmissionPipeline do
  @moduledoc false
  @behaviour Plug

  import Plug.Conn

  @impl true
  def init(opts), do: opts

  @impl true
  def call(conn, _opts) do
    route_key = {conn.method, conn.request_path}

    with :ok <- Liqi.Runtime.AdmissionController.admit(route_key, :endpoint),
         result <- Liqi.Runtime.Budgets.with_permit(:endpoint, fn -> router(conn) end) do
      case result do
        {:error, :capacity} -> reject(conn, 429, "capacity.endpoint")
        %Plug.Conn{} = routed -> routed
      end
    else
      {:error, :draining} -> reject(conn, 503, "runtime.draining")
      {:error, :capacity} -> reject(conn, 429, "capacity.endpoint")
      {:error, _} -> reject(conn, 503, "runtime.unavailable")
    end
  end

  defp router(conn), do: Liqi.Web.Router.call(conn, Liqi.Web.Router.init([]))

  defp reject(conn, status, code) do
    body = Jason.encode!(Liqi.Web.ErrorModel.build(code, conn, retryable: true))

    conn
    |> put_resp_content_type("application/json")
    |> send_resp(status, body)
    |> halt()
  end
end
