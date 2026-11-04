"""Explicit experimental positive control; forbidden in ordinary environments."""


class EnvironmentFaultInjector:
    def __init__(self, *, is_fault_probe=False, fault=None):
        if fault is not None and not is_fault_probe:
            raise RuntimeError("Fault injection is forbidden in ordinary gameplay")
        if fault not in (None, "unresponsive_use_trigger"):
            raise ValueError("Unknown environment fault")
        self.fault = fault

    def apply(self, vector, use_index, log, *, decision_idx, tic):
        output = list(vector)
        if self.fault == "unresponsive_use_trigger" and output[use_index]:
            output[use_index] = 0
            log(
                "env_fault_blocked_use",
                decision_idx=decision_idx,
                tic=tic,
                fault=self.fault,
                requested_use=True,
                applied_use=False,
            )
        return output
