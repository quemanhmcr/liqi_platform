defmodule LiqiJobs.QueuePolicyTest do
  use ExUnit.Case, async: true

  test "queue concurrency is bounded and recovery is paused" do
    assert LiqiJobs.QueuePolicy.configured_concurrency() == 7
    assert LiqiJobs.QueuePolicy.active_concurrency() == 6
    assert {:recovery, [limit: 1, paused: true]} in LiqiJobs.QueuePolicy.queues()
  end
end
