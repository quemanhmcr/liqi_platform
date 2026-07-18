defmodule Liqi.Web.PlatformChannel do
  use Phoenix.Channel

  @impl true
  def join("platform:v1", params, socket) do
    session_id = socket.assigns.session_id
    device_id = socket.assigns.device_id
    actor_keys = normalize_actor_keys(params)

    with {:ok, cursor} <- resume_cursor(params, session_id, device_id),
         {:ok, session_pid} <- Liqi.Runtime.ActorRouter.ensure_session(session_id),
         :ok <- subscribe_all(session_pid, actor_keys),
         {:ok, connection_pid} <-
           Liqi.Runtime.ActorRouter.start_connection(Liqi.Runtime.Id.uuid4(),
             channel_pid: self(),
             session_pid: session_pid
           ),
         {:ok, attachment} <-
           Liqi.Realtime.SessionActor.attach(session_pid, device_id, connection_pid, cursor),
         {:ok, repair} <- Liqi.Realtime.Resume.repair(session_pid, cursor) do
      response = %{
        "protocolVersion" => "1",
        "sessionId" => session_id,
        "resumeCursor" => attachment.cursor,
        "resumeToken" => attachment.resume_token,
        "repair" => repair
      }

      {:ok, response,
       assign(socket,
         session_pid: session_pid,
         connection_pid: connection_pid,
         actor_keys: actor_keys
       )}
    else
      {:error, reason} -> {:error, %{reason: error_code(reason)}}
    end
  end

  @impl true
  def handle_in("subscribe", %{"actorKey" => actor_key}, socket) do
    case Liqi.Realtime.SessionActor.subscribe(socket.assigns.session_pid, actor_key) do
      :ok -> {:reply, {:ok, %{actorKey: actor_key}}, socket}
      {:error, reason} -> {:reply, {:error, %{reason: error_code(reason)}}, socket}
    end
  end

  def handle_in("ack", %{"sequence" => sequence}, socket) do
    case Liqi.Realtime.SessionActor.ack(socket.assigns.session_pid, sequence) do
      {:ok, response} -> {:reply, {:ok, response}, socket}
      {:error, reason} -> {:reply, {:error, %{reason: error_code(reason)}}, socket}
    end
  end

  def handle_in("resume", %{"cursor" => cursor, "resumeToken" => token}, socket) do
    with {:ok, ^cursor} <-
           Liqi.Realtime.ResumeToken.verify(
             token,
             socket.assigns.session_id,
             socket.assigns.device_id
           ),
         {:ok, repair} <- Liqi.Realtime.Resume.repair(socket.assigns.session_pid, cursor) do
      {:reply, {:ok, repair}, socket}
    else
      {:error, reason} -> {:reply, {:error, %{reason: error_code(reason)}}, socket}
    end
  end

  def handle_in("heartbeat_ack", _payload, socket), do: {:reply, :ok, socket}

  def handle_in(_event, _payload, socket),
    do: {:reply, {:error, %{reason: "unsupported_frame"}}, socket}

  @impl true
  def handle_info({:connection_frame, frame}, socket) do
    push(socket, "frame", frame)
    {:noreply, socket}
  end

  def handle_info({:connection_close, reason}, socket) do
    {:stop, {:shutdown, reason}, socket}
  end

  @impl true
  def terminate(_reason, socket) do
    if pid = socket.assigns[:connection_pid],
      do: Liqi.Realtime.ConnectionProcess.close(pid, :transport_closed)

    :ok
  end

  defp resume_cursor(params, session_id, device_id) do
    cursor = params["resumeCursor"] || 0

    cond do
      not is_integer(cursor) or cursor < 0 ->
        {:error, :invalid_resume_cursor}

      is_binary(params["resumeToken"]) ->
        Liqi.Realtime.ResumeToken.verify(params["resumeToken"], session_id, device_id)

      cursor == 0 ->
        {:ok, 0}

      true ->
        {:error, :resume_token_required}
    end
  end

  defp normalize_actor_keys(%{"actorKeys" => keys}) when is_list(keys),
    do: keys |> Enum.filter(&is_binary/1) |> Enum.uniq() |> Enum.take(32)

  defp normalize_actor_keys(%{"actorKey" => key}) when is_binary(key), do: [key]
  defp normalize_actor_keys(_), do: []

  defp subscribe_all(session_pid, actor_keys) do
    Enum.reduce_while(actor_keys, :ok, fn key, :ok ->
      case Liqi.Realtime.SessionActor.subscribe(session_pid, key) do
        :ok -> {:cont, :ok}
        error -> {:halt, error}
      end
    end)
  end

  defp error_code(:invalid_resume_token), do: "resume_token_invalid"
  defp error_code(:resume_token_required), do: "resume_token_required"
  defp error_code(:invalid_resume_cursor), do: "resume_cursor_invalid"
  defp error_code(:device_binding_mismatch), do: "device_binding_mismatch"
  defp error_code(:access_revoked), do: "access_revoked"
  defp error_code(:slow_consumer), do: "slow_consumer"
  defp error_code({:handoff_gap, _}), do: "handoff_gap"
  defp error_code(_), do: "runtime_unavailable"
end
