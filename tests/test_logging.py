import logging

from lexflow.logging import REQUIRED_FIELDS, get_logger, log_event


def test_log_event_fills_required_fields():
    logger = get_logger("lexflow.test.logging")
    captured: list[dict] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(dict(getattr(record, "fields", {})))

    handler = Capture()
    logger.addHandler(handler)
    try:
        log_event(
            logger,
            "node.done",
            matter_id="matter-test",
            node="parse",
            model="parser",
            tokens=0,
            latency=7,
            status="ok",
        )
    finally:
        logger.removeHandler(handler)

    fields = captured[-1]
    assert set(REQUIRED_FIELDS) <= set(fields)
    assert fields["matter_id"] == "matter-test"
    assert fields["device"] is None
