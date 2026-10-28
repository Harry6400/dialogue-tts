"""Shared retry and per-segment deadline policy for speech synthesis."""

DEFAULT_RETRY_ATTEMPTS = 5
MIN_RETRY_ATTEMPTS = 1
MAX_RETRY_ATTEMPTS = 30
BASE_SEGMENT_TIMEOUT_SECONDS = 90
MAX_SEGMENT_TIMEOUT_SECONDS = 300


def validate_retry_attempts(value):
    if type(value) is not int or not MIN_RETRY_ATTEMPTS <= value <= MAX_RETRY_ATTEMPTS:
        raise ValueError('retry attempts must be an integer from 1 to 30')
    return value


def segment_timeout_seconds(attempts):
    validate_retry_attempts(attempts)
    return min(MAX_SEGMENT_TIMEOUT_SECONDS,
               max(BASE_SEGMENT_TIMEOUT_SECONDS, attempts * 10))


def retry_delay_seconds(failed_attempt):
    if type(failed_attempt) is not int or failed_attempt < 1:
        raise ValueError('failed attempt must be a positive integer')
    return min(0.5 * failed_attempt, 2.0)
