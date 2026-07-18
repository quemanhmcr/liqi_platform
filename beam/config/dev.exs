import Config

config :liqi_platform,
  start_endpoint: true

config :liqi_platform, Liqi.Web.Endpoint,
  server: true,
  code_reloader: false,
  debug_errors: true
