defmodule Liqi.Persistence.RepoConfig do
  @moduledoc "Resolves one bounded credential bundle into three least-privilege Repo URLs."

  @pool_sizes %{api: 12, realtime: 4, worker: 6}
  @bundle_roles %{api: "command", realtime: "realtime", worker: "worker"}
  @role_users %{api: "liqi_api", realtime: "liqi_realtime", worker: "liqi_worker"}
  @bundle_keys MapSet.new(Map.values(@bundle_roles))

  def init(role, config) do
    with {:ok, runtime} <- Liqi.Runtime.Config.load(),
         {:ok, url} <- role_url(runtime, role) do
      {:ok,
       Keyword.merge(config,
         url: url,
         pool_size: Map.fetch!(@pool_sizes, role),
         prepare: :unnamed,
         queue_target: 50,
         queue_interval: 1_000,
         timeout: timeout(role),
         connect_timeout: 5_000,
         show_sensitive_data_on_connection_error: false,
         log: false
       )}
    else
      {:error, reason} -> {:error, {:database_secret_unavailable, role, reason}}
    end
  end

  defp timeout(:api), do: 5_000
  defp timeout(:realtime), do: 3_000
  defp timeout(:worker), do: 30_000

  defp role_url(runtime, role) do
    case System.get_env(compatibility_reference_env(role)) do
      reference when is_binary(reference) and reference != "" ->
        resolve_url(reference, runtime, role)

      _ ->
        resolve_bundle_url(runtime, role)
    end
  end

  defp resolve_bundle_url(
         %{database_secret_ref: reference, database_credential_format: "role-url-bundle-v1"} =
           runtime,
         role
       )
       when is_binary(reference) do
    with {:ok, contents} <- Liqi.Persistence.SecretResolver.resolve_value(reference),
         {:ok, bundle} <- decode_bundle(contents),
         :ok <- validate_bundle_keys(bundle),
         {:ok, url} <- Map.fetch(bundle, Map.fetch!(@bundle_roles, role)),
         :ok <- validate_url(url, runtime, role) do
      {:ok, url}
    else
      :error -> {:error, :credential_bundle_role_missing}
      {:error, reason} -> {:error, reason}
    end
  end

  defp resolve_bundle_url(%{schema_version: "0", database_secret_ref: reference} = runtime, role)
       when is_binary(reference),
       do: resolve_url(reference, runtime, role)

  defp resolve_bundle_url(_runtime, _role), do: {:error, :reference_missing}

  defp decode_bundle(contents) do
    case Jason.decode(contents) do
      {:ok, bundle} when is_map(bundle) -> {:ok, bundle}
      {:ok, _} -> {:error, :credential_bundle_invalid}
      {:error, %Jason.DecodeError{}} -> {:error, :credential_bundle_invalid}
    end
  end

  defp validate_bundle_keys(bundle) do
    if MapSet.new(Map.keys(bundle)) == @bundle_keys,
      do: :ok,
      else: {:error, :credential_bundle_keys_invalid}
  end

  defp resolve_url(reference, runtime, role) do
    with {:ok, url} <- Liqi.Persistence.SecretResolver.resolve(reference),
         :ok <- validate_url(url, runtime, role) do
      {:ok, url}
    end
  end

  defp validate_url(url, runtime, role) when is_binary(url) do
    uri = URI.parse(url)
    expected_user = Map.fetch!(@role_users, role)

    with true <- uri.scheme in ["postgres", "postgresql"],
         true <- is_binary(uri.host) and uri.host != "",
         true <- database_user(uri.userinfo) == expected_user,
         :ok <- validate_production_endpoint(uri, runtime.environment) do
      :ok
    else
      {:error, reason} -> {:error, reason}
      _ -> {:error, :database_url_invalid}
    end
  end

  defp validate_url(_, _, _), do: {:error, :database_url_invalid}

  defp validate_production_endpoint(uri, "production") do
    if uri.host == "127.0.0.1" and uri.port == 6432 and uri.path == "/liqi" and
         is_nil(uri.query) and is_nil(uri.fragment),
       do: :ok,
       else: {:error, :database_endpoint_not_loopback_pgbouncer}
  end

  defp validate_production_endpoint(_uri, _environment), do: :ok

  defp database_user(userinfo) when is_binary(userinfo) do
    userinfo |> String.split(":", parts: 2) |> List.first()
  end

  defp database_user(_), do: nil

  defp compatibility_reference_env(:api), do: "LIQI_API_DATABASE_SECRET_REF"
  defp compatibility_reference_env(:realtime), do: "LIQI_REALTIME_DATABASE_SECRET_REF"
  defp compatibility_reference_env(:worker), do: "LIQI_WORKER_DATABASE_SECRET_REF"
end
