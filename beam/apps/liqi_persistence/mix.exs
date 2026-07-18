defmodule LiqiPersistence.MixProject do
  use Mix.Project

  def project do
    [
      app: :liqi_persistence,
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
      mod: {LiqiPersistence.Application, []},
      extra_applications: [:logger, :crypto]
    ]
  end

  defp deps do
    [
      {:ecto_sql, ">= 3.14.0 and < 3.15.0"},
      {:postgrex, ">= 0.22.3 and < 0.23.0"},
      {:decimal, ">= 3.0.0 and < 4.0.0"},
      {:jason, "~> 1.4"}
    ]
  end
end
