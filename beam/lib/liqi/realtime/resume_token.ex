defmodule Liqi.Realtime.ResumeToken do
  @moduledoc false

  @salt "liqi-session-resume-v1"

  def sign(session_id, device_id, cursor) do
    payload = %{
      "version" => "1",
      "session_id" => session_id,
      "device_id" => device_id,
      "cursor" => cursor,
      "issued_at" => System.system_time(:second)
    }

    Phoenix.Token.sign(Liqi.Web.Endpoint, @salt, payload)
  end

  def verify(token, expected_session_id, expected_device_id) do
    with {:ok, config} <- Liqi.Runtime.Config.load(),
         {:ok, payload} <-
           Phoenix.Token.verify(Liqi.Web.Endpoint, @salt, token,
             max_age: div(config.resume_window_ms, 1000)
           ),
         "1" <- payload["version"],
         ^expected_session_id <- payload["session_id"],
         ^expected_device_id <- payload["device_id"],
         cursor when is_integer(cursor) and cursor >= 0 <- payload["cursor"] do
      {:ok, cursor}
    else
      _ -> {:error, :invalid_resume_token}
    end
  end
end
