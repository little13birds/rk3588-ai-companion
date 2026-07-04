# Public Release Notes

This repository is a public GitHub delivery snapshot that combines the latest local mainline snapshots of `cloud-model` and `ros2`.

## What Was Included

- `cloud-model/`: Python voice Agent, dashboard, runtime scheduler, reading mode, safety integration, tests, docs, and frontend assets.
- `ros2/`: ROS2 packages, launch files, scripts, robot control code, perception code, and package manifests.

## What Was Changed for Public Safety

- Real cloud API keys were removed from the release snapshot.
- `cloud-model/config.py` now reads `DASHSCOPE_API_KEY` and `DASHSCOPE_TTS_API_KEY` from environment variables.
- Example VLM scripts now read `DASHSCOPE_API_KEY` from the environment.
- Large binary assets over the release threshold were omitted and listed in `OMITTED_LARGE_ASSETS.txt`.
- Broken Orbbec SDK library symlinks that pointed to omitted binaries were removed.

## What Was Not Changed

- Source repositories under `/home/tao/Desktop/claude/` were not modified.
- Board remotes were not changed.
- ROS2 package code was not rewritten for public release.
- The root project license was not added yet.

## Follow-Up Before Wider Public Use

- Decide the final root project license.
- Review packages with `TODO: License declaration`, `all copyrights reserved`, GPL, and non-commercial Creative Commons licenses.
- Decide whether omitted large assets should be distributed through Git LFS, GitHub Releases, external vendor downloads, or not redistributed.
- Decide how to publish PCB and 3D modeling files after checking third-party IP and export constraints.

## Known Import Quality Notes

- `git diff --check` reports many trailing-whitespace warnings in imported ROS/vendor files and historical planning Markdown. These were preserved from the source snapshots and were not mass-formatted during the public import to avoid changing behavior or vendor files.
