defmodule Liqi.Web.ProbeAuthTest do
  use ExUnit.Case, async: false
  import Phoenix.ConnTest
  import Plug.Conn, only: [put_req_header: 3]

  @endpoint Liqi.Web.Endpoint

  test "custom materialized credential directory is a one-window systemd alias" do
    directory =
      Path.join(System.tmp_dir!(), "liqi-credentials-#{System.unique_integer([:positive])}")

    File.mkdir_p!(directory)
    File.write!(Path.join(directory, "probe-token"), "materialized-token\n")
    old_standard = System.get_env("CREDENTIALS_DIRECTORY")
    old_alias = System.get_env("LIQI_CREDENTIALS_DIRECTORY")
    System.delete_env("CREDENTIALS_DIRECTORY")
    System.put_env("LIQI_CREDENTIALS_DIRECTORY", directory)

    on_exit(fn ->
      restore_env("CREDENTIALS_DIRECTORY", old_standard)
      restore_env("LIQI_CREDENTIALS_DIRECTORY", old_alias)
      File.rm_rf!(directory)
    end)

    assert {:ok, "materialized-token"} =
             Liqi.Runtime.SecretRef.resolve_value("systemd-credential://probe-token")
  end

  test "HTTP platform probe fails closed without the scoped token" do
    conn = post(build_conn(), "/platform/v1/probes", %{"clientProbeId" => Liqi.Runtime.Id.uuid4()})
    assert %{"error" => %{"code" => "auth.unauthorized"}} = json_response(conn, 401)
  end

  test "WebSocket connect fails closed and accepts the token only through headers" do
    params = %{
      "protocolVersion" => "1",
      "sessionId" => Liqi.Runtime.Id.uuid4(),
      "deviceId" => Liqi.Runtime.Id.uuid4()
    }

    assert :error = Liqi.Web.Socket.connect(params, %Phoenix.Socket{}, %{x_headers: []})

    socket = %Phoenix.Socket{}

    assert {:ok, %Phoenix.Socket{}} =
             Liqi.Web.Socket.connect(params, socket, %{
               x_headers: [{"x-liqi-probe-token", "liqi-test-probe-token"}]
             })
  end

  test "native diagnostic rejects out-of-order and oversized windows before admission" do
    for payload <- [
          %{
            "expectedFirst" => 1,
            "expectedLast" => 8,
            "observedSequences" => [2, 1]
          },
          %{
            "expectedFirst" => 1,
            "expectedLast" => 65_537,
            "observedSequences" => []
          }
        ] do
      conn =
        build_conn()
        |> put_req_header("x-liqi-probe-token", "liqi-test-probe-token")
        |> post("/platform/v1/probes/native", payload)

      assert %{"error" => %{"code" => "validation.failed"}} = json_response(conn, 400)
    end
  end

  test "bounded native diagnostic proves configured/reference parity" do
    conn =
      build_conn()
      |> put_req_header("x-liqi-probe-token", "liqi-test-probe-token")
      |> post("/platform/v1/probes/native", %{
        "expectedFirst" => 1,
        "expectedLast" => 8,
        "observedSequences" => [1, 2, 5, 5, 8]
      })

    response = json_response(conn, 200)
    assert response["parity"]
    assert response["kernel"] == "compact_sequence_diff"
    assert response["configured"]["result"] == response["reference"]["result"]
    assert response["fallbackExercise"]["parity"]
    assert response["fallbackExercise"]["implementation"] == "reference"
    assert response["fallbackExercise"]["fallback"]
    assert response["fallbackExercise"]["fallbackReason"] == "NATIVE_UNAVAILABLE"
  end

  test "probe authentication is explicit in HTTP and realtime contracts" do
    openapi = File.read!("contracts/openapi/platform-v1.yaml")
    realtime = File.read!("contracts/realtime/gateway-v1.schema.json")

    assert openapi =~ "x-liqi-probe-token"
    assert openapi =~ "/platform/v1/probes/native"
    assert realtime =~ "x-liqi-probe-token"
    assert realtime =~ "queryParametersForbidden"
  end

  test "probe observation is authorized and least privilege" do
    probe_id = Liqi.Runtime.Id.uuid4()
    event_id = Liqi.Runtime.Id.uuid4()

    unauthorized = get(build_conn(), "/platform/v1/probes/#{probe_id}?eventId=#{event_id}")
    assert %{"error" => %{"code" => "auth.unauthorized"}} = json_response(unauthorized, 401)

    authorized =
      build_conn()
      |> put_req_header("x-liqi-probe-token", "liqi-test-probe-token")
      |> get("/platform/v1/probes/#{probe_id}?eventId=#{event_id}")

    assert %{"error" => %{"code" => "probe.not_found"}} = json_response(authorized, 404)
  end

  defp restore_env(name, nil), do: System.delete_env(name)
  defp restore_env(name, value), do: System.put_env(name, value)
end
