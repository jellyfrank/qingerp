from datetime import timedelta
from math import ceil


def retry_delay(attempts, retry_after=None):
    if retry_after is not None:
        return timedelta(seconds=max(1, ceil(retry_after)))
    return timedelta(minutes=min(60, 2 ** attempts))


class DeferredChannelError(Exception):
    """A safe, non-sensitive diagnostic for work that can be resumed."""

    def __init__(self, code, retry_after=None):
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after


class ChannelReviewRequired(DeferredChannelError):
    """An operator decision is needed before processing can continue."""
