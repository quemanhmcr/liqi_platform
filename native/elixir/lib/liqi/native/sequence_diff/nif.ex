defmodule Liqi.Native.SequenceDiff.Nif do
  @moduledoc false

  use Rustler,
    otp_app: :liqi_native,
    crate: :liqi_sequence_diff_nif,
    skip_compilation?: true,
    load_from: {:liqi_native, "priv/native/libliqi_sequence_diff_nif"}

  def compact_sequence_diff_v1(_expected_first, _expected_last, _observed_big_endian),
    do: :erlang.nif_error(:nif_not_loaded)

  def kernel_info_v1, do: :erlang.nif_error(:nif_not_loaded)
end
