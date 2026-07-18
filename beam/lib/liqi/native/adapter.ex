defmodule Liqi.Native.Adapter do
  @moduledoc "Consumer contract for an optional bounded native sequence kernel."

  @callback readiness() :: :ok | {:error, term()}
  @callback sequence_diff([non_neg_integer()], non_neg_integer(), non_neg_integer()) ::
              {:ok, [non_neg_integer()]} | {:error, term()}
end
