defmodule LiqiPersistence.RestoreProbe do
  @moduledoc "Read-only BEAM verification against an isolated restored PostgreSQL cluster."

  alias LiqiPersistence.{CommandRepo, Probe, Readiness}

  @spec run!(Path.t()) :: :ok
  def run!(output_path) when is_binary(output_path) do
    git_sha = System.fetch_env!("LIQI_SOURCE_GIT_SHA")
    release_id = System.fetch_env!("LIQI_RELEASE_ID")
    probe_id = System.fetch_env!("LIQI_RESTORE_PROBE_ID")
    event_id = System.fetch_env!("LIQI_RESTORE_PROBE_EVENT_ID")
    required_migration = 8

    unless Regex.match?(~r/^[0-9a-f]{40}$/, git_sha), do: raise("invalid source Git SHA")
    unless Regex.match?(~r/^liqi-v1-[a-z0-9][a-z0-9._-]{2,95}$/, release_id), do: raise("invalid release ID")

    {:ok, _} = Application.ensure_all_started(:ecto_sql)
    {:ok, repo} = CommandRepo.start_link([])

    try do
      {:ok, readiness} = Readiness.check(required_migration, CommandRepo)
      {:ok, observation} = Probe.observe(probe_id, event_id)

      passed =
        readiness["ready"] == true and readiness["write_ready"] == true and
          readiness["current_version"] == required_migration and
          readiness["expected_version"] == required_migration and readiness["in_recovery"] == false and
          observation["probe_status"] == "completed" and observation["outbox_state"] == "succeeded" and
          observation["effect_applied"] == true and observation["terminal"] == true

      result = %{
        schema_version: "liqi.database.restore-beam-probe/v1",
        git_sha: git_sha,
        release_id: release_id,
        migration_version: readiness["current_version"],
        database_ready: readiness["ready"],
        write_ready: readiness["write_ready"],
        in_recovery: readiness["in_recovery"],
        probe_id: observation["probe_id"],
        event_id: observation["event_id"],
        probe_status: observation["probe_status"],
        outbox_state: observation["outbox_state"],
        effect_applied: observation["effect_applied"],
        terminal: observation["terminal"],
        status: if(passed, do: "passed", else: "failed"),
        observed_at: DateTime.utc_now() |> DateTime.to_iso8601()
      }

      encoded = Jason.encode_to_iodata!(result, pretty: true)
      output = Path.expand(output_path)
      File.mkdir_p!(Path.dirname(output))
      temporary = output <> ".tmp-" <> Integer.to_string(System.unique_integer([:positive]))
      File.write!(temporary, [encoded, "\n"], [:binary, :sync])
      File.rename!(temporary, output)

      unless passed, do: raise("isolated BEAM restore probe failed")
      :ok
    after
      GenServer.stop(repo, :normal, 5_000)
    end
  end
end
