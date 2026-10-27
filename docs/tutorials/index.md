# Tutorials

Step-by-step guides for using each package in a typical Delphi chronic-recording session.

For a standard **delphi_pirouette** session, the packages are used in this order:

```
1. launcher            ← start Bonsai workflows
2. experiment-conductor  ← manages all of the below automatically
     ├── delphi-data      ← ingest + visualise behavioral data
     └── metadata-generator ← write AIND metadata JSON files
```

If you use the **experiment-conductor**, you do not need to run `delphi-data` or `metadata-generator` manually — the conductor calls them for you.  The individual tutorials are useful for one-off post-hoc processing or debugging.

---

| Tutorial | When to read it |
|----------|----------------|
| [Launcher](launcher.md) | You want to start Bonsai interactively or replay a saved session profile |
| [delphi-data](delphi-data.md) | Post-hoc processing of an already-recorded session, or debugging a pipeline step |
| [metadata-generator](metadata-generator.md) | Regenerating or inspecting AIND metadata for a session |
| [experiment-conductor](experiment-conductor.md) | Running a full supervised live session end-to-end |
