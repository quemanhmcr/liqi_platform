import Config

resolve_secret = fn
  "file://" <> path ->
    File.read!(path) |> String.trim()

  "systemd-credential://" <> name ->
    directory =
      System.get_env("CREDENTIALS_DIRECTORY") ||
        System.fetch_env!("LIQI_CREDENTIALS_DIRECTORY")

    File.read!(Path.join(directory, name)) |> String.trim()

  other ->
    raise "unsupported materialized secret reference: #{inspect(other)}"
end

if config_env() == :prod do
  runtime_path = System.fetch_env!("LIQI_RUNTIME_CONFIG_PATH")
  runtime = runtime_path |> File.read!() |> Jason.decode!()
  http = Map.fetch!(runtime, "http")
  endpoint_secret = http |> Map.fetch!("secretRef") |> resolve_secret.()

  config :liqi_platform, Liqi.Web.Endpoint,
    http: [
      ip: {127, 0, 0, 1},
      port: Map.fetch!(http, "port"),
      websocket_options: [
        max_frame_size: 65_536,
        max_fragmented_message_size: 65_536,
        compress: false
      ]
    ],
    secret_key_base: endpoint_secret,
    server: true
end
