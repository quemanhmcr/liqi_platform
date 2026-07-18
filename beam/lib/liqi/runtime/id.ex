defmodule Liqi.Runtime.Id do
  @moduledoc false
  import Bitwise

  @spec uuid4() :: String.t()
  def uuid4 do
    <<a::32, b::16, c::16, d::16, e::48>> = :crypto.strong_rand_bytes(16)
    format(a, b, bor(band(c, 0x0FFF), 0x4000), bor(band(d, 0x3FFF), 0x8000), e)
  end

  @spec deterministic_uuid(binary(), binary()) :: String.t()
  def deterministic_uuid(namespace, value) do
    <<a::32, b::16, c::16, d::16, e::48, _::binary>> =
      :crypto.hash(:sha256, namespace <> <<0>> <> value)

    format(a, b, bor(band(c, 0x0FFF), 0x5000), bor(band(d, 0x3FFF), 0x8000), e)
  end

  @spec valid_uuid?(term()) :: boolean()
  def valid_uuid?(value) when is_binary(value) do
    Regex.match?(
      ~r/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
      value
    )
  end

  def valid_uuid?(_), do: false

  defp format(a, b, c, d, e) do
    :io_lib.format(~c"~8.16.0b-~4.16.0b-~4.16.0b-~4.16.0b-~12.16.0b", [a, b, c, d, e])
    |> IO.iodata_to_binary()
    |> String.downcase()
  end
end
