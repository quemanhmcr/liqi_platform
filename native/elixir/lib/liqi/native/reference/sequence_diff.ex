defmodule Liqi.Native.Reference.SequenceDiff do
  @moduledoc """
  Pure Elixir reference semantics for the bounded compact sequence-diff kernel.

  The observed binary is a sequence of unsigned 64-bit big-endian integers in
  non-decreasing order. Duplicate values are counted and ignored for gap calculation.
  """

  @max_u64 18_446_744_073_709_551_615
  @max_observed_sequences 2_048
  @max_input_bytes @max_observed_sequences * 8
  @max_window_span 65_536

  @type sequence_range :: %{first: non_neg_integer(), last: non_neg_integer()}
  @type result :: %{
          missing_ranges: [sequence_range()],
          observed_count: non_neg_integer(),
          unique_count: non_neg_integer(),
          duplicate_count: non_neg_integer()
        }
  @type native_error :: %{code: String.t(), retryable: false, detail: String.t()}

  @spec compact(integer(), integer(), binary()) :: {:ok, result()} | {:error, native_error()}
  def compact(expected_first, expected_last, observed_big_endian) do
    with :ok <- validate_window(expected_first, expected_last),
         :ok <- validate_input(observed_big_endian) do
      do_compact(
        observed_big_endian,
        expected_first,
        expected_last,
        nil,
        expected_first,
        [],
        0,
        0,
        0
      )
    end
  end

  @spec limits() :: map()
  def limits do
    %{
      input_encoding: "big-endian-u64",
      max_input_bytes: @max_input_bytes,
      max_observed_sequences: @max_observed_sequences,
      max_window_span: @max_window_span,
      max_output_ranges: @max_observed_sequences + 1
    }
  end

  defp validate_window(expected_first, expected_last)
       when not is_integer(expected_first) or not is_integer(expected_last),
       do: error("NATIVE_INVALID_WINDOW", "window_values_must_be_unsigned_u64")

  defp validate_window(expected_first, expected_last)
       when expected_first < 0 or expected_last < 0 or expected_first > @max_u64 or
              expected_last > @max_u64,
       do: error("NATIVE_INVALID_WINDOW", "window_values_must_be_unsigned_u64")

  defp validate_window(expected_first, expected_last) when expected_first > expected_last,
    do: error("NATIVE_INVALID_WINDOW", "expected_first_must_not_exceed_expected_last")

  defp validate_window(expected_first, expected_last)
       when expected_last - expected_first + 1 > @max_window_span,
       do: error("NATIVE_WINDOW_TOO_LARGE", "expected_window_exceeds_declared_span")

  defp validate_window(_expected_first, _expected_last), do: :ok

  defp validate_input(observed_big_endian) when not is_binary(observed_big_endian),
    do: error("NATIVE_INVALID_ENCODING", "observed_sequences_must_be_a_binary")

  defp validate_input(observed_big_endian) when byte_size(observed_big_endian) > @max_input_bytes,
    do: error("NATIVE_INPUT_TOO_LARGE", "observed_binary_exceeds_declared_bytes")

  defp validate_input(observed_big_endian) when rem(byte_size(observed_big_endian), 8) != 0,
    do: error("NATIVE_INVALID_ENCODING", "observed_binary_must_contain_complete_u64_values")

  defp validate_input(_observed_big_endian), do: :ok

  defp do_compact(
         <<>>,
         _expected_first,
         expected_last,
         _previous,
         next_missing,
         missing_ranges,
         observed_count,
         unique_count,
         duplicate_count
       ) do
    final_ranges =
      case next_missing do
        first when is_integer(first) and first <= expected_last ->
          [%{first: first, last: expected_last} | missing_ranges]

        _other ->
          missing_ranges
      end

    {:ok,
     %{
       missing_ranges: Enum.reverse(final_ranges),
       observed_count: observed_count,
       unique_count: unique_count,
       duplicate_count: duplicate_count
     }}
  end

  defp do_compact(
         <<sequence::unsigned-big-integer-size(64), rest::binary>>,
         expected_first,
         expected_last,
         previous,
         next_missing,
         missing_ranges,
         observed_count,
         unique_count,
         duplicate_count
       ) do
    index = observed_count

    cond do
      sequence < expected_first or sequence > expected_last ->
        error("NATIVE_SEQUENCE_OUT_OF_WINDOW", "observed_sequence_is_outside_expected_window")

      is_integer(previous) and sequence < previous ->
        error("NATIVE_SEQUENCE_OUT_OF_ORDER", "observed_sequences_must_be_non_decreasing")

      sequence == previous ->
        do_compact(
          rest,
          expected_first,
          expected_last,
          previous,
          next_missing,
          missing_ranges,
          index + 1,
          unique_count,
          duplicate_count + 1
        )

      true ->
        ranges =
          if is_integer(next_missing) and next_missing < sequence do
            [%{first: next_missing, last: sequence - 1} | missing_ranges]
          else
            missing_ranges
          end

        next = if sequence == @max_u64, do: nil, else: sequence + 1

        do_compact(
          rest,
          expected_first,
          expected_last,
          sequence,
          next,
          ranges,
          index + 1,
          unique_count + 1,
          duplicate_count
        )
    end
  end

  defp error(code, detail), do: {:error, %{code: code, retryable: false, detail: detail}}
end
