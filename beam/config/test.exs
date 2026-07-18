import Config

config :liqi_platform,
  start_endpoint: false,
  start_persistence: false,
  start_oban: false,
  persistence_adapter: Liqi.Persistence.Fake,
  native_adapter: Liqi.Native.Fallback

config :liqi_platform, Liqi.Web.Endpoint,
  server: false,
  secret_key_base: String.duplicate("test-only-not-a-secret-", 4)

config :logger, level: :warning
