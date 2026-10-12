"""Mailbox mode (`design/mailbox.md`): launch runs through a store, watch the Actions run, replay
the worker's recorded API calls. `dispatcher.tick` and `reaper.MailboxReaper` replace the runs
dispatcher (and skip the runs reaper's heartbeat rules) when AGENTOS_LAUNCHER=mailbox."""
