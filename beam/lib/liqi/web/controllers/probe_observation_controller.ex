defmodule Liqi.Web.ProbeObservationController do
  use Phoenix.Controller, formats: [:json]

  def show(conn, %{"probe_id" => probe_id, "eventId" => event_id}) do
    with :ok <- Liqi.Runtime.ProbeAuth.authorize_conn(conn),
         true <- Liqi.Runtime.Id.valid_uuid?(probe_id),
         true <- Liqi.Runtime.Id.valid_uuid?(event_id),
         {:ok, observation} <- adapter().observe_probe(probe_id, event_id) do
      json(conn, %{
        probeId: observation.probe_id,
        eventId: observation.event_id,
        probeStatus: observation.probe_status,
        outboxState: observation.outbox_state,
        effectApplied: observation.effect_applied,
        terminal: observation.terminal,
        observedAt: iso8601(observation.observed_at)
      })
    else
      {:error, :unauthorized} ->
        render_error(conn, 401, "auth.unauthorized")

      false ->
        render_error(conn, 400, "validation.failed")

      {:error, :not_found} ->
        render_error(conn, 404, "probe.not_found")

      {:error, :probe_identity_mismatch} ->
        render_error(conn, 409, "idempotency.conflict")

      {:error, :database_unavailable} ->
        render_error(conn, 503, "database.unavailable", retryable: true)

      {:error, _} ->
        render_error(conn, 503, "runtime.unavailable", retryable: true)
    end
  end

  def show(conn, _params) do
    case Liqi.Runtime.ProbeAuth.authorize_conn(conn) do
      :ok -> render_error(conn, 400, "validation.failed")
      {:error, :unauthorized} -> render_error(conn, 401, "auth.unauthorized")
    end
  end

  defp iso8601(%DateTime{} = value), do: DateTime.to_iso8601(value)
  defp iso8601(value) when is_binary(value), do: value

  defp render_error(conn, status, code, opts \\ []),
    do: conn |> put_status(status) |> json(Liqi.Web.ErrorModel.build(code, conn, opts))

  defp adapter, do: Application.fetch_env!(:liqi_platform, :persistence_adapter)
end
