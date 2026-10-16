class TradingError(Exception):
    """Base error for operationally meaningful trading failures."""


class ConfigurationError(TradingError):
    pass


class InsufficientHistory(TradingError):
    """The instrument is too newly listed to evaluate.

    Expected and routine: exchanges list new tokens constantly and they surface high in a
    volume-ranked universe. It is not a screening failure, so it must not be logged as one --
    warning-level noise on every scan trains an operator to ignore the log.
    """


class VenueError(TradingError):
    pass


class RateLimitError(VenueError):
    def __init__(self, message: str, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class UnknownExecutionStatus(VenueError):
    """The venue may have accepted an order; reconcile before any retry."""


class OrderRejected(VenueError):
    pass


class SafetyInterlockError(TradingError):
    """Trading must stop until an operator reconciles state.

    Raising this means the system cannot prove its own consistency. It is not the right
    error for an ordinary decision not to trade -- see :class:`TradeRejected`.
    """


class TradeRejected(TradingError):
    """This candidate will not be traded, and nothing is wrong.

    An IOC entry that does not fill, a risk gate that declines, or a signal that expires
    before submission are all *expected* outcomes of a conservative system. They leave no
    venue state behind, so the cycle should move to the next ranked candidate.

    Conflating these with :class:`SafetyInterlockError` is what let three unfilled IOC
    limits in a row trip the durable kill switch.
    """
