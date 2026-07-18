defmodule Liqi.Runtime.Envelope do
  @moduledoc "Versioned internal command/message envelope with bounded deadlines."

  @priorities ~w(durable realtime ephemeral telemetry)a
  @enforce_keys [
    :protocol_version,
    :message_id,
    :correlation_id,
    :causation_id,
    :deadline,
    :actor_key,
    :priority,
    :payload_type,
    :payload_version,
    :payload
  ]
  defstruct protocol_version: "1",
            message_id: nil,
            correlation_id: nil,
            causation_id: nil,
            trace_context: %{},
            deadline: nil,
            actor_key: nil,
            priority: :durable,
            payload_type: nil,
            payload_version: 1,
            payload: %{}

  @type t :: %__MODULE__{}

  @spec new(keyword()) :: {:ok, t()} | {:error, term()}
  def new(attrs) do
    now = System.system_time(:millisecond)
    timeout_ms = Keyword.get(attrs, :timeout_ms, 5_000)
    message_id = Keyword.get_lazy(attrs, :message_id, &Liqi.Runtime.Id.uuid4/0)
    correlation_id = Keyword.get(attrs, :correlation_id, message_id)

    envelope = %__MODULE__{
      protocol_version: "1",
      message_id: message_id,
      correlation_id: correlation_id,
      causation_id: Keyword.get(attrs, :causation_id, message_id),
      trace_context: Keyword.get(attrs, :trace_context, %{}),
      deadline: Keyword.get(attrs, :deadline, now + timeout_ms),
      actor_key: Keyword.fetch!(attrs, :actor_key),
      priority: Keyword.get(attrs, :priority, :durable),
      payload_type: Keyword.fetch!(attrs, :payload_type),
      payload_version: Keyword.get(attrs, :payload_version, 1),
      payload: Keyword.get(attrs, :payload, %{})
    }

    validate(envelope)
  end

  @spec validate(t()) :: {:ok, t()} | {:error, term()}
  def validate(%__MODULE__{} = envelope) do
    cond do
      envelope.protocol_version != "1" -> {:error, :unsupported_protocol_version}
      not Liqi.Runtime.Id.valid_uuid?(envelope.message_id) -> {:error, :invalid_message_id}
      not Liqi.Runtime.Id.valid_uuid?(envelope.correlation_id) -> {:error, :invalid_correlation_id}
      not Liqi.Runtime.Id.valid_uuid?(envelope.causation_id) -> {:error, :invalid_causation_id}
      envelope.priority not in @priorities -> {:error, :invalid_priority}
      not is_integer(envelope.deadline) -> {:error, :invalid_deadline}
      not bounded_string?(envelope.actor_key, 160) -> {:error, :invalid_actor_key}
      not bounded_string?(envelope.payload_type, 160) -> {:error, :invalid_payload_type}
      envelope.payload_version not in 1..2_147_483_647 -> {:error, :invalid_payload_version}
      not is_map(envelope.payload) -> {:error, :invalid_payload}
      not is_map(envelope.trace_context) -> {:error, :invalid_trace_context}
      true -> {:ok, envelope}
    end
  end

  @spec expired?(t(), integer()) :: boolean()
  def expired?(%__MODULE__{deadline: deadline}, now_ms \\ System.system_time(:millisecond)),
    do: now_ms >= deadline

  @spec remaining_ms(t(), integer()) :: non_neg_integer()
  def remaining_ms(%__MODULE__{deadline: deadline}, now_ms \\ System.system_time(:millisecond)),
    do: max(deadline - now_ms, 0)

  @spec to_map(t()) :: map()
  def to_map(%__MODULE__{} = envelope) do
    %{
      "protocol_version" => envelope.protocol_version,
      "message_id" => envelope.message_id,
      "correlation_id" => envelope.correlation_id,
      "causation_id" => envelope.causation_id,
      "trace_context" => envelope.trace_context,
      "deadline" => envelope.deadline,
      "actor_key" => envelope.actor_key,
      "priority" => Atom.to_string(envelope.priority),
      "payload_type" => envelope.payload_type,
      "payload_version" => envelope.payload_version,
      "payload" => envelope.payload
    }
  end

  @spec from_map(map()) :: {:ok, t()} | {:error, term()}
  def from_map(map) when is_map(map) do
    with {:ok, priority} <- priority(map["priority"]) do
      validate(%__MODULE__{
        protocol_version: map["protocol_version"],
        message_id: map["message_id"],
        correlation_id: map["correlation_id"],
        causation_id: map["causation_id"],
        trace_context: map["trace_context"] || %{},
        deadline: map["deadline"],
        actor_key: map["actor_key"],
        priority: priority,
        payload_type: map["payload_type"],
        payload_version: map["payload_version"],
        payload: map["payload"]
      })
    end
  end

  def from_map(_), do: {:error, :invalid_envelope}

  defp priority(value) when value in ~w(durable realtime ephemeral telemetry),
    do: {:ok, String.to_existing_atom(value)}

  defp priority(_), do: {:error, :invalid_priority}
  defp bounded_string?(value, max) when is_binary(value), do: byte_size(value) in 1..max
  defp bounded_string?(_, _), do: false
end
