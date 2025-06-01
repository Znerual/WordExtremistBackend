import logging
import json
import datetime as dt
from typing import Dict, Any, Optional, Set

# Attributes from LogRecord that are often included by default or are special
LOG_RECORD_BUILTIN_ATTRS: Set[str] = {
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "module", "msecs",
    "message", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "thread", "threadName",
}

class MyJSONFormatter(logging.Formatter):
    def __init__(self, *, fmt_keys: Optional[Dict[str, str]] = None, datefmt: Optional[str] = None):
        super().__init__(datefmt=datefmt) # Pass datefmt to parent
        self.fmt_keys = fmt_keys if fmt_keys is not None else {}

    def format(self, record: logging.LogRecord) -> str:
        message = self._prepare_log_dict(record)
        return json.dumps(message, default=str)

    def _prepare_log_dict(self, record: logging.LogRecord) -> Dict[str, Any]:
        always_fields = {}
        # Use record.getMessage() to get the formatted message string
        # This handles cases where record.msg is a format string and record.args are present
        always_fields["message"] = record.getMessage()
        
        # Format timestamp using the datefmt provided to __init__ (or default if None)
        # The 'created' attribute is a Unix timestamp (seconds since epoch)
        # The 'asctime' is already formatted by logging.Formatter.formatTime if datefmt is set.
        # We can re-format it here to ensure UTC and ISO format if we want strict control.
        # If self.datefmt is set, formatTime is called by the parent Formatter.
        # Let's get the formatted time via formatTime
        if self.datefmt:
             always_fields["timestamp"] = self.formatTime(record, self.datefmt)
        else: # Default to ISO format in UTC if no datefmt
             always_fields["timestamp"] = dt.datetime.fromtimestamp(
                record.created, tz=dt.timezone.utc
            ).isoformat()


        if record.exc_info:
            always_fields["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            always_fields["stack_info"] = self.formatStack(record.stack_info)

        message_dict = {}
        for key, val_key_in_record in self.fmt_keys.items():
            val = getattr(record, val_key_in_record, None)
            # Special handling for message if it's one of the fmt_keys, use already_fields version
            if val_key_in_record == "message" and "message" in always_fields:
                message_dict[key] = always_fields["message"]
            elif val_key_in_record == "timestamp" and "timestamp" in always_fields: # Use our formatted timestamp
                message_dict[key] = always_fields["timestamp"]
            elif val is not None:
                 message_dict[key] = val
        
        # Add any fields from always_fields that weren't explicitly in fmt_keys
        # (e.g., if fmt_keys only asked for 'level' and 'logger', but we always want 'message' and 'timestamp')
        for key, value in always_fields.items():
            if key not in message_dict.values(): # Check if the *value* (original LogRecord attr name) is already mapped
                # Try to find if the key itself exists in message_dict to avoid overwriting a custom key
                if key not in message_dict:
                    message_dict[key] = value


        # Add extra fields that are not part of standard LogRecord attributes
        for key, val in record.__dict__.items():
            if key not in LOG_RECORD_BUILTIN_ATTRS and key not in message_dict and key not in self.fmt_keys.values():
                message_dict[key] = val
        
        return message_dict