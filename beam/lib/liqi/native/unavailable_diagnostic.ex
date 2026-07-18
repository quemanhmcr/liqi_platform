defmodule Liqi.Native.UnavailableDiagnostic do
  @moduledoc false

  # Runtime-owned negative-path diagnostic only. It never participates in command execution.
  def kernel_info_v1, do: :erlang.nif_error(:nif_not_loaded)
end
