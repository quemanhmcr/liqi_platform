defmodule Liqi.Web.ProbeStatusController do
  use Phoenix.Controller, formats: [:json]

  def show(conn, %{"probe_id" => probe_id, "eventId" => event_id}) do
    with :ok <- Liqi.Runtime.ProbeAuth.authorize_conn(conn),
         true <- Liqi.Runtime.Id.valid_uuid?(probe_id),
         true <- Liqi.Runtime.Id.valid_uuid?(event_id),
         {:ok, observation} <- adapter().observe_probe(probe_id, event_id) do
      json(conn, observation)
    else
      {:error, :unauthorized} -> render_error(conn, 401, "auth.unauthorized")
      false -> render_error(conn, 400, "validation.failed")
      {:error, :not_found} -> render_error(conn, 404, "probe.not_found")
      {:error, _reason} -> render_error(conn, 503, "database.unavailable", retryable: true)
    end
  end

  def show(conn, _params) do
    case Liqi.Runtime.ProbeAuth.authorize_conn(conn) do
      :ok -> render_error(conn, 400, "validation.failed")
      {:error, :unauthorized} -> render_error(conn, 401, "auth.unauthorized")
    end
  end

  defp adapter, do: Application.fetch_env!(:liqi_platform, :persistence_adapter)

  defp render_error(conn, status, code, opts \\ []) do
    conn |> put_status(status) |> json(Liqi.Web.ErrorModel.build(code, conn, opts))
  end
end
