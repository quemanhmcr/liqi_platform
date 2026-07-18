ExUnit.start()

root = Path.expand("..", __DIR__)
Code.require_file("lib/liqi/native/reference/sequence_diff.ex", root)
Code.require_file("lib/liqi/native/sequence_diff.ex", root)

defmodule Liqi.Native.SequenceDiff.ReferenceTest do
  use ExUnit.Case, async: true

  alias Liqi.Native.Reference.SequenceDiff, as: Reference
  alias Liqi.Native.SequenceDiff

  defmodule FakeNative do
    def kernel_info_v1 do
      %{
        kernel: "compact_sequence_diff",
        kernel_version: "1",
        nif_abi: "2.15",
        scheduler_class: "regular"
      }
    end

    def compact_sequence_diff_v1(first, last, observed), do: Reference.compact(first, last, observed)
  end

  defmodule MissingNative do
    def kernel_info_v1, do: :erlang.nif_error(:nif_not_loaded)
  end

  defmodule MismatchedNative do
    def kernel_info_v1 do
      %{
        kernel: "compact_sequence_diff",
        kernel_version: "2",
        nif_abi: "2.15",
        scheduler_class: "regular"
      }
    end
  end

  test "reference compacts gaps and counts duplicates" do
    observed = encode([10, 11, 11, 14, 18, 20])

    assert {:ok,
            %{
              missing_ranges: [
                %{first: 12, last: 13},
                %{first: 15, last: 17},
                %{first: 19, last: 19}
              ],
              observed_count: 6,
              unique_count: 5,
              duplicate_count: 1
            }} = Reference.compact(10, 20, observed)
  end

  test "native and reference paths have identical semantics" do
    observed = encode([1, 2, 5, 9])
    assert {:ok, reference, _execution} = SequenceDiff.compact(1, 10, observed, :reference)

    assert {:ok, native, %{implementation: :native, fallback: false}} =
             SequenceDiff.compact_with_native(1, 10, observed, :native_required, FakeNative)

    assert native == reference
  end

  test "feature disable never attempts native code" do
    assert {:ok, _result, %{implementation: :reference, fallback: false}} =
             SequenceDiff.compact_with_native(1, 3, encode([1]), :reference, MissingNative)
  end

  test "optional missing artifact falls back but required mode fails closed" do
    observed = encode([1])

    assert {:ok, _result,
            %{
              implementation: :reference,
              fallback: true,
              fallback_reason: "NATIVE_UNAVAILABLE"
            }} =
             SequenceDiff.compact_with_native(
               1,
               3,
               observed,
               :native_preferred,
               MissingNative
             )

    assert {:error, %{code: "NATIVE_UNAVAILABLE"}, %{implementation: :none}} =
             SequenceDiff.compact_with_native(1, 3, observed, :native_required, MissingNative)
  end

  test "version mismatch is explicit and optional policy can fall back" do
    observed = encode([1])

    assert {:ok, _result, %{fallback_reason: "NATIVE_VERSION_MISMATCH"}} =
             SequenceDiff.compact_with_native(
               1,
               3,
               observed,
               :native_preferred,
               MismatchedNative
             )
  end

  defp encode(values) do
    for value <- values, into: <<>>, do: <<value::unsigned-big-integer-size(64)>>
  end
end
