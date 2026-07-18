defmodule Liqi.Persistence.ProbeCommand do
  @moduledoc "Validated consumer value for the database-owned V1 probe command."

  @enforce_keys [
    :envelope,
    :probe_id,
    :idempotency_scope,
    :idempotency_key,
    :request_fingerprint,
    :expected_version
  ]
  defstruct [
    :envelope,
    :probe_id,
    :idempotency_scope,
    :idempotency_key,
    :request_fingerprint,
    :expected_version
  ]

  @type t :: %__MODULE__{
          envelope: Liqi.Runtime.Envelope.t(),
          probe_id: String.t(),
          idempotency_scope: String.t(),
          idempotency_key: String.t(),
          request_fingerprint: String.t(),
          expected_version: non_neg_integer()
        }

  @spec new(Liqi.Runtime.Envelope.t(), String.t(), String.t()) :: {:ok, t()} | {:error, atom()}
  def new(%Liqi.Runtime.Envelope{} = envelope, probe_id, idempotency_key) do
    with true <- Liqi.Runtime.Id.valid_uuid?(probe_id),
         true <- valid_key?(idempotency_key) do
      payload = %{
        "clientProbeId" => probe_id,
        "payloadType" => envelope.payload_type,
        "payloadVersion" => envelope.payload_version
      }

      {:ok,
       %__MODULE__{
         envelope: envelope,
         probe_id: probe_id,
         idempotency_scope: "platform.probe.create.v1",
         idempotency_key: idempotency_key,
         request_fingerprint: fingerprint(payload),
         expected_version: 0
       }}
    else
      false -> {:error, :validation_failed}
    end
  end

  @spec event_id(t()) :: String.t()
  def event_id(%__MODULE__{} = command) do
    Liqi.Runtime.Id.deterministic_uuid(
      "liqi-platform-probe-event-v1",
      command.idempotency_scope <> <<0>> <> command.idempotency_key
    )
  end

  defp valid_key?(value) when is_binary(value), do: byte_size(value) in 1..128
  defp valid_key?(_), do: false

  defp fingerprint(payload) do
    encoded = Jason.encode_to_iodata!(payload)
    :crypto.hash(:sha256, encoded) |> Base.encode16(case: :lower)
  end
end
