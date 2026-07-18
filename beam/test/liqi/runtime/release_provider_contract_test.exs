defmodule Liqi.Runtime.ReleaseProviderContractTest do
  use ExUnit.Case, async: true

  test "release provider commands are direct, versioned, and fail closed" do
    assert File.read!("rel/overlays/bin/liqi-health") == File.read!("beam/scripts/health.sh")
    assert File.read!("rel/overlays/bin/liqi-drain") == File.read!("beam/scripts/drain.sh")

    assert File.exists?("beam/scripts/validate-v1-source.sh")
    assert File.exists?("beam/scripts/run-v1-integration.sh")
    assert File.exists?("beam/scripts/verify-v1-release.sh")
    assert File.exists?("docs/adr/1001-v1-provider-contract-mismatches.md")

    source_gate = File.read!("beam/scripts/validate-v1-source.sh")
    assert source_gate =~ "mix deps.get --locked"
    assert source_gate =~ "shutil.rmtree('_build/test'"
    refute source_gate =~ "deps.clean --all"

    for path <- [
          "contracts/runtime/runtime-source-result-v1.schema.json",
          "contracts/runtime/runtime-integration-result-v1.schema.json",
          "contracts/runtime/runtime-artifact-result-v1.schema.json",
          "beam/release/mix-release-provider-v1.example.json"
        ] do
      assert {:ok, _} = path |> File.read!() |> Jason.decode()
    end

    manifest =
      "beam/release/mix-release-provider-v1.example.json"
      |> File.read!()
      |> Jason.decode!()

    assert manifest["manifest_signature"]["signed_payload"] == "exact-manifest-bytes"
    assert manifest["artifact"]["signature"]["signed_payload"] == "artifact-bytes"
    assert String.starts_with?(manifest["rollback_target_release_id"], "liqi-v0-")
  end
end
