# Telemetry degraded V0

1. Confirm user-facing health and SLO symptoms independently; telemetry loss is not proof of application failure.
2. Check collector memory limiter refusals, exporter queue saturation and bounded retry exhaustion.
3. Preserve journald evidence within the 2 GiB/7-day local retention window.
4. Do not increase queue size, retry duration or journal disk budget during an incident without a capacity decision note.
5. If required release-ID, recovery or security signals are absent, block promotion.
6. Restore the sink or collector source configuration; do not copy credentials into files or command history.
7. Record dropped-signal duration and affected dashboards/alerts in the incident timeline.
