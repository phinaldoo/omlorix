from app.telemetry.bootstrap import bootstrap_telemetry


if __name__ == "__main__":
    bootstrap_telemetry()
    try:
        from app.automations.worker import run_automation_scheduler_forever

        run_automation_scheduler_forever()
    finally:
        from app.telemetry import shutdown_telemetry

        shutdown_telemetry()
