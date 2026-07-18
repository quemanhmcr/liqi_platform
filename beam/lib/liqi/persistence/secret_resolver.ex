defmodule Liqi.Persistence.SecretResolver do
  @moduledoc "Database-specific validation over already-materialized runtime secret references."

  def resolve(reference) do
    with {:ok, value} <- Liqi.Runtime.SecretRef.resolve_value(reference),
         do: validate_database_url(value)
  end

  defdelegate resolve_value(reference), to: Liqi.Runtime.SecretRef

  defp validate_database_url("postgres" <> _ = url), do: {:ok, url}
  defp validate_database_url(_), do: {:error, :invalid_database_url}
end
