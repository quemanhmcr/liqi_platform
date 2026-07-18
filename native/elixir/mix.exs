defmodule LiqiNative.MixProject do
  use Mix.Project

  def project do
    [
      app: :liqi_native,
      version: "1.0.0",
      elixir: ">= 1.18.0 and < 2.0.0",
      start_permanent: Mix.env() == :prod,
      deps: deps()
    ]
  end

  def application do
    [extra_applications: [:logger]]
  end

  defp deps do
    [
      {:rustler, "~> 0.38.0", runtime: false}
    ]
  end
end
