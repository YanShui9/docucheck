from app.celery_app import app


def main() -> None:
    responses = app.control.ping(destination=["docucheck@worker"], timeout=3)
    if not any(response.get("docucheck@worker", {}).get("ok") == "pong" for response in responses):
        raise SystemExit("Celery worker did not respond to ping")


if __name__ == "__main__":
    main()
