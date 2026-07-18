defmodule Liqi.Runtime.ActorRouter do
  @moduledoc "Deterministic local routing to partitioned actor supervisors with mailbox rejection."

  def ensure_session(session_id, opts \\ []) do
    case Registry.lookup(Liqi.SessionRegistry, session_id) do
      [{pid, _}] ->
        {:ok, pid}

      [] ->
        start_actor(
          session_id,
          {Liqi.Realtime.SessionActor, Keyword.put(opts, :session_id, session_id)}
        )
    end
  end

  def start_connection(connection_id, opts) do
    start_actor(
      connection_id,
      {Liqi.Realtime.ConnectionProcess, Keyword.put(opts, :connection_id, connection_id)}
    )
  end

  def call(pid, request, timeout \\ 5_000) when is_pid(pid) do
    with {:ok, config} <- Liqi.Runtime.Config.load(),
         {:message_queue_len, length} <- Process.info(pid, :message_queue_len) do
      cond do
        length >= config.actor_mailbox_reject ->
          :telemetry.execute([:liqi, :actor, :mailbox, :reject], %{length: length}, %{})
          {:error, :actor_overloaded}

        length >= config.actor_mailbox_warn ->
          :telemetry.execute([:liqi, :actor, :mailbox, :warning], %{length: length}, %{})
          safe_call(pid, request, timeout)

        true ->
          safe_call(pid, request, timeout)
      end
    else
      nil -> {:error, :actor_unavailable}
      {:error, reason} -> {:error, reason}
    end
  end

  def partition_for(key, partitions) when partitions > 0, do: :erlang.phash2(key, partitions)

  defp start_actor(key, child_spec) do
    supervisor = {:via, PartitionSupervisor, {Liqi.Runtime.ActorPartitions, key}}

    case DynamicSupervisor.start_child(supervisor, child_spec) do
      {:ok, pid} -> {:ok, pid}
      {:error, {:already_started, pid}} -> {:ok, pid}
      other -> other
    end
  end

  defp safe_call(pid, request, timeout) do
    GenServer.call(pid, request, timeout)
  catch
    :exit, {:timeout, _} -> {:error, :deadline_exceeded}
    :exit, _ -> {:error, :actor_unavailable}
  end
end
