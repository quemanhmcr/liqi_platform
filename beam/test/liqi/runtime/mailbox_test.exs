defmodule Liqi.Runtime.MailboxTest do
  use ExUnit.Case, async: false

  defmodule SuspendedServer do
    use GenServer
    def start_link(_), do: GenServer.start_link(__MODULE__, :ok)
    def init(:ok), do: {:ok, :ok}
    def handle_call(:ping, _from, state), do: {:reply, :pong, state}
  end

  test "actor calls reject at the hard mailbox threshold" do
    {:ok, pid} = start_supervised(SuspendedServer)
    :ok = :sys.suspend(pid)
    for _ <- 1..128, do: send(pid, :backlog)
    assert {:error, :actor_overloaded} = Liqi.Runtime.ActorRouter.call(pid, :ping, 50)
    :ok = :sys.resume(pid)
  end
end
