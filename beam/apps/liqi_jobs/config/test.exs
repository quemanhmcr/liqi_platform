import Config

integration = System.get_env("LIQI_DATABASE_INTEGRATION") == "1"
config :liqi_persistence, start_repos: integration
config :liqi_jobs, start_oban: integration
