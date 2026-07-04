#ifndef SAFETY_GUARD_RKNN_LIB_H_
#define SAFETY_GUARD_RKNN_LIB_H_

#ifdef __cplusplus
extern "C" {
#endif

void* safety_rknn_create(const char* pose_path, const char* hand_path, const char* hazard_path);
void safety_rknn_destroy(void* handle);

int safety_rknn_set_config(void* handle,
                           float pose_conf,
                           float hand_conf,
                           float hazard_conf,
                           float relation_min_px,
                           float relation_diag_ratio,
                           float overlay_scale,
                           int show_top_status,
                           int jpeg_quality,
                           int target_w,
                           int target_h);

int safety_rknn_process_bgr(void* handle,
                            const unsigned char* bgr,
                            int width,
                            int height,
                            int stride,
                            double now_sec,
                            int run_hazard,
                            unsigned char* jpg_buf,
                            int jpg_cap,
                            int* jpg_size,
                            char* json_buf,
                            int json_cap);

const char* safety_rknn_last_error(void* handle);

#ifdef __cplusplus
}
#endif

#endif
