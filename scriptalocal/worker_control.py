"""Cancellation/lease checkpoints shared without importing queue or GPU libraries."""
class ProcessingInterrupted(Exception):
    def __init__(self,reason):super().__init__(reason);self.reason=reason
