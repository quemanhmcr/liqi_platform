defmodule Liqi.Runtime.ActiveEntity do
  @moduledoc "Minimal seam for rebuildable active entities; it is not a generic domain framework."

  @callback actor_key(init_arg :: term()) :: String.t()
  @callback rebuild(init_arg :: term()) :: {:ok, term()} | {:error, term()}
end
