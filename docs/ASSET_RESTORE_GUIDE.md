# Asset Restore Guide

The initial public repository excludes large or potentially restricted binary assets. This keeps the GitHub repository lightweight and avoids accidental redistribution of files whose licenses need separate review.

## Omitted Files

See `OMITTED_LARGE_ASSETS.txt` for the exact omitted paths.

Current omitted categories:

- Orbbec SDK shared libraries.
- ORB-SLAM vocabulary file.
- dlib 68-point face-landmark model.

## Restoring For Local Board Development

For internal development, restore omitted files from the private board backup or vendor/source package into the same relative paths listed in `OMITTED_LARGE_ASSETS.txt`.

Do not commit restored binary assets directly to this public repository unless the team has confirmed:

1. the redistribution license permits public hosting;
2. the file size fits GitHub limits or Git LFS is configured;
3. the asset is necessary for source-level reproduction.

## Future Hardware Assets

PCB and 3D modeling materials should be placed under:

- `hardware/pcb/`
- `hardware/3d-models/`

Before committing hardware files, check whether they contain manufacturer account data, private notes, absolute local paths, or third-party model restrictions.
