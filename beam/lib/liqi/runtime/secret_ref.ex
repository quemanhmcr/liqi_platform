defmodule Liqi.Runtime.SecretRef do
  @moduledoc "Resolves already-materialized local secret references without logging contents."

  @max_secret_bytes 8_192

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

  defp validate_secret_value(value) when byte_size(value) in 1..@max_secret_bytes,
    do: {:ok, value}

  defp validate_secret_value(_), do: {:error, :invalid_secret_value}
end
