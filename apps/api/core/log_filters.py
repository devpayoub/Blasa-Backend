import contextvars
import logging
import re

# The current request's id, shared with log formatters via RequestIDFilter and
# visible to Celery workers that run handlers inside the request flow.
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class RequestIDFilter(logging.Filter):
    """Injects the ambient request id into every LogRecord."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class RedactTokenFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        record.msg = re.sub(r"([?&]token=)[^&\s\"']+", r"\1[redacted]", message)
        record.args = ()
        return True
