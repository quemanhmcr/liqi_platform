import Config

config :liqi_persistence, start_repos: false

import_config "#{config_env()}.exs"
