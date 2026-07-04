#include "rknn_yolo.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <numeric>
#include <set>
#include <sys/time.h>
#include <utility>

#include <opencv2/imgproc.hpp>

static const int DFL_BINS = 16;
static const int STRIDES[3] = {8, 16, 32};

static inline int64_t now_us() {
    timeval tv;
    gettimeofday(&tv, nullptr);
    return (int64_t)tv.tv_sec * 1000000 + tv.tv_usec;
}

static inline float sigmoid(float x) {
    return 1.0f / (1.0f + expf(-x));
}

static void softmax16(const float* x, float* out) {
    float mx = x[0];
    for (int i = 1; i < DFL_BINS; i++) mx = std::max(mx, x[i]);
    float sum = 0.0f;
    for (int i = 0; i < DFL_BINS; i++) {
        out[i] = expf(x[i] - mx);
        sum += out[i];
    }
    if (sum <= 0.0f) return;
    for (int i = 0; i < DFL_BINS; i++) out[i] /= sum;
}

static float dfl_decode_channel(const float* feat, int grid_len, int offset, int box_idx) {
    float vals[DFL_BINS];
    for (int d = 0; d < DFL_BINS; d++) {
        vals[d] = feat[(box_idx * DFL_BINS + d) * grid_len + offset];
    }
    float probs[DFL_BINS];
    softmax16(vals, probs);
    float acc = 0.0f;
    for (int d = 0; d < DFL_BINS; d++) acc += probs[d] * (float)d;
    return acc;
}

float box_iou(const Detection& a, const Detection& b) {
    float ix1 = std::max(a.x1, b.x1);
    float iy1 = std::max(a.y1, b.y1);
    float ix2 = std::min(a.x2, b.x2);
    float iy2 = std::min(a.y2, b.y2);
    float iw = std::max(0.0f, ix2 - ix1);
    float ih = std::max(0.0f, iy2 - iy1);
    float inter = iw * ih;
    float aa = std::max(0.0f, a.x2 - a.x1) * std::max(0.0f, a.y2 - a.y1);
    float ab = std::max(0.0f, b.x2 - b.x1) * std::max(0.0f, b.y2 - b.y1);
    float u = aa + ab - inter;
    return u > 0.0f ? inter / u : 0.0f;
}

float box_gap(const Detection& a, const Detection& b) {
    float dx = std::max(std::max(b.x1 - a.x2, a.x1 - b.x2), 0.0f);
    float dy = std::max(std::max(b.y1 - a.y2, a.y1 - b.y2), 0.0f);
    return sqrtf(dx * dx + dy * dy);
}

const char* hazard_label(int class_id) {
    switch (class_id) {
        case 42: return "fork";
        case 43: return "knife";
        case 76: return "scissors";
        default: return "hazard";
    }
}

RknnYolo::RknnYolo(YoloConfig cfg) : cfg_(std::move(cfg)) {}

RknnYolo::~RknnYolo() {
    release();
}

void RknnYolo::set_conf_thresh(float value) {
    if (value >= 0.0f && value <= 1.0f) cfg_.conf_thresh = value;
}

void RknnYolo::set_nms_thresh(float value) {
    if (value >= 0.0f && value <= 1.0f) cfg_.nms_thresh = value;
}

void RknnYolo::set_max_det(int value) {
    if (value > 0) cfg_.max_det = value;
}

bool RknnYolo::init(const std::string& model_path) {
    release();
    int ret = rknn_init(&ctx_, (char*)model_path.c_str(), 0, 0, nullptr);
    if (ret < 0) {
        fprintf(stderr, "[%s] rknn_init failed: %d\n", cfg_.name.c_str(), ret);
        return false;
    }

    rknn_input_output_num io_num;
    ret = rknn_query(ctx_, RKNN_QUERY_IN_OUT_NUM, &io_num, sizeof(io_num));
    if (ret < 0) {
        fprintf(stderr, "[%s] query io num failed: %d\n", cfg_.name.c_str(), ret);
        return false;
    }
    n_outputs_ = io_num.n_output;

    rknn_tensor_attr in_attr;
    memset(&in_attr, 0, sizeof(in_attr));
    in_attr.index = 0;
    ret = rknn_query(ctx_, RKNN_QUERY_INPUT_ATTR, &in_attr, sizeof(in_attr));
    if (ret < 0) {
        fprintf(stderr, "[%s] query input attr failed: %d\n", cfg_.name.c_str(), ret);
        return false;
    }

    if (in_attr.fmt == RKNN_TENSOR_NHWC) {
        model_h_ = in_attr.dims[1];
        model_w_ = in_attr.dims[2];
        model_c_ = in_attr.dims[3];
    } else {
        model_c_ = in_attr.dims[1];
        model_h_ = in_attr.dims[2];
        model_w_ = in_attr.dims[3];
    }

    out_attrs_ = (rknn_tensor_attr*)calloc(n_outputs_, sizeof(rknn_tensor_attr));
    if (!out_attrs_) return false;
    for (int i = 0; i < n_outputs_; i++) {
        out_attrs_[i].index = i;
        ret = rknn_query(ctx_, RKNN_QUERY_OUTPUT_ATTR, &out_attrs_[i], sizeof(rknn_tensor_attr));
        if (ret < 0) {
            fprintf(stderr, "[%s] query output %d attr failed: %d\n", cfg_.name.c_str(), i, ret);
            return false;
        }
        fprintf(stdout, "[%s] output%d dims=[%d,%d,%d,%d] n_dims=%d fmt=%d type=%d qnt=%d\n",
                cfg_.name.c_str(), i,
                out_attrs_[i].dims[0], out_attrs_[i].dims[1],
                out_attrs_[i].dims[2], out_attrs_[i].dims[3],
                out_attrs_[i].n_dims, out_attrs_[i].fmt, out_attrs_[i].type,
                out_attrs_[i].qnt_type);
    }

    input_buf_ = (unsigned char*)malloc(model_w_ * model_h_ * model_c_);
    if (!input_buf_) return false;

    fprintf(stdout, "[%s] loaded %s input=%dx%dx%d outputs=%d\n",
            cfg_.name.c_str(), model_path.c_str(), model_w_, model_h_, model_c_, n_outputs_);
    return true;
}

