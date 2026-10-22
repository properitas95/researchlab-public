"""Selftest scaffolding shared by the stage modules."""


def fake(d, script, name="fake.py"):
    """Write a fake model provider script into folder d; return the cmd: provider string that runs it."""
    (d / name).write_text(script)
    return f"cmd:python3 {d / name}"
