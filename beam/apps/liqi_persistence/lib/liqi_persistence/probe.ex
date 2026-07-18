defmodule LiqiPersistence.Probe do
  @moduledoc "Least-privilege walking-skeleton observation."
  alias LiqiPersistence.{Query, Repos}

  @sql """
  SELECT probe_id::text, event_id::text, aggregate_version, probe_status,
         outbox_state, effect_applied, handoff_cursor, terminal, observed_at
  FROM platform.observe_probe_v1($1::text::uuid, $2::text::uuid)
  """

  def observe(probe_id, event_id),
    do: Query.one(Repos.command(), @sql, [probe_id, event_id], timeout: 5_000)
end
