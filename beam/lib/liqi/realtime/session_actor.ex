defmodule Liqi.Realtime.SessionActor do
  @moduledoc "Rebuildable logical session with bounded unacked delivery and reattachment."
  use GenServer, restart: :transient

  def start_link(opts) do
    session_id = Keyword.fetch!(opts, :session_id)
    GenServer.start_link(__MODULE__, opts, name: via(session_id))
  end

  def attach(pid, device_id, connection_pid, resume_cursor),
    do: Liqi.Runtime.ActorRouter.call(pid, {:attach, device_id, connection_pid, resume_cursor})

  def detach(pid, connection_pid), do: GenServer.cast(pid, {:detach, connection_pid})
  def subscribe(pid, actor_key), do: Liqi.Runtime.ActorRouter.call(pid, {:subscribe, actor_key})
  def ack(pid, sequence), do: Liqi.Runtime.ActorRouter.call(pid, {:ack, sequence})
  def deliver(pid, event), do: Liqi.Runtime.ActorRouter.call(pid, {:deliver, event})

  def revoke(pid, reason \\ "access_revoked"),
    do: Liqi.Runtime.ActorRouter.call(pid, {:revoke, reason})

  def snapshot(pid), do: Liqi.Runtime.ActorRouter.call(pid, :snapshot)

  defp via(session_id), do: {:via, Registry, {Liqi.SessionRegistry, session_id}}

  @impl true
  def init(opts) do
    {:ok, config} = Liqi.Runtime.Config.load()

    {:ok,
     %{
       session_id: Keyword.fetch!(opts, :session_id),
       device_id: nil,
       connection_pid: nil,
       subscriptions: MapSet.new(),
       queue: :queue.new(),
       queue_size: 0,
       queue_bytes: 0,
       last_ack: 0,
       latest_seen: 0,
       seen_event_ids: :queue.new(),
       seen_event_set: MapSet.new(),
       revoked?: false,
       gap_required?: false,
       idle_timer: schedule_idle(config.actor_idle_ttl_ms),
       config: config
     }}
  end

  @impl true
  def handle_call(
        {:attach, _device_id, _connection_pid, _cursor},
        _from,
        %{revoked?: true} = state
      ),
      do: {:reply, {:error, :access_revoked}, state}

  def handle_call({:attach, device_id, _connection_pid, _resume_cursor}, _from, state)
      when not is_nil(state.device_id) and state.device_id != device_id do
    {:reply, {:error, :device_binding_mismatch}, state}
  end

  def handle_call({:attach, device_id, connection_pid, resume_cursor}, _from, state) do
    cancel_timer(state.idle_timer)

    if is_pid(state.connection_pid) and state.connection_pid != connection_pid do
      Liqi.Realtime.ConnectionProcess.close(state.connection_pid, :reattached)
    end

    cursor = max(resume_cursor || 0, state.last_ack)
    {queue, removed} = drop_acknowledged(state.queue, cursor, %{count: 0, bytes: 0})

    state = %{
      state
      | device_id: device_id,
        connection_pid: connection_pid,
        idle_timer: nil,
        last_ack: cursor,
        queue: queue,
        queue_size: state.queue_size - removed.count,
        queue_bytes: max(state.queue_bytes - removed.bytes, 0)
    }

    replay_queue(state)

    {:reply,
     {:ok,
      %{
        session_id: state.session_id,
        cursor: state.last_ack,
        resume_token: Liqi.Realtime.ResumeToken.sign(state.session_id, device_id, state.last_ack)
      }}, state}
  end

  def handle_call({:subscribe, actor_key}, _from, state) when is_binary(actor_key) do
    if byte_size(actor_key) in 1..160 do
      :ok = Phoenix.PubSub.subscribe(Liqi.PubSub, topic(actor_key))
      {:reply, :ok, %{state | subscriptions: MapSet.put(state.subscriptions, actor_key)}}
    else
      {:reply, {:error, :invalid_actor_key}, state}
    end
  end

  def handle_call({:ack, sequence}, _from, state) when is_integer(sequence) and sequence >= 0 do
    if sequence > state.latest_seen do
      {:reply, {:error, :ack_ahead_of_delivery}, state}
    else
      {queue, removed} = drop_acknowledged(state.queue, sequence, %{count: 0, bytes: 0})
      last_ack = max(state.last_ack, sequence)
      token = Liqi.Realtime.ResumeToken.sign(state.session_id, state.device_id, last_ack)

      {:reply, {:ok, %{cursor: last_ack, resume_token: token}},
       %{
         state
         | queue: queue,
           queue_size: state.queue_size - removed.count,
           queue_bytes: max(state.queue_bytes - removed.bytes, 0),
           last_ack: last_ack
       }}
    end
  end

  def handle_call({:deliver, event}, _from, state) do
    {reply, state} = enqueue_event(event, state)
    {:reply, reply, state}
  end

  def handle_call({:revoke, reason}, _from, state) do
    Enum.each(state.subscriptions, &Phoenix.PubSub.unsubscribe(Liqi.PubSub, topic(&1)))

    if is_pid(state.connection_pid) do
      Liqi.Realtime.ConnectionProcess.frame(
        state.connection_pid,
        frame("access_revoked", state, %{"reason" => reason})
      )

      Liqi.Realtime.ConnectionProcess.close(state.connection_pid, :access_revoked)
    end

    {:reply, :ok, %{state | revoked?: true, connection_pid: nil, subscriptions: MapSet.new()}}
  end

  def handle_call(:snapshot, _from, state) do
    snapshot =
      Map.take(state, [
        :session_id,
        :device_id,
        :queue_size,
        :last_ack,
        :latest_seen,
        :revoked?,
        :gap_required?
      ])

    {:reply, {:ok, Map.put(snapshot, :subscriptions, MapSet.to_list(state.subscriptions))}, state}
  end

  @impl true
  def handle_cast({:detach, connection_pid}, %{connection_pid: connection_pid} = state) do
    {:noreply, detach_state(state)}
  end

  def handle_cast({:detach, _}, state), do: {:noreply, state}

  @impl true
  def handle_info({:committed_event, event}, state) do
    {_reply, state} = enqueue_event(event, state)
    {:noreply, state}
  end

  def handle_info(:idle_expire, %{connection_pid: nil} = state), do: {:stop, :normal, state}
  def handle_info(:idle_expire, state), do: {:noreply, state}

  defp enqueue_event(event, state) do
    event_id = fetch(event, :event_id)
    sequence = fetch(event, :handoff_id)

    cond do
      not is_integer(sequence) or sequence <= state.last_ack ->
        {:duplicate, state}

      MapSet.member?(state.seen_event_set, event_id) ->
        {:duplicate, state}

      queue_expired?(state) ->
        slow_consumer_state(event, state)

      state.queue_size >= state.config.session_queue_capacity ->
        slow_consumer_state(event, state)

      true ->
        payload = %{
          "sequence" => sequence,
          "event" => event_to_wire(event),
          "resumeRequired" => state.gap_required?
        }

        outbound_frame = frame("event", state, payload)
        frame_bytes = outbound_frame |> Jason.encode_to_iodata!() |> IO.iodata_length()

        if frame_bytes > state.config.max_realtime_message_bytes or
             state.queue_bytes + frame_bytes > state.config.session_queue_max_bytes do
          slow_consumer_state(event, state)
        else
          outbound = %{
            sequence: sequence,
            event_id: event_id,
            bytes: frame_bytes,
            enqueued_at: System.monotonic_time(:millisecond),
            frame: outbound_frame
          }

          queue = :queue.in(outbound, state.queue)
          {seen_event_ids, seen_event_set} = remember_event(event_id, state)

          state = %{
            state
            | queue: queue,
              queue_size: state.queue_size + 1,
              queue_bytes: state.queue_bytes + frame_bytes,
              latest_seen: max(state.latest_seen, sequence),
              seen_event_ids: seen_event_ids,
              seen_event_set: seen_event_set
          }

          if is_pid(state.connection_pid),
            do: Liqi.Realtime.ConnectionProcess.frame(state.connection_pid, outbound.frame)

          {:ok, state}
        end
    end
  end

  defp slow_consumer_state(event, state) do
    sequence = fetch(event, :handoff_id)

    if is_pid(state.connection_pid) do
      Liqi.Realtime.ConnectionProcess.frame(
        state.connection_pid,
        frame("slow_consumer", state, %{
          "lastAcknowledgedSequence" => state.last_ack,
          "resumeFrom" => state.last_ack
        })
      )

      Liqi.Realtime.ConnectionProcess.close(state.connection_pid, :slow_consumer)
    end

    :telemetry.execute([:liqi, :session, :slow_consumer], %{count: 1}, %{
      session_id: state.session_id
    })

    {{:error, :slow_consumer},
     %{
       state
       | connection_pid: nil,
         queue: :queue.new(),
         queue_size: 0,
         queue_bytes: 0,
         gap_required?: true,
         latest_seen: max(state.latest_seen, sequence)
     }}
  end

  defp remember_event(event_id, state) do
    queue = :queue.in(event_id, state.seen_event_ids)
    set = MapSet.put(state.seen_event_set, event_id)
    max_seen = state.config.session_queue_capacity * 2

    if :queue.len(queue) > max_seen do
      {{:value, expired}, queue} = :queue.out(queue)
      {queue, MapSet.delete(set, expired)}
    else
      {queue, set}
    end
  end

  defp replay_queue(state) do
    state.queue
    |> :queue.to_list()
    |> Enum.each(&Liqi.Realtime.ConnectionProcess.frame(state.connection_pid, &1.frame))
  end

  defp drop_acknowledged(queue, sequence, removed) do
    case :queue.peek(queue) do
      {:value, %{sequence: queued} = outbound} when queued <= sequence ->
        {{:value, _}, rest} = :queue.out(queue)

        drop_acknowledged(rest, sequence, %{
          count: removed.count + 1,
          bytes: removed.bytes + outbound.bytes
        })

      _ ->
        {queue, removed}
    end
  end

  defp queue_expired?(%{queue_size: 0}), do: false

  defp queue_expired?(state) do
    case :queue.peek(state.queue) do
      {:value, outbound} ->
        System.monotonic_time(:millisecond) - outbound.enqueued_at >
          state.config.session_queue_max_age_ms

      :empty ->
        false
    end
  end

  defp detach_state(state) do
    %{state | connection_pid: nil, idle_timer: schedule_idle(state.config.actor_idle_ttl_ms)}
  end

  defp schedule_idle(ttl), do: Process.send_after(self(), :idle_expire, ttl)
  defp cancel_timer(nil), do: :ok
  defp cancel_timer(ref), do: Process.cancel_timer(ref, async: true, info: false)
  defp topic(actor_key), do: "liqi:realtime:" <> actor_key

  defp frame(kind, state, payload) do
    %{
      "protocolVersion" => "1",
      "messageId" => Liqi.Runtime.Id.uuid4(),
      "sentAt" => DateTime.utc_now() |> DateTime.to_iso8601(),
      "kind" => kind,
      "sessionId" => state.session_id,
      "payload" => payload
    }
  end

  defp event_to_wire(event) do
    %{
      "eventId" => fetch(event, :event_id),
      "eventType" => fetch(event, :event_type),
      "eventVersion" => fetch(event, :event_version),
      "occurredAt" => iso8601(fetch(event, :occurred_at)),
      "producer" => fetch(event, :producer),
      "correlationId" => fetch(event, :correlation_id),
      "causationId" => fetch(event, :causation_id),
      "aggregateKey" => fetch(event, :aggregate_key),
      "orderingKey" => fetch(event, :ordering_key),
      "payload" => fetch(event, :payload),
      "metadata" => fetch(event, :metadata)
    }
  end

  defp fetch(map, key) do
    case Map.fetch(map, key) do
      {:ok, value} -> value
      :error -> Map.get(map, Atom.to_string(key))
    end
  end

  defp iso8601(%DateTime{} = value), do: DateTime.to_iso8601(value)
  defp iso8601(value) when is_binary(value), do: value
end
