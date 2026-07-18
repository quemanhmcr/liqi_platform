defmodule LiqiPersistence.Contract do
  @moduledoc "Published V1 persistence compatibility constants."
  @required_migration 8
  @required_oban_migration 14
  def required_migration, do: @required_migration
  def required_oban_migration, do: @required_oban_migration
end
