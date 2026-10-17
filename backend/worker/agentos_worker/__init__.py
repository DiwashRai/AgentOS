"""The AgentOS worker's package (Python >= 3.11 standard library only, enforced by a test).

`archive` builds what the server serves as worker.py: one zip of the entry script
(`backend/worker/worker.py`) and this package, which `python worker.py` runs as is.
"""
