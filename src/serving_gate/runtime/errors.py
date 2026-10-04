class APIError(Exception):
    def __init__(self, status, code, **details):
        super().__init__(code)
        self.status = status
        self.code = code
        self.details = details

    def payload(self):
        return {"error": self.code, **self.details}


class WorkerError(Exception):
    def __init__(self, code, retryable=False):
        super().__init__(code)
        self.code = code
        self.retryable = retryable
