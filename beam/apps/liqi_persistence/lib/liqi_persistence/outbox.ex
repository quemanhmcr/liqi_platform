defmodule LiqiPersistence.Outbox do
  @moduledoc "Bounded claim/ack/fail and terminal-effect API over the shared durable outbox."
  alias LiqiPersistence.{Query, Repos}

  @claim_sql """
  SELECT event_id::text, claim_token::text, attempt_no, protocol_version, message_id::text,
         correlation_id::text, causation_id::text, trace_context, deadline_at, actor_key,
         aggregate_key, priority, payload_type, event_type, payload_version, event_version,
         ordering_key, occurred_at, producer, payload, metadata, lease_expires_at
  FROM platform.claim_outbox_v1($1::text, $2::integer, $3::integer)
  """

  def claim(consumer_id, batch_size \\ 10, lease_seconds \\ 30),
    do:
      Query.all(Repos.worker(), @claim_sql, [consumer_id, batch_size, lease_seconds],
        timeout: 30_000
      )

  def ack(event_id, claim_token, consumer_id),
    do:
      scalar(
        "SELECT platform.ack_outbox_v1($1::text::uuid, $2::text::uuid, $3::text)",
        [event_id, claim_token, consumer_id]
      )

  def fail(event_id, claim_token, consumer_id, error_code, retry_at),
    do:
      scalar(
        "SELECT platform.fail_outbox_v1($1::text::uuid, $2::text::uuid, $3::text, $4::text, $5::timestamptz)",
        [event_id, claim_token, consumer_id, error_code, retry_at]
      )

  def apply_probe_effect(event_id, claim_token, consumer_id),
    do:
      scalar(
        "SELECT platform.apply_probe_effect_and_ack_v1($1::text::uuid, $2::text::uuid, $3::text)",
        [event_id, claim_token, consumer_id]
      )

  def apply_probe_effect(%{"protocol_version" => 1} = event, consumer_id),
    do: apply_probe_effect(event["event_id"], event["claim_token"], consumer_id)

  def apply_probe_effect(%{"protocol_version" => 0} = event, consumer_id),
    do:
      scalar(
        "SELECT platform.apply_probe_effect_and_ack_v0($1::text::uuid, $2::text::uuid, $3::text)",
        [event["event_id"], event["claim_token"], consumer_id]
      )

  defp scalar(sql, params), do: Query.scalar(Repos.worker(), sql, params, timeout: 30_000)
end
