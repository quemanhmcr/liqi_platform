import Config

config :liqi_persistence, start_repos: false
config :liqi_jobs, start_oban: false

import_config "#{config_env()}.exs"
