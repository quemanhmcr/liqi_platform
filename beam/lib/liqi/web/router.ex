defmodule Liqi.Web.Router do
  use Phoenix.Router

  pipeline :api do
    plug(:accepts, ["json"])
  end

  scope "/health", Liqi.Web do
    pipe_through(:api)
    get("/live", HealthController, :live)
    get("/ready", HealthController, :ready)
  end

  scope "/platform/v1", Liqi.Web do
    pipe_through(:api)
    get("/metadata", MetadataController, :show)
    get("/metrics", MetricsController, :show)
    post("/probes", ProbeController, :create)
    post("/probes/native", NativeProbeController, :create)
  end
end
