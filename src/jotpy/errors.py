class ApiError(Exception):
    def __init__(self, status: int, error: str | None = None, errors: list | None = None) -> None:
        self.status = status
        self.error = error
        self.errors = errors
