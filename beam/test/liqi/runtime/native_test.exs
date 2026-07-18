defmodule Liqi.Runtime.NativeTest do
  use ExUnit.Case, async: false

  test "provider reference returns compact missing ranges" do
    old = Application.get_env(:liqi_platform, :runtime_config)

    Application.put_env(
      :liqi_platform,
      :runtime_config,
      %Liqi.Runtime.Config{
        environment: "test",
        release_id: "test",
        service_identity: "liqi-platform",
        native_mode: :disabled
      }
    )

    on_exit(fn -> restore(old) end)

    assert {:ok, [%{first: 2, last: 2}, %{first: 4, last: 4}]} =
             Liqi.Native.Kernel.sequence_diff([1, 3, 5], 0, 5)

    assert {:ok, []} = Liqi.Native.Kernel.sequence_diff([], 3, 3)

    assert {:error, {:native_kernel, "NATIVE_WINDOW_TOO_LARGE", false}} =
             Liqi.Native.Kernel.sequence_diff([], 0, 70_000)
  end

  test "optional missing artifact falls back but required readiness fails closed" do
    old = Application.get_env(:liqi_platform, :runtime_config)
    on_exit(fn -> restore(old) end)

    Application.put_env(
      :liqi_platform,
      :runtime_config,
      %Liqi.Runtime.Config{
        environment: "test",
        release_id: "test",
        service_identity: "liqi-platform",
        native_mode: :optional
      }
    )

    assert :ok = Liqi.Native.Kernel.readiness()
    assert {:ok, []} = Liqi.Native.Kernel.sequence_diff([1, 2], 0, 2)

    Application.put_env(
      :liqi_platform,
      :runtime_config,
      %Liqi.Runtime.Config{
        environment: "test",
        release_id: "test",
        service_identity: "liqi-platform",
        native_mode: :required
      }
    )

    assert {:error, {:native_unavailable, "NATIVE_UNAVAILABLE"}} =
             Liqi.Native.Kernel.readiness()
  end

  defp restore(nil), do: Application.delete_env(:liqi_platform, :runtime_config)
  defp restore(value), do: Application.put_env(:liqi_platform, :runtime_config, value)
end
