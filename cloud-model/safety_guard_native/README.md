# Safety Guard Native Runtime

This directory contains the RK3588 native C++ runtime for cloud-model safety monitoring.

It builds only one shared library:

```text
build/libsafety_rknn.so
```

The Python package `safety_guard` loads this library with `ctypes`.

Build on the RK3588 board:

```bash
cd ~/cloud-model/safety_guard_native
./build_native.sh
```

Runtime inputs:

- `pose_yolov8n_hybrid.rknn`
- `hand_yolov8n_int8.rknn`
- `hazard_yolov8s_coco_int8.rknn`

Model files are deployment artifacts and should not be committed to git.
