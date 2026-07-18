defmodule Liqi.Runtime.NativeTest do
  use ExUnit.Case, async: true

  test "pure fallback returns missing bounded sequences" do
    assert {:ok, [2, 4]} = Liqi.Native.Fallback.sequence_diff([1, 3, 5], 0, 5)
    assert {:ok, []} = Liqi.Native.Fallback.sequence_diff([], 3, 3)
    assert {:error, :range_too_large} = Liqi.Native.Fallback.sequence_diff([], 0, 5000)
  end
end
