defmodule Liqi.Persistence.SecretResolver do
  @moduledoc "Resolves already-materialized local secret references and never logs their contents."

  def resolve(reference) do
    with {:ok, value} <- resolve_value(reference), do: validate_database_url(value)
  end

  def resolve_value("file://" <> path), do: read_single_value(path)
  def resolve_value("systemd-credential://" <> name), do: resolve_credential(name)
  def resolve_value("env://" <> name), do: fetch_env(name)
  def resolve_value(nil), do: {:error, :missing_reference}
  def resolve_value("oci-vault://" <> _), do: {:error, :vault_reference_not_materialized}
  def resolve_value(_), do: {:error, :unsupported_reference}

  defp resolve_credential(name) do
    case System.get_env("CREDENTIALS_DIRECTORY") do
      nil -> {:error, :credentials_directory_missing}
      directory -> read_single_value(Path.join(directory, name))
    end
  end

  defp fetch_env(name) do
    case System.get_env(name) do
      nil -> {:error, :environment_value_missing}
      value -> validate_secret_value(String.trim(value))
    end
  end

  defp read_single_value(path) do
    case File.read(path) do
      {:ok, contents} -> validate_secret_value(String.trim(contents))
      {:error, reason} -> {:error, reason}
    end
  end

  defp validate_secret_value(value) when byte_size(value) in 1..8192, do: {:ok, value}
  defp validate_secret_value(_), do: {:error, :invalid_secret_value}

  defp validate_database_url("postgres" <> _ = url), do: {:ok, url}
  defp validate_database_url(_), do: {:error, :invalid_database_url}
end
