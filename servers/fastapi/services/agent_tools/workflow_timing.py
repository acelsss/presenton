"""Bounded phase timings with no request/response bodies or credentials."""
import logging
from functools import wraps
from time import perf_counter

LOGGER = logging.getLogger("presenton.workflow.timing")


def timed_stage(function):
    @wraps(function)
    async def wrapped(*args, **kwargs):
        started = perf_counter()
        result = {}
        error_type = "-"
        try:
            result = await function(*args, **kwargs)
            return result
        except BaseException as exc:
            error_type = type(exc).__name__
            raise
        finally:
            output = result if isinstance(result, dict) else {}
            LOGGER.info("[ppt.phase] phase=%s status=%s duration_ms=%.2f committed=%d error_type=%s",
                        function.__name__, output.get("status", "error" if error_type != "-" else "ok"),
                        (perf_counter() - started) * 1000, len(output.get("committed", [])), error_type)
    return wrapped
