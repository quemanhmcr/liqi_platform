import Config

config :liqi_platform,
  ecto_repos: [Liqi.Persistence.ApiRepo, Liqi.Persistence.RealtimeRepo, Liqi.Persistence.WorkerRepo],
  runtime_config_provider: Liqi.Runtime.Config,
  persistence_adapter: Liqi.Persistence.PostgresV1,
  native_adapter: Liqi.Native.Fallback,
  start_endpoint: false,
  start_persistence: false,
  start_oban: false,
  start_dispatcher: false,
  start_outbox_worker: false

config :liqi_platform, Liqi.Web.Endpoint,
  url: [host: "localhost"],
  adapter: Bandit.PhoenixAdapter,
  render_errors: [formats: [json: Liqi.Web.ErrorJSON], layout: false],
  pubsub_server: Liqi.PubSub,
  secret_key_base: String.duplicate("development-only-not-a-secret-", 3),
  server: false,
  check_origin: false,
  http: [
    ip: {127, 0, 0, 1},
    port: 4100,
    websocket_options: [
      max_frame_size: 65_536,
      max_fragmented_message_size: 65_536,
      compress: false
    ]
  ],
  websocket: [connect_info: [:peer_data, :x_headers]]

config :phoenix, :json_library, Jason
config :logger, level: :info

import_config "#{config_env()}.exs"
