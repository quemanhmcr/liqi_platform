defmodule Liqi.Web.MetadataController do
  use Phoenix.Controller, formats: [:json]
  def show(conn, _params), do: json(conn, Liqi.Runtime.Health.metadata())
end
