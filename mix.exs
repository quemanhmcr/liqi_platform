defmodule LiqiPlatform.MixProject do
  use Mix.Project

  def project do
    [
      app: :liqi_platform,
      version: "1.0.0-dev",
      elixir: "~> 1.20",
      elixirc_paths: elixirc_paths(Mix.env()),
      test_paths: ["beam/test"],
      config_path: "beam/config/config.exs",
      lockfile: "mix.lock",
      start_permanent: Mix.env() == :prod,
      aliases: aliases(),
      deps: deps(),
      releases: releases()
    ]
  end

  def application do
    [
      mod: {Liqi.Application, []},
      extra_applications: [:logger, :runtime_tools, :crypto, :ssl]
    ]
  end

  defp elixirc_paths(:test), do: ["beam/lib", "beam/test/support"]
  defp elixirc_paths(_), do: ["beam/lib"]

  defp deps do
    [
      {:phoenix, "~> 1.8.9"},
      {:bandit, "~> 1.12.0"},
      {:jason, "~> 1.4.5"},
      {:ecto_sql, "~> 3.14.0"},
      {:postgrex, "~> 0.22.3"},
      {:oban, "~> 2.23.0"},
      {:telemetry, "~> 1.3"},
      {:telemetry_metrics, "~> 1.1"},
      {:telemetry_poller, "~> 1.3"},
      {:liqi_persistence, path: "beam/apps/liqi_persistence"},
      {:liqi_jobs, path: "beam/apps/liqi_jobs"},
      {:liqi_native, path: "native/elixir"}
    ]
  end

  defp releases do
    [
      liqi_platform: [
        include_executables_for: [:unix],
        applications: [runtime_tools: :permanent],
        overlays: ["rel/overlays"]
      ]
    ]
  end

  defp aliases do
    [
      setup: ["deps.get"],
      check: ["format --check-formatted", "compile --warnings-as-errors", "test"]
    ]
  end
end
