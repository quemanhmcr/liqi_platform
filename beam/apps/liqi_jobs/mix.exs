defmodule LiqiJobs.MixProject do
  use Mix.Project

  def project do
    [
      app: :liqi_jobs,
      version: "1.0.0",
      elixir: "~> 1.20",
      start_permanent: Mix.env() == :prod,
      build_path: System.get_env("LIQI_MIX_BUILD_PATH", "_build"),
      deps_path: System.get_env("LIQI_MIX_DEPS_PATH", "deps"),
      lockfile: System.get_env("LIQI_MIX_LOCKFILE", "mix.lock"),
      deps: deps()
    ]
  end

  def application do
    [
      mod: {LiqiJobs.Application, []},
      extra_applications: [:logger]
    ]
  end

  defp deps do
    [
      {:liqi_persistence, path: "../liqi_persistence"},
      {:oban, ">= 2.23.0 and < 2.24.0"}
    ]
  end
end
