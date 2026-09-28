"""Explicit target contract, independent of GPU libraries."""


def target_probability(label):
    if label is None:
        return .5
    if type(label) is bool:
        return float(label)
    raise ValueError("Intent targets must be Boolean or unknown")
