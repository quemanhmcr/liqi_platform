defmodule Liqi.Web.MetricsController do
  use Phoenix.Controller, formats: [:json]

  def show(conn, _params) do
    metrics = Liqi.Telemetry.Store.snapshot()
    json(conn, %{version: "1", metrics: metrics})
  end
end
