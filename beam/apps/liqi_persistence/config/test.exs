import Config

config :liqi_persistence,
  start_repos: System.get_env("LIQI_DATABASE_INTEGRATION") == "1"
