defmodule Liqi.Runtime.ReleaseProviderContractTest do
  use ExUnit.Case, async: true

  test "release provider commands are direct, versioned, and fail closed" do
    assert File.read!("rel/overlays/bin/liqi-health") == File.read!("beam/scripts/health.sh")
    assert File.read!("rel/overlays/bin/liqi-drain") == File.read!("beam/scripts/drain.sh")

    assert File.exists?("beam/scripts/validate-v1-source.sh")
    assert File.exists?("beam/scripts/run-v1-integration.sh")
    refute File.exists?("beam/scripts/prepare_disposable_database.py")
    assert File.exists?("beam/scripts/run_v1_integration.py")
    assert File.exists?("beam/scripts/verify-v1-release.sh")
    assert File.exists?("beam/scripts/build_linux_release.py")
    assert File.exists?("beam/release/mix-release-provider-v1.e5-temporary.example.json")
    assert File.exists?("docs/adr/1001-v1-provider-contract-mismatches.md")

    release_builder = File.read!("beam/scripts/build_linux_release.py")
    assert release_builder =~ "release build requires a clean exact-SHA worktree"
    assert release_builder =~ "native handoff target does not match Mix release target"
    assert release_builder =~ "artifact and manifest signing key IDs must be distinct"
    assert release_builder =~ "self-verification did not pass"
    assert release_builder =~ "os.replace(staged_output, final_output)"

    source_gate = File.read!("beam/scripts/validate-v1-source.sh")
    assert source_gate =~ "mix deps.get --locked"
    assert source_gate =~ "LIQI_SOURCE_MIX_BUILD_PATH"
    assert source_gate =~ "export MIX_BUILD_PATH"
    assert source_gate =~ "shutil.rmtree(sys.argv[1]"
    refute source_gate =~ "deps.clean --all"
    refute source_gate =~ "mix deps.compile"
    refute source_gate =~ "mix deps.get\n"

    for path <- [
          "contracts/runtime/runtime-source-result-v1.schema.json",
          "contracts/runtime/runtime-integration-result-v1.schema.json",
          "contracts/runtime/runtime-artifact-result-v1.schema.json",
          "contracts/runtime/linux-release-build-result-v1.schema.json",
          "contracts/runtime/linux-release-build-result-v1.example.json",
          "beam/release/mix-release-provider-v1.example.json",
          "beam/release/mix-release-provider-v1.e5-temporary.example.json"
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
