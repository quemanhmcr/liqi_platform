defmodule LiqiJobs.QueuePolicy do
  @moduledoc "Bounded V1 Oban queue policy."
  @queues [
    maintenance: [limit: 1],
    push: [limit: 2],
    provider: [limit: 1],
    media: [limit: 1],
    cleanup: [limit: 1],
    recovery: [limit: 1, paused: true]
  ]
  def queues, do: @queues

  def configured_concurrency,
    do: Enum.reduce(@queues, 0, fn {_name, opts}, total -> total + Keyword.fetch!(opts, :limit) end)

  def active_concurrency, do: configured_concurrency() - 1
end
