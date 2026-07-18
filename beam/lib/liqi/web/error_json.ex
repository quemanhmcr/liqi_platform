defmodule Liqi.Web.ErrorJSON do
  def render(_template, _assigns),
    do: %{
      error: %{
        version: "1",
        code: "internal.error",
        message: "An internal error occurred.",
        retryable: false
      }
    }
end