void RknnYolo::release() {
    if (input_buf_) {
        free(input_buf_);
        input_buf_ = nullptr;
    }
    if (out_attrs_) {
        free(out_attrs_);
        out_attrs_ = nullptr;
    }
    if (ctx_) {
        rknn_destroy(ctx_);
        ctx_ = 0;
    }
}

void RknnYolo::letterbox_rgb(const cv::Mat& bgr, unsigned char* out, LetterBox* lb) const {
    float r = std::min((float)model_w_ / bgr.cols, (float)model_h_ / bgr.rows);
    int nw = (int)roundf(bgr.cols * r);
    int nh = (int)roundf(bgr.rows * r);
    int ox = (model_w_ - nw) / 2;
    int oy = (model_h_ - nh) / 2;

    cv::Mat rgb;
    cv::cvtColor(bgr, rgb, cv::COLOR_BGR2RGB);
    cv::Mat canvas(model_h_, model_w_, CV_8UC3, out);
    canvas.setTo(cv::Scalar(114, 114, 114));
    cv::Mat roi = canvas(cv::Rect(ox, oy, nw, nh));
    cv::resize(rgb, roi, cv::Size(nw, nh), 0, 0, cv::INTER_LINEAR);

    lb->scale = r;
    lb->x_pad = ox;
    lb->y_pad = oy;
}

bool RknnYolo::infer(const cv::Mat& bgr, std::vector<Detection>& detections, float* infer_ms) {
    detections.clear();
    if (!ctx_ || bgr.empty()) return false;

    LetterBox lb;
    letterbox_rgb(bgr, input_buf_, &lb);

    rknn_input input;
    memset(&input, 0, sizeof(input));
    input.index = 0;
    input.type = RKNN_TENSOR_UINT8;
    input.fmt = RKNN_TENSOR_NHWC;
    input.size = model_w_ * model_h_ * model_c_;
    input.buf = input_buf_;

    int ret = rknn_inputs_set(ctx_, 1, &input);
    if (ret < 0) return false;

    int64_t t0 = now_us();
    ret = rknn_run(ctx_, nullptr);
    int64_t t1 = now_us();
    if (ret < 0) return false;
    if (infer_ms) *infer_ms = (t1 - t0) / 1000.0f;

    rknn_output outputs[8];
    memset(outputs, 0, sizeof(outputs));
    for (int i = 0; i < n_outputs_ && i < 8; i++) {
        outputs[i].index = i;
        outputs[i].want_float = 1;
    }

    ret = rknn_outputs_get(ctx_, n_outputs_, outputs, nullptr);
    if (ret < 0) return false;

    decode_outputs(outputs, bgr, lb, detections);
    rknn_outputs_release(ctx_, n_outputs_, outputs);
    return true;
}

