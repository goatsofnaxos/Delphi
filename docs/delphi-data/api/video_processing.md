# video_processing

Poke-triggered video clip extraction for Delphi behavioral sessions.

!!! note "System requirements"
    `ffmpeg` and `ffprobe` must be on `PATH`.

!!! note "Python requirements"
    ```bash
    pip install delphi-data[video]
    ```

---

## Session-level pipeline

::: delphi_data.video_processing.process_session

::: delphi_data.video_processing.process_chunk

---

## Clip export

::: delphi_data.video_processing.export_clip

::: delphi_data.video_processing.relaxed_trigger_clip

---

## Session discovery

::: delphi_data.video_processing.find_session_dirs

::: delphi_data.video_processing.get_subject_id

::: delphi_data.video_processing.get_chunk_timestamps

::: delphi_data.video_processing.load_camera

---

## Chunk management

::: delphi_data.video_processing.delete_port_chunk

---

## CLI entry point

::: delphi_data.video_processing.main

---

## Constants

::: delphi_data.video_processing.HALF_WINDOW

::: delphi_data.video_processing.DELETE_BUFFER_HRS
