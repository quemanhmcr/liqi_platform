defmodule LiqiPersistence.Readiness do
  @moduledoc "Migration and command-write readiness through the provider function."
  alias LiqiPersistence.{Contract, Query, Repos}

  @sql """
  SELECT ready, write_ready, reason, current_version, expected_version,
         oban_migration_version, expected_oban_migration_version, in_recovery
  FROM platform.database_readiness_v1($1::bigint, $2::integer)
  """

  def check(required_version \\ Contract.required_migration(), repo \\ Repos.command()) do
    Query.one(repo, @sql, [required_version, Contract.required_oban_migration()])
  end
end
