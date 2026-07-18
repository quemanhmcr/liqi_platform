defmodule LiqiPersistence.RealtimeHandoff do
  @moduledoc "Committed V1 realtime cursor read API with explicit gap errors."
  alias LiqiPersistence.{Query, Repos}

  @sql """
  SELECT handoff_id, event_id::text, protocol_version, message_id::text,
         correlation_id::text, causation_id::text, trace_context, deadline_at,
         actor_key, aggregate_key, priority, payload_type, event_type,
         payload_version, event_version, ordering_key, occurred_at, producer,
         payload, metadata, recorded_at
  FROM platform.read_realtime_handoff_v1($1::bigint, $2::integer)
  """

  def read(after_cursor, batch_size \\ 64),
    do: Query.all(Repos.realtime(), @sql, [after_cursor, batch_size], timeout: 3_000)
end
