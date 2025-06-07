# app/core/db_logging_handler.py
import logging
import traceback
from app.db.session import SessionLocal
from app.crud import crud_system

class DatabaseHandler(logging.Handler):
    """
    A custom logging handler that writes log records to the database.
    """
    def __init__(self, level=logging.NOTSET):
        super().__init__(level)
        self.db_session = None

    def emit(self, record: logging.LogRecord):
        """
        This method is called for every log record.
        """
        # We only want to log ERROR and CRITICAL to the DB alerts
        if record.levelno < logging.ERROR:
            return

        # Get a rich traceback if there's an exception
        details = None
        if record.exc_info:
            details = "".join(traceback.format_exception(*record.exc_info))
        
        # Use a new session for each log to ensure thread safety
        # and prevent issues with sessions from the main app threads.
        db = SessionLocal()
        try:
            crud_system.create_alert(
                db=db,
                level=record.levelname,
                message=record.getMessage(), # Formats the message with its arguments
                details=details
            )
        except Exception as e:
            # If we can't write to the DB, we're in big trouble.
            # Log this failure to the console/file as a last resort.
            print(f"--- CRITICAL: FAILED TO WRITE LOG TO DATABASE ---")
            print(f"Original Log: [{record.levelname}] {record.getMessage()}")
            print(f"Database Error: {e}")
            print("--- END CRITICAL ---")
        finally:
            db.close()