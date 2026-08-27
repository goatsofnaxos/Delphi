# watcher

Network-drive session discovery.

Scans configured watch paths for acquisition session directories using a
simple directory-structure heuristic: a session directory is a
`YYYY-MM-DDTHH-MM-SS`-named directory that contains at least one
run sub-directory with a `behavior/` folder.

::: experiment_conductor.watcher
