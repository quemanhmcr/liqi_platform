defmodule LiqiPersistence.ConfigTest do
  use ExUnit.Case, async: true

  test "published pool sizes fit the provider contract" do
    assert %{command: 12, realtime: 4, worker: 6} = LiqiPersistence.Config.pool_sizes()
    assert Enum.sum(Map.values(LiqiPersistence.Config.pool_sizes())) == 22
  end
end
