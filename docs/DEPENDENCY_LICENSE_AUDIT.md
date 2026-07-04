# Dependency And License Audit

This file records the public-release license scan performed before the initial GitHub upload.

## Scope

Scanned local files:

- ROS2 `package.xml` manifests.
- Included `LICENSE` files.
- Python `requirements.txt` files where present.
- Frontend `package.json` / `pnpm-lock.yaml` presence.
- Obvious cloud API key and private-key string patterns.

This is an engineering audit, not legal advice.

## Project Root License

No root license is declared in this release. Do not add a root `LICENSE` until the team decides how to handle third-party ROS packages, vendor SDKs, model files, PCB files, and 3D assets.

## Notable License Findings

### Permissive Or Common Open-Source Declarations

The following package manifests declare permissive or common open-source licenses:

- `depth_camera_perception`: MIT
- `face_track`: MIT
- `map_preprocess`: Apache-2.0
- `moveit_servo`: BSD 3-Clause
- `roarm_description`: MIT
- `roarm_moveit`: BSD
- `roarm_moveit_cmd`: BSD
- `roarm_moveit_ikfast_plugins`: BSD
- `ros2web`: Apache License 2.0
- `ros2web_interfaces`: Apache License 2.0
- `ros2web_app`: Apache License 2.0
- `rplidar_ros`: BSD
- `simple_nav`: Apache-2.0
- `yahboomcar_arm`: MIT

### Strong Copyleft Or Restricted Declarations

These components need extra attention before choosing a root license or redistributing binaries:

- `rf2o_laser_odometry`: GPL v3
- `openslam_gmapping`: CreativeCommons-by-nc-sa-2.0
- `slam_gmapping`: CreativeCommons-by-nc-sa-2.0

The Creative Commons non-commercial license can be incompatible with commercial redistribution. GPLv3 has source-distribution obligations for derivative redistribution.

### Incomplete Or Restrictive Declarations

The following manifests contain incomplete or restrictive declarations and need manual review:

- `orbbec_camera`: `TODO: License declaration`
- `orbbec_camera_msgs`: `all copyrights reserved`
- `orbbec_description`: `TODO: License declaration`
- `launch_api`: `TODO: License declaration`
- `roarm_driver`: `TODO: License declaration`
- `roarm_web_app`: `TODO: License declaration`
- `ros2web_example_cpp`: `TODO: License declaration`
- `ros2web_example_py`: `TODO: License declaration`
- `ros2web_widgets`: `TODO: License declaration`
- multiple `yahboomcar_*` packages: `TODO: License declaration`

These packages should not be treated as cleanly relicensable project-owned code until their upstream origin and license terms are confirmed.

## Omitted Large Assets

Large binary assets and model files were omitted from the initial public snapshot. See:

- `docs/OMITTED_LARGE_ASSETS.txt`

The omitted files include Orbbec SDK shared libraries, ORB vocabulary data, and a dlib face-landmark model. Redistribution and GitHub storage strategy should be reviewed separately.

## Python And Frontend Dependencies

Python dependencies are mostly imported directly in project code or declared in ROS package manifests. Public release users should inspect import usage and board setup scripts before installing.

Frontend dependency lock files exist under `ros2/src/ros2web_app/web_app/`. Their transitive licenses were not expanded in this audit; run a frontend license scanner before using this package in a production or commercial release.

## Secret Scan Result

The release snapshot was sanitized to remove the previously hardcoded DashScope/OpenAI-compatible API key. Runtime keys must be provided by environment variables:

- `DASHSCOPE_API_KEY`
- `DASHSCOPE_TTS_API_KEY`

Before every public push, rerun a secret scan for:

- `sk-`
- `api_key`
- `secret`
- `password`
- `token`
- PEM/SSH private-key headers

## Recommendation

For the first GitHub upload:

1. Keep the repository public only if no private keys, API keys, personal logs, and restricted binary assets are present.
2. Do not add a root license yet.
3. Add a root license only after deciding whether third-party ROS packages and vendor SDK materials are included, excluded, or moved to external downloads.
