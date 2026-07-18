defmodule Liqi.Web.HealthController do
  use Phoenix.Controller, formats: [:json]

  def live(conn, _params), do: json(conn, Liqi.Runtime.Health.live())

  def ready(conn, _params) do
    response = Liqi.Runtime.Health.ready()
    status = if response.status == "ready", do: 200, else: 503
    conn |> put_status(status) |> json(response)
  end
end
