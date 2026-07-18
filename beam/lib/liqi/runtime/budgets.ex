defmodule Liqi.Runtime.Budgets do
  @moduledoc "Lock-free reject-before-work concurrency budgets. No wait queue is maintained."
  use GenServer

  @budget_names [:endpoint, :database, :reconnect, :native, :task]

  def start_link(config), do: GenServer.start_link(__MODULE__, config, name: __MODULE__)

  def with_permit(name, fun) when name in @budget_names and is_function(fun, 0) do
    case acquire(name) do
      :ok ->
        try do
          fun.()
        after
          release(name)
        end

      {:error, :capacity} = error ->
        error
    end
  end

  def acquire(name) when name in @budget_names do
    %{counter: counter, limit: limit} = fetch!(name)
    do_acquire(counter, limit)
  end

  def release(name) when name in @budget_names do
    %{counter: counter} = fetch!(name)
    :atomics.sub_get(counter, 1, 1)
    :ok
  end

  def in_use(name) when name in @budget_names do
    %{counter: counter} = fetch!(name)
    :atomics.get(counter, 1)
  end

  @impl true
  def init(config) do
    limits = %{
      endpoint: config.endpoint_concurrency,
      database: config.database_concurrency,
      reconnect: config.reconnect_concurrency,
      native: config.native_concurrency,
      task: config.bounded_tasks
    }

    Enum.each(limits, fn {name, limit} ->
      :persistent_term.put({__MODULE__, name}, %{counter: :atomics.new(1, []), limit: limit})
    end)

    {:ok, limits}
  end

  @impl true
  def terminate(_reason, _state) do
    Enum.each(@budget_names, &:persistent_term.erase({__MODULE__, &1}))
    :ok
  end

  defp fetch!(name), do: :persistent_term.get({__MODULE__, name})

  defp do_acquire(counter, limit) do
    current = :atomics.get(counter, 1)

    cond do
      current >= limit ->
        {:error, :capacity}

      :atomics.compare_exchange(counter, 1, current, current + 1) == :ok ->
        :ok

      true ->
        do_acquire(counter, limit)
    end
  end
end
