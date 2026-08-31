"""Scheduled tracking and rule-trigger boundary."""

__all__ = ["SourceStatus", "TrackingRunResult", "TrackingService"]


def __getattr__(name: str):
    if name in __all__:
        from .service import SourceStatus, TrackingRunResult, TrackingService

        return {"SourceStatus": SourceStatus, "TrackingRunResult": TrackingRunResult, "TrackingService": TrackingService}[name]
    raise AttributeError(name)
