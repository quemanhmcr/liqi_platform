defmodule Liqi.Web.DrainController do
  use Phoenix.Controller, formats: [:json]

  def create(conn, _params) do
    with {:ok, config} <- Liqi.Runtime.Config.load(),
         {:ok, expected} <- Liqi.Runtime.SecretRef.resolve_value(config.drain_token_ref),
         provided when is_binary(provided) <- List.first(get_req_header(conn, "x-liqi-drain-token")),
         true <- secure_equal?(provided, expected),
         :ok <- Liqi.Runtime.Drain.begin() do
      json(conn, %{status: "draining", deadlineMs: config.shutdown_deadline_ms})
    else
      _ ->
        conn
        |> put_status(:unauthorized)
        |> json(Liqi.Web.ErrorModel.build("runtime.unavailable", conn))
    end
  end

  defp secure_equal?(left, right) when byte_size(left) == byte_size(right),
    do: Plug.Crypto.secure_compare(left, right)

  defp secure_equal?(_, _), do: false
end