void RknnYolo::decode_outputs(rknn_output* outputs, const cv::Mat& bgr, const LetterBox& lb,
                              std::vector<Detection>& detections) {
    std::set<int> filter(cfg_.class_filter.begin(), cfg_.class_filter.end());
    std::vector<Detection> raw;
    std::vector<int> raw_kpt_idx;
    std::vector<int> raw_grid_x;
    std::vector<int> raw_grid_y;
    std::vector<int> raw_stride;
    int cell_offset = 0;

    for (int fi = 0; fi < 3 && fi < n_outputs_; fi++) {
        const auto& attr = out_attrs_[fi];
        int ch = attr.dims[1];
        int gh = attr.dims[2];
        int gw = attr.dims[3];
        if (ch < 64 + cfg_.num_classes || gh <= 0 || gw <= 0) continue;
        int stride = (fi < 3) ? STRIDES[fi] : (model_w_ / gw);
        int grid_len = gh * gw;
        const float* feat = (const float*)outputs[fi].buf;

        for (int y = 0; y < gh; y++) {
            for (int x = 0; x < gw; x++) {
                int offset = y * gw + x;
                int best_id = -1;
                float best_conf = 0.0f;

                if (filter.empty()) {
                    for (int c = 0; c < cfg_.num_classes; c++) {
                        float conf = sigmoid(feat[(64 + c) * grid_len + offset]);
                        if (conf > best_conf) {
                            best_conf = conf;
                            best_id = c;
                        }
                    }
                } else {
                    for (int c : filter) {
                        if (c < 0 || c >= cfg_.num_classes) continue;
                        float conf = sigmoid(feat[(64 + c) * grid_len + offset]);
                        if (conf > best_conf) {
                            best_conf = conf;
                            best_id = c;
                        }
                    }
                }
                if (best_id < 0 || best_conf < cfg_.conf_thresh) continue;

                float d0 = dfl_decode_channel(feat, grid_len, offset, 0);
                float d1 = dfl_decode_channel(feat, grid_len, offset, 1);
                float d2 = dfl_decode_channel(feat, grid_len, offset, 2);
                float d3 = dfl_decode_channel(feat, grid_len, offset, 3);

                float x1_l = ((x + 0.5f - d0) * stride - lb.x_pad) / lb.scale;
                float y1_l = ((y + 0.5f - d1) * stride - lb.y_pad) / lb.scale;
                float x2_l = ((x + 0.5f + d2) * stride - lb.x_pad) / lb.scale;
                float y2_l = ((y + 0.5f + d3) * stride - lb.y_pad) / lb.scale;

                Detection det;
                det.x1 = std::max(0.0f, std::min((float)bgr.cols - 1, x1_l));
                det.y1 = std::max(0.0f, std::min((float)bgr.rows - 1, y1_l));
                det.x2 = std::max(0.0f, std::min((float)bgr.cols - 1, x2_l));
                det.y2 = std::max(0.0f, std::min((float)bgr.rows - 1, y2_l));
                det.conf = best_conf;
                det.class_id = best_id;
                if (det.x2 <= det.x1 || det.y2 <= det.y1) continue;
                raw.push_back(det);
                raw_kpt_idx.push_back(cell_offset + offset);
                raw_grid_x.push_back(x);
                raw_grid_y.push_back(y);
                raw_stride.push_back(stride);
            }
        }
        cell_offset += grid_len;
    }

    std::vector<int> order(raw.size());
    std::iota(order.begin(), order.end(), 0);
    std::sort(order.begin(), order.end(), [&](int a, int b) {
        return raw[a].conf > raw[b].conf;
    });

    std::vector<char> suppressed(raw.size(), 0);
    for (size_t oi = 0; oi < order.size() && (int)detections.size() < cfg_.max_det; oi++) {
        int idx = order[oi];
        if (suppressed[idx]) continue;
        Detection det = raw[idx];

        if (cfg_.is_pose && n_outputs_ >= 4 && cfg_.num_keypoints > 0) {
            const float* kpt = (const float*)outputs[3].buf;
            int total = 0;
            const auto& kattr = out_attrs_[3];
            if (kattr.n_dims == 3) {
                total = kattr.dims[2];
            } else if (kattr.n_dims == 4) {
                total = kattr.dims[2] * kattr.dims[3];
            }
            if (total <= 0) total = cell_offset;
            int kidx = raw_kpt_idx[idx];
            int gx = raw_grid_x[idx];
            int gy = raw_grid_y[idx];
            int stride = raw_stride[idx];
            det.kpts.reserve(cfg_.num_keypoints);
            for (int k = 0; k < cfg_.num_keypoints; k++) {
                float kx = kpt[(k * 3 + 0) * total + kidx];
                float ky = kpt[(k * 3 + 1) * total + kidx];
                float kc = kpt[(k * 3 + 2) * total + kidx];
                Keypoint kp;
                kp.x = ((kx * 2.0f + gx) * stride - lb.x_pad) / lb.scale;
                kp.y = ((ky * 2.0f + gy) * stride - lb.y_pad) / lb.scale;
                kp.conf = sigmoid(kc);
                det.kpts.push_back(kp);
            }
        }

        detections.push_back(det);
        for (size_t oj = oi + 1; oj < order.size(); oj++) {
            int j = order[oj];
            if (suppressed[j]) continue;
            if (raw[j].class_id != raw[idx].class_id) continue;
            if (box_iou(raw[idx], raw[j]) > cfg_.nms_thresh) {
                suppressed[j] = 1;
            }
        }
    }
}
