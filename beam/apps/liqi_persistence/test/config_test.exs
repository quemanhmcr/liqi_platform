defmodule LiqiPersistence.ConfigTest do
  use ExUnit.Case, async: false

  test "published pool sizes fit the provider contract" do
    assert %{command: 12, realtime: 4, worker: 6} = LiqiPersistence.Config.pool_sizes()
    assert Enum.sum(Map.values(LiqiPersistence.Config.pool_sizes())) == 22
  end

  test "isolated recovery probe can select a Unix socket without changing pool policy" do
    path = Path.join(System.tmp_dir!(), "liqi-config-test-#{System.unique_integer([:positive])}")
    File.write!(path, "TEST_ONLY_database_password")
    previous_socket = System.get_env("LIQI_DATABASE_SOCKET_DIR")
    previous_password = System.get_env("LIQI_DATABASE_API_PASSWORD_FILE")

    on_exit(fn ->
      if previous_socket, do: System.put_env("LIQI_DATABASE_SOCKET_DIR", previous_socket), else: System.delete_env("LIQI_DATABASE_SOCKET_DIR")
      if previous_password, do: System.put_env("LIQI_DATABASE_API_PASSWORD_FILE", previous_password), else: System.delete_env("LIQI_DATABASE_API_PASSWORD_FILE")
      File.rm(path)
    end)

    System.put_env("LIQI_DATABASE_SOCKET_DIR", "/run/liqi/restore/test")
    System.put_env("LIQI_DATABASE_API_PASSWORD_FILE", path)
    options = LiqiPersistence.Config.repo_options(:command)

    assert options[:socket_dir] == "/run/liqi/restore/test"
    refute Keyword.has_key?(options, :hostname)
    assert options[:username] == "liqi_api"
    assert options[:pool_size] == 12
  end

end
