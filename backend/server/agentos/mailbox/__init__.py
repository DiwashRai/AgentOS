"""Mailbox mode (`design/mailbox.md`): launch runs through a store, watch the Actions run, replay
the worker's recorded API calls. `dispatcher.tick` and `reaper.MailboxReaper` take the
file-report runs whenever a mailbox store is configured (`Settings.mailbox_store`)."""
