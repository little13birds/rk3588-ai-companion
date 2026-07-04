#ifndef SAFETY_GUARD_RKNN_YOLO_H_
#define SAFETY_GUARD_RKNN_YOLO_H_

#include <string>
#include <vector>

#include <opencv2/core.hpp>
#include "rknn_api.h"

struct Keypoint {
    float x = 0.0f;
    float y = 0.0f;
    float conf = 0.0f;
};

struct Detection {
    float x1 = 0.0f;
    float y1 = 0.0f;
    float x2 = 0.0f;
    float y2 = 0.0f;
    float conf = 0.0f;
    int class_id = 0;
    int track_id = -1;
    std::vector<Keypoint> kpts;
};

struct YoloConfig {
    std::string name;
    int num_classes = 1;
    bool is_pose = false;
    int num_keypoints = 0;
    float conf_thresh = 0.25f;
    float nms_thresh = 0.45f;
    int max_det = 64;
    std::vector<int> class_filter;
};

class RknnYolo {
public:
    explicit RknnYolo(YoloConfig cfg);
    ~RknnYolo();

    bool init(const std::string& model_path);
    void release();
    bool infer(const cv::Mat& bgr, std::vector<Detection>& detections, float* infer_ms);

    void set_conf_thresh(float value);
    void set_nms_thresh(float value);
    void set_max_det(int value);

    int model_w() const { return model_w_; }
    int model_h() const { return model_h_; }
    const YoloConfig& config() const { return cfg_; }

private:
    struct LetterBox {
        float scale = 1.0f;
        int x_pad = 0;
        int y_pad = 0;
    };

    void letterbox_rgb(const cv::Mat& bgr, unsigned char* out, LetterBox* lb) const;
    void decode_outputs(rknn_output* outputs, const cv::Mat& bgr, const LetterBox& lb,
                        std::vector<Detection>& detections);

    YoloConfig cfg_;
    rknn_context ctx_ = 0;
    rknn_tensor_attr* out_attrs_ = nullptr;
    int n_outputs_ = 0;
    int model_w_ = 640;
    int model_h_ = 640;
    int model_c_ = 3;
    unsigned char* input_buf_ = nullptr;
};

float box_iou(const Detection& a, const Detection& b);
float box_gap(const Detection& a, const Detection& b);
const char* hazard_label(int class_id);

#endif
