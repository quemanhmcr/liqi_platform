defmodule Liqi.Web.NativeProbeController do
  use Phoenix.Controller, formats: [:json]

  def create(
        conn,
        %{
          "expectedFirst" => expected_first,
          "expectedLast" => expected_last,
          "observedSequences" => observed
        }
      ) do
    with :ok <- Liqi.Runtime.ProbeAuth.authorize_conn(conn),
         true <- valid_input?(expected_first, expected_last, observed),
         {:ok, result} <- Liqi.Native.Kernel.diagnostic(observed, expected_first, expected_last),
         true <- result.parity do
      json(conn, result)
    else
      {:error, :unauthorized} -> render_error(conn, 401, "auth.unauthorized")
      false -> render_error(conn, 400, "validation.failed")
      {:error, :native_capacity} -> render_error(conn, 429, "capacity.native", retryable: true)
      {:error, _reason} -> render_error(conn, 503, "runtime.unavailable", retryable: true)
    end
  end

  def create(conn, _params) do
    case Liqi.Runtime.ProbeAuth.authorize_conn(conn) do
      :ok -> render_error(conn, 400, "validation.failed")
      {:error, :unauthorized} -> render_error(conn, 401, "auth.unauthorized")
    end
  end

  defp valid_input?(first, last, observed)
       when is_integer(first) and is_integer(last) and is_list(observed) and
              first >= 0 and last >= first and last - first + 1 <= 65_536 and
              length(observed) <= 2_048 do
    Enum.all?(observed, &(is_integer(&1) and &1 >= first and &1 <= last)) and
      observed == Enum.sort(observed)
  end

  defp valid_input?(_, _, _), do: false

  defp render_error(conn, status, code, opts \\ []) do
    conn |> put_status(status) |> json(Liqi.Web.ErrorModel.build(code, conn, opts))
  end
end
