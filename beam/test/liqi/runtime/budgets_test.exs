defmodule Liqi.Runtime.BudgetsTest do
  use ExUnit.Case, async: false

  test "native budget rejects instead of queueing" do
    assert :ok = Liqi.Runtime.Budgets.acquire(:native)
    assert :ok = Liqi.Runtime.Budgets.acquire(:native)

    on_exit(fn ->
      Enum.each(List.duplicate(:permit, Liqi.Runtime.Budgets.in_use(:native)), fn _ ->
        Liqi.Runtime.Budgets.release(:native)
      end)
    end)

    assert {:error, :capacity} = Liqi.Runtime.Budgets.acquire(:native)

    Enum.each(1..2, fn _ -> assert :ok = Liqi.Runtime.Budgets.release(:native) end)
    assert Liqi.Runtime.Budgets.in_use(:native) == 0
    Process.delete(:native_budget_released)
  end

  test "partition routing is deterministic" do
    key = "session:#{Liqi.Runtime.Id.uuid4()}"

    assert Liqi.Runtime.ActorRouter.partition_for(key, 4) ==
             Liqi.Runtime.ActorRouter.partition_for(key, 4)

    assert Liqi.Runtime.ActorRouter.partition_for(key, 4) in 0..3
  end

  test "read coordinator coalesces identical concurrent reads" do
    parent = self()
    key = {:coalesce, Liqi.Runtime.Id.uuid4()}

    fun = fn ->
      send(parent, :executed)
      Process.sleep(50)
      {:ok, :value}
    end

    tasks = for _ <- 1..8, do: Task.async(fn -> Liqi.Runtime.ReadCoordinator.fetch(key, fun) end)
    assert Enum.map(tasks, &Task.await(&1, 1_000)) == List.duplicate({:ok, :value}, 8)
    assert_receive :executed
    refute_receive :executed, 100
  end
end
