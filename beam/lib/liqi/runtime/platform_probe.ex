defmodule Liqi.Runtime.PlatformProbe do
  @moduledoc false

  def execute(%Liqi.Runtime.Envelope{} = envelope, probe_id, idempotency_key) do
    with false <- Liqi.Runtime.Envelope.expired?(envelope),
         {:ok, command} <- Liqi.Persistence.ProbeCommand.new(envelope, probe_id, idempotency_key) do
      adapter().request_probe(command)
    else
      true -> {:error, :deadline_exceeded}
      {:error, reason} -> {:error, reason}
    end
  end

  defp adapter, do: Application.fetch_env!(:liqi_platform, :persistence_adapter)
end
