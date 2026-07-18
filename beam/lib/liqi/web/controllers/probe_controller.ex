defmodule Liqi.Web.ProbeController do
  use Phoenix.Controller, formats: [:json]

  def create(conn, %{"clientProbeId" => probe_id}) do
    request_id = request_id(conn)
    timeout_ms = request_timeout(conn)
    idempotency_key = List.first(get_req_header(conn, "idempotency-key"))

    with :ok <- Liqi.Runtime.ProbeAuth.authorize_conn(conn),
         true <- Liqi.Runtime.Id.valid_uuid?(probe_id),
         true <- valid_idempotency_key?(idempotency_key),
         {:ok, envelope} <-
           Liqi.Runtime.Envelope.new(
             message_id: request_id,
             correlation_id: request_id,
             causation_id: request_id,
             timeout_ms: timeout_ms,
             actor_key: "platform-probe:#{probe_id}",
             priority: :durable,
             payload_type: "platform.probe.requested",
             payload_version: 1,
             payload: %{"clientProbeId" => probe_id},
             trace_context: trace_context(conn)
           ),
         {:ok, result} <-
           Liqi.Runtime.PlatformProbeActor.execute(probe_id, envelope, idempotency_key) do
      conn
      |> put_status(:accepted)
      |> put_resp_header("x-request-id", request_id)
      |> json(%{
        probeId: result.probe_id,
        eventId: result.event_id,
        aggregateVersion: result.aggregate_version,
        status: result.status,
        actorKey: "platform-probe:#{probe_id}"
      })
    else
      {:error, :unauthorized} ->
        render_error(conn, 401, "auth.unauthorized")

      false ->
        render_error(conn, 400, "validation.failed")

      {:error, :validation_failed} ->
        render_error(conn, 400, "validation.failed")

      {:error, :deadline_exceeded} ->
        render_error(conn, 504, "deadline.exceeded", retryable: true)

      {:error, :database_capacity} ->
        render_error(conn, 429, "capacity.database", retryable: true)

      {:error, :idempotency_conflict} ->
        render_error(conn, 409, "idempotency.conflict")

      {:error, :stale_aggregate_version} ->
        render_error(conn, 409, "aggregate.version_conflict")

      {:error, :database_unavailable} ->
        render_error(conn, 503, "database.unavailable", retryable: true)

      {:error, _} ->
        render_error(conn, 503, "runtime.unavailable", retryable: true)
    end
  end

  def create(conn, _params) do
    case Liqi.Runtime.ProbeAuth.authorize_conn(conn) do
      :ok -> render_error(conn, 400, "validation.failed")
      {:error, :unauthorized} -> render_error(conn, 401, "auth.unauthorized")
    end
  end

  defp valid_idempotency_key?(value) when is_binary(value), do: byte_size(value) in 1..128
  defp valid_idempotency_key?(_), do: false

  defp request_id(conn) do
    case List.first(get_req_header(conn, "x-request-id")) do
      value when is_binary(value) ->
        if Liqi.Runtime.Id.valid_uuid?(value), do: value, else: Liqi.Runtime.Id.uuid4()

      _ ->
        Liqi.Runtime.Id.uuid4()
    end
  end

  defp request_timeout(conn) do
    {:ok, config} = Liqi.Runtime.Config.load()

    case List.first(get_req_header(conn, "x-liqi-deadline-ms")) do
      nil ->
        config.request_timeout_ms

      value ->
        min(max(parse_integer(value, config.request_timeout_ms), 1), config.request_timeout_ms)
    end
  end

  defp parse_integer(value, fallback) do
    case Integer.parse(value) do
      {integer, ""} -> integer
      _ -> fallback
    end
  end

  defp trace_context(conn) do
    case List.first(get_req_header(conn, "traceparent")) do
      value when is_binary(value) and byte_size(value) <= 128 -> %{"traceparent" => value}
      _ -> %{}
    end
  end

  defp render_error(conn, status, code, opts \\ []) do
    conn |> put_status(status) |> json(Liqi.Web.ErrorModel.build(code, conn, opts))
  end
end
