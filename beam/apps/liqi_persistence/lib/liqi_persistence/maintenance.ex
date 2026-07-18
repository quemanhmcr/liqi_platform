defmodule LiqiPersistence.Maintenance do
  @moduledoc "Bounded retention commands for Oban maintenance workers."
  alias LiqiPersistence.{Query, Repos}

  def prune_idempotency(before, batch_size \\ 500),
    do:
      scalar(
        "SELECT platform.prune_command_idempotency_v1($1::timestamptz, $2::integer)",
        [before, batch_size]
      )

  def prune_realtime(before, batch_size \\ 500),
    do:
      Query.one(
        Repos.worker(),
        "SELECT deleted_count, retained_after_handoff_id FROM platform.prune_realtime_handoff_v1($1::timestamptz, $2::integer)",
        [before, batch_size],
        timeout: 30_000
      )

  def prune_outbox(succeeded_before, dead_letter_before, batch_size \\ 500),
    do:
      scalar(
        "SELECT platform.prune_outbox_v1($1::timestamptz, $2::timestamptz, $3::integer)",
        [succeeded_before, dead_letter_before, batch_size]
      )

  defp scalar(sql, params), do: Query.scalar(Repos.worker(), sql, params, timeout: 30_000)
end
