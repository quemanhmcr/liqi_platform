defmodule Liqi.Runtime.SecretRefTest do
  use ExUnit.Case, async: false

  setup do
    standard = System.get_env("CREDENTIALS_DIRECTORY")
    compatibility = System.get_env("LIQI_CREDENTIALS_DIRECTORY")

    on_exit(fn ->
      restore("CREDENTIALS_DIRECTORY", standard)
      restore("LIQI_CREDENTIALS_DIRECTORY", compatibility)
    end)

    :ok
  end

  test "standard systemd credential directory wins over the one-window compatibility alias" do
    root = Path.join(System.tmp_dir!(), "liqi-secret-ref-#{System.unique_integer([:positive])}")
    standard = Path.join(root, "standard")
    compatibility = Path.join(root, "compatibility")
    File.mkdir_p!(standard)
    File.mkdir_p!(compatibility)
    File.write!(Path.join(standard, "probe-token"), "standard-value\n")
    File.write!(Path.join(compatibility, "probe-token"), "compatibility-value\n")
    on_exit(fn -> File.rm_rf(root) end)

    System.put_env("CREDENTIALS_DIRECTORY", standard)
    System.put_env("LIQI_CREDENTIALS_DIRECTORY", compatibility)

    assert {:ok, "standard-value"} =
             Liqi.Runtime.SecretRef.resolve_value("systemd-credential://probe-token")
  end

  test "compatibility alias resolves only an already-materialized bounded file" do
    root =
      Path.join(System.tmp_dir!(), "liqi-secret-ref-alias-#{System.unique_integer([:positive])}")

    File.mkdir_p!(root)
    File.write!(Path.join(root, "probe-token"), "alias-value\n")
    on_exit(fn -> File.rm_rf(root) end)

    System.delete_env("CREDENTIALS_DIRECTORY")
    System.put_env("LIQI_CREDENTIALS_DIRECTORY", root)

    assert {:ok, "alias-value"} =
             Liqi.Runtime.SecretRef.resolve_value("systemd-credential://probe-token")

    assert {:error, :vault_reference_not_materialized} =
             Liqi.Runtime.SecretRef.resolve_value("oci-vault://not-materialized")
  end

  defp restore(name, nil), do: System.delete_env(name)
  defp restore(name, value), do: System.put_env(name, value)
end
