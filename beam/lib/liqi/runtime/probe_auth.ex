defmodule Liqi.Runtime.ProbeAuth do
  @moduledoc "Fail-closed authorization for the operator-only platform walking skeleton."

  import Plug.Conn, only: [get_req_header: 2]

  @header "x-liqi-probe-token"

  def authorize_conn(conn), do: authorize_values(get_req_header(conn, @header))

  def authorize_headers(headers) when is_list(headers) do
    values =
      for {name, value} <- headers,
          is_binary(name) and is_binary(value),
          String.downcase(name) == @header,
          do: value

    authorize_values(values)
  end

  def authorize_headers(%{} = headers) do
    value = Map.get(headers, @header) || Map.get(headers, :"x-liqi-probe-token")
    authorize_values(List.wrap(value))
  end

  def authorize_headers(_), do: {:error, :unauthorized}

  defp authorize_values([provided]) when is_binary(provided) do
    with {:ok, config} <- Liqi.Runtime.Config.load(),
         {:ok, expected} <- Liqi.Runtime.SecretRef.resolve_value(config.probe_token_ref),
         true <- secure_equal?(provided, expected) do
      :ok
    else
      _ -> {:error, :unauthorized}
    end
  end

  defp authorize_values(_), do: {:error, :unauthorized}

  defp secure_equal?(left, right) when byte_size(left) == byte_size(right),
    do: Plug.Crypto.secure_compare(left, right)

  defp secure_equal?(_, _), do: false
end
