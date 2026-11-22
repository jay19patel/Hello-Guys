class HelloGuyError(Exception):
    """Base error for hello_guy_ai."""


class AssistantMisconfigured(HelloGuyError):
    pass


class AssistantNotFound(HelloGuyError):
    pass


class NotAllowed(HelloGuyError):
    pass


class ThreadBusy(HelloGuyError):
    """A run is already queued/running on this thread."""


class RunConflict(HelloGuyError):
    """The run is not in a state that allows this action."""
