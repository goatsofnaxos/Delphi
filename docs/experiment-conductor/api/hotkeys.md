# hotkeys

!!! warning "Removed"
    Hotkey support has been removed from the refactored conductor.

The conductor now runs headlessly on a shared network drive and no longer
listens for global keyboard shortcuts.  Graceful shutdown is handled via
`SIGINT` / `SIGTERM` (e.g. `Ctrl+C` in the terminal where the conductor is
running, or `kill <pid>`).
