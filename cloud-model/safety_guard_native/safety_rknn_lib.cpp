#include "safety_rknn_lib.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <deque>
#include <exception>
#include <map>
#include <memory>
#include <mutex>
#include <set>
#include <sstream>
#include <string>
#include <vector>

#include <opencv2/imgcodecs.hpp>
#include <opencv2/imgproc.hpp>

#include "rknn_yolo.h"

static const int KP_NOSE = 0;
static const int KP_LEYE = 1;
static const int KP_REYE = 2;
static const int KP_LSHOULDER = 5;
static const int KP_RSHOULDER = 6;
static const int KP_LHIP = 11;
static const int KP_RHIP = 12;

static const float POSE_KP_CONF = 0.5f;
static const float FALL_TORSO_ENTER = 55.0f;
static const float FALL_ASPECT_ENTER = 1.3f;
static const float FALL_TORSO_EXIT = 40.0f;
static const float FALL_ASPECT_EXIT = 0.9f;
static const float FAST_FALL_SPEED = 300.0f;
static const float FALL_COOLDOWN_SEC = 3.0f;
static const float SIT_UP_RECOVER_SEC = 0.8f;
static const float FAST_FALL_SIT_UP_RECOVER_SEC = 1.2f;
static const float LIE_NO_MOVE_SEC = 10.0f;
static const float LIE_MOVE_SPEED = 80.0f;

struct RuntimeConfig {
    float relation_min_px = 48.0f;
    float relation_diag_ratio = 0.06f;
    float overlay_scale = 0.42f;
    bool show_top_status = true;
    int jpeg_quality = 72;
    int target_w = 960;
    int target_h = 720;
};

struct Point2f {
    float x = 0.0f;
    float y = 0.0f;
};

struct PersonTrack {
    int id = -1;
    Detection box;
    double last_seen = 0.0;
    bool fallen = false;
    bool fall_alerted = false;
    bool lie_still_warned = false;
    double last_fast_fall = -999.0;
    double recovery_since = -1.0;
    double still_since = -1.0;
    std::deque<std::pair<float, double>> hip_motion;
    std::deque<float> speed_history;
    float hip_speed = 0.0f;
    float smooth_speed = 0.0f;
    float peak_speed = 0.0f;
    float torso_angle = 0.0f;
    float aspect = 0.0f;
    std::string state = "OK";
};

class FallState {
public:
    std::vector<std::string> update(std::vector<Detection>& persons, double t) {
        std::vector<std::string> events;
        std::set<int> active;

        for (auto& det : persons) {
            int tid = match_track(det);
            if (tid < 0) {
                tid = next_id_++;
                tracks_[tid].id = tid;
            }
            auto& tr = tracks_[tid];
            tr.box = det;
            tr.last_seen = t;
            det.track_id = tid;
            active.insert(tid);
            update_one(tr, det, t, events);
        }

        std::vector<int> stale;
        for (auto& kv : tracks_) {
            if (active.count(kv.first)) continue;
            const auto& tr = kv.second;
            double expire = tr.fallen ? 8.0 : 3.0;
            if (t - tr.last_seen > expire) stale.push_back(kv.first);
        }
        for (int id : stale) tracks_.erase(id);
        return events;
    }

    bool active_fall() const {
        for (const auto& kv : tracks_) {
            const auto& tr = kv.second;
            if (tr.fallen || tr.fall_alerted || tr.lie_still_warned) return true;
        }
        return false;
    }

    const std::map<int, PersonTrack>& tracks() const { return tracks_; }

private:
    std::map<int, PersonTrack> tracks_;
    int next_id_ = 1;

    int match_track(const Detection& det) {
        int best = -1;
        float best_iou = 0.25f;
        for (const auto& kv : tracks_) {
            float iou = box_iou(det, kv.second.box);
            if (iou > best_iou) {
                best_iou = iou;
                best = kv.first;
            }
        }
        return best;
    }

    static bool midpoint(const Detection& det, int a, int b, Point2f& out) {
        if ((int)det.kpts.size() <= std::max(a, b)) return false;
        if (det.kpts[a].conf < POSE_KP_CONF || det.kpts[b].conf < POSE_KP_CONF) return false;
        out.x = (det.kpts[a].x + det.kpts[b].x) * 0.5f;
        out.y = (det.kpts[a].y + det.kpts[b].y) * 0.5f;
        return true;
    }

    static float torso_angle_deg(const Point2f& shoulder, const Point2f& hip) {
        float vx = hip.x - shoulder.x;
        float vy = hip.y - shoulder.y;
        float norm = sqrtf(vx * vx + vy * vy);
        if (norm < 1e-4f) return 0.0f;
        float c = std::max(-1.0f, std::min(1.0f, vy / norm));
        return acosf(c) * 180.0f / 3.14159265f;
    }

    void update_motion(PersonTrack& tr, const Detection& det, double t) {
        Point2f hip;
        if (!midpoint(det, KP_LHIP, KP_RHIP, hip)) return;
        tr.hip_motion.push_back({hip.y, t});
        while (tr.hip_motion.size() > 15) tr.hip_motion.pop_front();

        if (tr.hip_motion.size() >= 2) {
            auto p0 = tr.hip_motion[tr.hip_motion.size() - 2];
            auto p1 = tr.hip_motion[tr.hip_motion.size() - 1];
            double dt = p1.second - p0.second;
            tr.hip_speed = dt > 0.02 ? fabsf(p1.first - p0.first) / (float)dt : 0.0f;
        }

        tr.speed_history.push_back(tr.hip_speed);
        while (tr.speed_history.size() > 10) tr.speed_history.pop_front();
        if (tr.speed_history.size() >= 3) {
            std::vector<float> tmp(tr.speed_history.end() - std::min<size_t>(5, tr.speed_history.size()),
                                   tr.speed_history.end());
            std::sort(tmp.begin(), tmp.end());
            tr.smooth_speed = tmp[tmp.size() / 2];
        }

        tr.peak_speed = 0.0f;
        for (size_t i = 1; i < tr.hip_motion.size(); i++) {
            double dt = tr.hip_motion[i].second - tr.hip_motion[i - 1].second;
            if (dt > 0.02) {
                float spd = fabsf(tr.hip_motion[i].first - tr.hip_motion[i - 1].first) / (float)dt;
                tr.peak_speed = std::max(tr.peak_speed, spd);
            }
        }
    }

    void update_one(PersonTrack& tr, const Detection& det, double t, std::vector<std::string>& events) {
        float bw = det.x2 - det.x1;
        float bh = det.y2 - det.y1;
        tr.aspect = bh > 1.0f ? bw / bh : 0.0f;

        Point2f shoulder, hip;
        if (midpoint(det, KP_LSHOULDER, KP_RSHOULDER, shoulder) &&
            midpoint(det, KP_LHIP, KP_RHIP, hip)) {
            tr.torso_angle = torso_angle_deg(shoulder, hip);
        }
        update_motion(tr, det, t);

        int face_vis = 0;
        if ((int)det.kpts.size() > KP_REYE) {
            if (det.kpts[KP_NOSE].conf > 0.4f) face_vis++;
            if (det.kpts[KP_LEYE].conf > 0.4f) face_vis++;
            if (det.kpts[KP_REYE].conf > 0.4f) face_vis++;
        }
        const char* facing = face_vis <= 1 ? "face-down" : "face-up";

        bool geom_fallen = false;
        std::string recovery_reason;
        if (tr.fallen) {
            bool stood_up = tr.torso_angle < FALL_TORSO_EXIT && tr.aspect < FALL_ASPECT_EXIT;
            bool sat_up = tr.torso_angle < FALL_TORSO_ENTER && tr.aspect < FALL_ASPECT_ENTER;
            if (stood_up) {
                tr.recovery_since = -1.0;
                geom_fallen = false;
                recovery_reason = "stood up";
            } else if (sat_up) {
                if (tr.recovery_since < 0.0) tr.recovery_since = t;
                double need = tr.fall_alerted ? FAST_FALL_SIT_UP_RECOVER_SEC : SIT_UP_RECOVER_SEC;
                geom_fallen = (t - tr.recovery_since) < need;
                if (!geom_fallen) recovery_reason = "sat up";
            } else {
                tr.recovery_since = -1.0;
                geom_fallen = true;
            }
        } else {
            tr.recovery_since = -1.0;
            geom_fallen = tr.torso_angle > FALL_TORSO_ENTER && tr.aspect > FALL_ASPECT_ENTER;
        }

        bool cooldown = (t - tr.last_fast_fall) < FALL_COOLDOWN_SEC;
        if (geom_fallen && !tr.fallen) {
            tr.fallen = true;
            tr.still_since = -1.0;
            if (tr.peak_speed > FAST_FALL_SPEED && !cooldown) {
                tr.fall_alerted = true;
                tr.last_fast_fall = t;
                events.push_back("FAST FALL " + std::string(facing));
            } else {
                events.push_back("lying " + std::string(facing));
            }
        } else if (!geom_fallen && tr.fallen) {
            tr.fallen = false;
            tr.fall_alerted = false;
            tr.lie_still_warned = false;
            tr.still_since = -1.0;
            events.push_back("RECOVERED " + recovery_reason);
        }

        if (tr.fallen) {
            if (tr.smooth_speed > LIE_MOVE_SPEED) {
                tr.still_since = -1.0;
            } else {
                if (tr.still_since < 0.0) tr.still_since = t;
                if (t - tr.still_since > LIE_NO_MOVE_SEC && !tr.lie_still_warned) {
                    tr.lie_still_warned = true;
                    events.push_back("LYING STILL");
                }
            }
        }

        if (tr.fall_alerted) tr.state = "FALL";
        else if (tr.lie_still_warned) tr.state = "STILL";
        else if (tr.fallen) tr.state = "LYING";
        else tr.state = "OK";
    }
};

struct Relation {
    Detection hand;
    Detection hazard;
    float gap = 0.0f;
    bool contact = false;
};

static std::vector<Relation> compute_relations(const std::vector<Detection>& hands,
                                               const std::vector<Detection>& hazards,
                                               int w, int h,
                                               float relation_min_px,
                                               float relation_diag_ratio) {
    std::vector<Relation> out;
    float threshold = std::max(relation_min_px, sqrtf((float)(w * w + h * h)) * relation_diag_ratio);
    for (const auto& hand : hands) {
        for (const auto& hazard : hazards) {
            float iou = box_iou(hand, hazard);
            float gap = box_gap(hand, hazard);
            if (iou >= 0.01f || gap <= threshold) {
                Relation r;
                r.hand = hand;
                r.hazard = hazard;
                r.gap = gap;
                r.contact = iou >= 0.01f;
                out.push_back(r);
            }
        }
    }
    return out;
}

static void draw_label(cv::Mat& img, const Detection& d, const std::string& label,
                       const cv::Scalar& color, float font_scale, int thickness = 2) {
    cv::rectangle(img, cv::Point((int)d.x1, (int)d.y1), cv::Point((int)d.x2, (int)d.y2), color, thickness);
    font_scale = std::max(0.28f, std::min(0.70f, font_scale));
    int text_thick = font_scale < 0.50f ? 1 : 2;
    int base = 0;
    auto sz = cv::getTextSize(label, cv::FONT_HERSHEY_SIMPLEX, font_scale, text_thick, &base);
    int pad_x = 4;
    int pad_y = 3;
    int y = std::max(0, (int)d.y1 - sz.height - pad_y * 2);
    int x = std::max(0, std::min((int)d.x1, img.cols - sz.width - pad_x * 2));
    cv::rectangle(img, cv::Rect(x, y, sz.width + pad_x * 2, sz.height + pad_y * 2), color, -1);
    cv::putText(img, label, cv::Point(x + pad_x, y + sz.height + pad_y - 1),
                cv::FONT_HERSHEY_SIMPLEX, font_scale, cv::Scalar(255, 255, 255), text_thick, cv::LINE_AA);
}

static void draw_skeleton(cv::Mat& img, const Detection& d) {
    static const int edges[][2] = {
        {5, 6}, {5, 7}, {7, 9}, {6, 8}, {8, 10}, {5, 11}, {6, 12},
        {11, 12}, {11, 13}, {13, 15}, {12, 14}, {14, 16}, {0, 1},
        {0, 2}, {1, 3}, {2, 4},
    };
    for (auto& e : edges) {
        if ((int)d.kpts.size() <= std::max(e[0], e[1])) continue;
        const auto& a = d.kpts[e[0]];
        const auto& b = d.kpts[e[1]];
        if (a.conf > POSE_KP_CONF && b.conf > POSE_KP_CONF) {
            cv::line(img, cv::Point((int)a.x, (int)a.y), cv::Point((int)b.x, (int)b.y),
                     cv::Scalar(255, 255, 0), 1);
        }
    }
    for (const auto& kp : d.kpts) {
        if (kp.conf > POSE_KP_CONF) {
            cv::circle(img, cv::Point((int)kp.x, (int)kp.y), 3, cv::Scalar(0, 255, 255), -1);
        }
    }
}

static std::string json_escape(const std::string& s) {
    std::string out;
    for (char c : s) {
        if (c == '"' || c == '\\') out += '\\';
        else if (c == '\n') out += "\\n";
        else if (c == '\r') out += "\\r";
        else out += c;
    }
    return out;
}

static void append_det_box(std::ostringstream& os, const Detection& d) {
    os << "[" << d.x1 << "," << d.y1 << "," << d.x2 << "," << d.y2 << "]";
}

static std::string build_json(int frame_idx, double fps, float pose_ms, float hand_ms, float hazard_ms,
                              double last_hazard_age,
                              const FallState& fall_state,
                              const std::vector<Detection>& persons,
                              const std::vector<Detection>& hands,
                              const std::vector<Detection>& hazards,
                              const std::vector<Relation>& relations,
                              const std::vector<std::string>& events) {
    std::ostringstream os;
    os.setf(std::ios::fixed);
    os.precision(2);
    os << "{\"ready\":true,\"frame\":" << frame_idx
       << ",\"fps\":" << fps
       << ",\"pose_ms\":" << pose_ms
       << ",\"hand_ms\":" << hand_ms
       << ",\"hazard_ms\":" << hazard_ms
       << ",\"hazard_age_sec\":" << last_hazard_age
       << ",\"fall_active\":" << (fall_state.active_fall() ? "true" : "false")
       << ",\"hand_hazard_active\":" << (!relations.empty() ? "true" : "false")
       << ",\"counts\":{\"persons\":" << persons.size()
       << ",\"hands\":" << hands.size()
       << ",\"hazards\":" << hazards.size()
       << ",\"relations\":" << relations.size() << "},";

    os << "\"tracks\":[";
    bool first = true;
    for (const auto& kv : fall_state.tracks()) {
        const auto& tr = kv.second;
        if (!first) os << ",";
        first = false;
        os << "{\"id\":" << tr.id << ",\"state\":\"" << tr.state << "\",\"torso\":"
           << tr.torso_angle << ",\"aspect\":" << tr.aspect << ",\"speed\":" << tr.hip_speed
           << ",\"smooth_speed\":" << tr.smooth_speed << ",\"peak\":" << tr.peak_speed << "}";
    }
    os << "],\"hazards\":[";
    for (size_t i = 0; i < hazards.size(); i++) {
        if (i) os << ",";
        os << "{\"class_id\":" << hazards[i].class_id << ",\"name\":\""
           << hazard_label(hazards[i].class_id) << "\",\"conf\":" << hazards[i].conf
           << ",\"box\":";
        append_det_box(os, hazards[i]);
        os << "}";
    }
    os << "],\"hands\":[";
    for (size_t i = 0; i < hands.size(); i++) {
        if (i) os << ",";
        os << "{\"conf\":" << hands[i].conf << ",\"box\":";
        append_det_box(os, hands[i]);
        os << "}";
    }
    os << "],\"relations\":[";
    for (size_t i = 0; i < relations.size(); i++) {
        if (i) os << ",";
        os << "{\"hazard\":\"" << hazard_label(relations[i].hazard.class_id)
           << "\",\"gap\":" << relations[i].gap
           << ",\"contact\":" << (relations[i].contact ? "true" : "false") << "}";
    }
    os << "],\"events\":[";
    for (size_t i = 0; i < events.size(); i++) {
        if (i) os << ",";
        os << "\"" << json_escape(events[i]) << "\"";
    }
    os << "]}";
    return os.str();
}

static void draw_text_line(cv::Mat& img, const std::string& text, int x, int y,
                           float scale, const cv::Scalar& fg, const cv::Scalar& bg) {
    scale = std::max(0.26f, std::min(0.70f, scale));
    int thick = scale < 0.50f ? 1 : 2;
    int base = 0;
    auto sz = cv::getTextSize(text, cv::FONT_HERSHEY_SIMPLEX, scale, thick, &base);
    int pad_x = 4;
    int pad_y = 3;
    cv::Rect rect(std::max(0, x - pad_x), std::max(0, y - sz.height - pad_y),
                  std::min(img.cols - std::max(0, x - pad_x), sz.width + pad_x * 2),
                  sz.height + pad_y * 2);
    cv::rectangle(img, rect, bg, -1);
    cv::putText(img, text, cv::Point(x, y), cv::FONT_HERSHEY_SIMPLEX, scale,
                cv::Scalar(20, 20, 20), thick + 2, cv::LINE_AA);
    cv::putText(img, text, cv::Point(x, y), cv::FONT_HERSHEY_SIMPLEX, scale,
                fg, thick, cv::LINE_AA);
}

static float fit_text_scale(const cv::Mat& img, const std::string& text, float wanted_scale) {
    float scale = wanted_scale;
    while (scale > 0.26f) {
        int base = 0;
        int thick = scale < 0.50f ? 1 : 2;
        auto sz = cv::getTextSize(text, cv::FONT_HERSHEY_SIMPLEX, scale, thick, &base);
        if (sz.width <= img.cols - 16) return scale;
        scale *= 0.90f;
    }
    return scale;
}

static void draw_overlay(cv::Mat& img, const FallState& fall_state,
                         const std::vector<Detection>& persons,
                         const std::vector<Detection>& hands,
                         const std::vector<Detection>& hazards,
                         const std::vector<Relation>& relations,
                         double fps, float pose_ms, float hand_ms, float hazard_ms,
                         const RuntimeConfig& cfg) {
    float label_scale = std::max(0.30f, std::min(0.65f, cfg.overlay_scale));
    for (const auto& p : persons) {
        auto it = fall_state.tracks().find(p.track_id);
        std::string state = it != fall_state.tracks().end() ? it->second.state : "OK";
        cv::Scalar color = state == "OK" ? cv::Scalar(0, 220, 0) : cv::Scalar(0, 0, 255);
        char label[128];
        snprintf(label, sizeof(label), "ID:%d %s %.2f", p.track_id, state.c_str(), p.conf);
        draw_label(img, p, label, color, label_scale, 2);
        draw_skeleton(img, p);
    }
    for (const auto& h : hazards) {
        char label[96];
        snprintf(label, sizeof(label), "hazard/%s %.2f", hazard_label(h.class_id), h.conf);
        draw_label(img, h, label, cv::Scalar(0, 0, 255), label_scale, 2);
    }
    for (const auto& h : hands) {
        char label[64];
        snprintf(label, sizeof(label), "hand %.2f", h.conf);
        draw_label(img, h, label, cv::Scalar(0, 180, 255), label_scale, 2);
    }
    for (const auto& r : relations) {
        cv::Point hc((int)((r.hand.x1 + r.hand.x2) * 0.5f), (int)((r.hand.y1 + r.hand.y2) * 0.5f));
        cv::Point zc((int)((r.hazard.x1 + r.hazard.x2) * 0.5f), (int)((r.hazard.y1 + r.hazard.y2) * 0.5f));
        cv::line(img, hc, zc, cv::Scalar(0, 255, 255), 2);
        char text[96];
        snprintf(text, sizeof(text), "%s %s gap=%.0fpx", r.contact ? "CONTACT" : "NEAR",
                 hazard_label(r.hazard.class_id), r.gap);
        float rel_scale = std::max(0.34f, label_scale);
        int rel_thick = rel_scale < 0.50f ? 1 : 2;
        cv::putText(img, text, cv::Point(std::min(hc.x, zc.x), std::max(78, std::min(hc.y, zc.y) - 8)),
                    cv::FONT_HERSHEY_SIMPLEX, rel_scale, cv::Scalar(20, 20, 20), rel_thick + 2, cv::LINE_AA);
        cv::putText(img, text, cv::Point(std::min(hc.x, zc.x), std::max(78, std::min(hc.y, zc.y) - 8)),
                    cv::FONT_HERSHEY_SIMPLEX, rel_scale, cv::Scalar(0, 255, 255), rel_thick, cv::LINE_AA);
    }

    bool fall = fall_state.active_fall();
    bool near = !relations.empty();
    if (cfg.show_top_status) {
        const char* status = fall && near ? "FALL+NEAR" : (fall ? "FALL" : (near ? "NEAR" : "OK"));
        cv::Scalar color = (fall || near) ? cv::Scalar(0, 0, 255) : cv::Scalar(0, 220, 0);
        cv::Scalar bg(36, 36, 36);
        char line1[192];
        snprintf(line1, sizeof(line1), "%s fps %.1f | pose %.0f hand %.0f haz %.0f",
                 status, fps, pose_ms, hand_ms, hazard_ms);
        char line2[128];
        snprintf(line2, sizeof(line2), "P:%zu hand:%zu haz:%zu rel:%zu",
                 persons.size(), hands.size(), hazards.size(), relations.size());
        float s1 = fit_text_scale(img, line1, cfg.overlay_scale);
        float s2 = std::max(0.28f, s1 * 0.92f);
        int base = 0;
        auto sz1 = cv::getTextSize(line1, cv::FONT_HERSHEY_SIMPLEX, s1, s1 < 0.50f ? 1 : 2, &base);
        draw_text_line(img, line1, 8, 8 + sz1.height, s1, color, bg);
        draw_text_line(img, line2, 8, 12 + sz1.height + (int)(18.0f * s2), s2, cv::Scalar(245, 245, 245), bg);
    }
}

struct SafetyRknnContext {
    explicit SafetyRknnContext()
        : pose({"pose", 1, true, 17, 0.35f, 0.45f, 8, {}}),
          hand({"hand", 1, false, 0, 0.30f, 0.45f, 20, {}}),
          hazard({"hazard", 80, false, 0, 0.04f, 0.45f, 40, {42, 43, 76}}) {}

    RknnYolo pose;
    RknnYolo hand;
    RknnYolo hazard;
    FallState fall_state;
    std::vector<Detection> hands;
    std::vector<Detection> hazards;
    std::vector<Relation> relations;
    std::deque<double> frame_times;
    RuntimeConfig config;
    std::string last_error;
    double last_hazard_t = -999.0;
    float pose_ms = 0.0f;
    float hand_ms = 0.0f;
    float hazard_ms = 0.0f;
    int frame_idx = 0;
};

static std::mutex g_error_mtx;
static std::string g_create_error;

static void set_global_error(const std::string& err) {
    std::lock_guard<std::mutex> lk(g_error_mtx);
    g_create_error = err;
}

static void write_json_error(char* json_buf, int json_cap, const std::string& err) {
    if (!json_buf || json_cap <= 0) return;
    std::string msg = "{\"ready\":false,\"error\":\"" + json_escape(err) + "\"}";
    std::snprintf(json_buf, (size_t)json_cap, "%s", msg.c_str());
}

extern "C" void* safety_rknn_create(const char* pose_path, const char* hand_path, const char* hazard_path) {
    try {
        std::unique_ptr<SafetyRknnContext> ctx(new SafetyRknnContext());
        if (!pose_path || !ctx->pose.init(pose_path)) {
            set_global_error("failed to init pose model");
            return nullptr;
        }
        if (!hand_path || !ctx->hand.init(hand_path)) {
            set_global_error("failed to init hand model");
            return nullptr;
        }
        if (!hazard_path || !ctx->hazard.init(hazard_path)) {
            set_global_error("failed to init hazard model");
            return nullptr;
        }
        set_global_error("");
        return ctx.release();
    } catch (const std::exception& e) {
        set_global_error(e.what());
        return nullptr;
    } catch (...) {
        set_global_error("unknown create exception");
        return nullptr;
    }
}

extern "C" void safety_rknn_destroy(void* handle) {
    delete static_cast<SafetyRknnContext*>(handle);
}

extern "C" int safety_rknn_set_config(void* handle,
                                      float pose_conf,
                                      float hand_conf,
                                      float hazard_conf,
                                      float relation_min_px,
                                      float relation_diag_ratio,
                                      float overlay_scale,
                                      int show_top_status,
                                      int jpeg_quality,
                                      int target_w,
                                      int target_h) {
    if (!handle) return -1;
    SafetyRknnContext* ctx = static_cast<SafetyRknnContext*>(handle);
    if (pose_conf >= 0.0f) ctx->pose.set_conf_thresh(pose_conf);
    if (hand_conf >= 0.0f) ctx->hand.set_conf_thresh(hand_conf);
    if (hazard_conf >= 0.0f) ctx->hazard.set_conf_thresh(hazard_conf);
    if (relation_min_px > 0.0f) ctx->config.relation_min_px = relation_min_px;
    if (relation_diag_ratio > 0.0f) ctx->config.relation_diag_ratio = relation_diag_ratio;
    if (overlay_scale > 0.0f) ctx->config.overlay_scale = overlay_scale;
    ctx->config.show_top_status = show_top_status != 0;
    if (jpeg_quality > 0) ctx->config.jpeg_quality = std::max(30, std::min(95, jpeg_quality));
    if (target_w > 0) ctx->config.target_w = target_w;
    if (target_h > 0) ctx->config.target_h = target_h;
    return 0;
}

extern "C" int safety_rknn_process_bgr(void* handle,
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
                                        int json_cap) {
    if (jpg_size) *jpg_size = 0;
    if (!handle) {
        write_json_error(json_buf, json_cap, "null handle");
        return -1;
    }
    SafetyRknnContext* ctx = static_cast<SafetyRknnContext*>(handle);
    try {
        if (!bgr || width <= 0 || height <= 0 || stride < width * 3) {
            ctx->last_error = "invalid BGR frame";
            write_json_error(json_buf, json_cap, ctx->last_error);
            return -2;
        }
        if (!jpg_buf || jpg_cap <= 0 || !jpg_size || !json_buf || json_cap <= 0) {
            ctx->last_error = "invalid output buffers";
            return -3;
        }

        cv::Mat frame(height, width, CV_8UC3, const_cast<unsigned char*>(bgr), (size_t)stride);
        cv::Mat infer_frame;
        float scale = std::min(1280.0f / frame.cols, 960.0f / frame.rows);
        if (scale < 1.0f) {
            cv::resize(frame, infer_frame, cv::Size((int)(frame.cols * scale), (int)(frame.rows * scale)));
        } else {
            infer_frame = frame;
        }

        std::vector<Detection> persons;
        if (!ctx->pose.infer(infer_frame, persons, &ctx->pose_ms)) {
            ctx->last_error = "pose inference failed";
            write_json_error(json_buf, json_cap, ctx->last_error);
            return -4;
        }
        std::vector<std::string> events = ctx->fall_state.update(persons, now_sec);

        if (run_hazard || ctx->last_hazard_t < -900.0) {
            if (!ctx->hand.infer(infer_frame, ctx->hands, &ctx->hand_ms)) {
                ctx->last_error = "hand inference failed";
                write_json_error(json_buf, json_cap, ctx->last_error);
                return -5;
            }
            if (!ctx->hazard.infer(infer_frame, ctx->hazards, &ctx->hazard_ms)) {
                ctx->last_error = "hazard inference failed";
                write_json_error(json_buf, json_cap, ctx->last_error);
                return -6;
            }
            ctx->relations = compute_relations(ctx->hands, ctx->hazards, infer_frame.cols, infer_frame.rows,
                                               ctx->config.relation_min_px,
                                               ctx->config.relation_diag_ratio);
            ctx->last_hazard_t = now_sec;
        }

        ctx->frame_times.push_back(now_sec);
        while (ctx->frame_times.size() > 30) ctx->frame_times.pop_front();
        double fps = 0.0;
        if (ctx->frame_times.size() >= 2) {
            double dt = ctx->frame_times.back() - ctx->frame_times.front();
            if (dt > 0.0) fps = (ctx->frame_times.size() - 1) / dt;
        }

        cv::Mat stream_frame = infer_frame.clone();
        draw_overlay(stream_frame, ctx->fall_state, persons, ctx->hands, ctx->hazards,
                     ctx->relations, fps, ctx->pose_ms, ctx->hand_ms, ctx->hazard_ms,
                     ctx->config);
        if (stream_frame.cols > ctx->config.target_w || stream_frame.rows > ctx->config.target_h) {
            float s = std::min((float)ctx->config.target_w / stream_frame.cols,
                               (float)ctx->config.target_h / stream_frame.rows);
            cv::Mat resized;
            cv::resize(stream_frame, resized,
                       cv::Size((int)(stream_frame.cols * s), (int)(stream_frame.rows * s)));
            stream_frame = resized;
        }

        std::vector<uchar> jpg;
        if (!cv::imencode(".jpg", stream_frame, jpg, {cv::IMWRITE_JPEG_QUALITY, ctx->config.jpeg_quality})) {
            ctx->last_error = "jpeg encode failed";
            write_json_error(json_buf, json_cap, ctx->last_error);
            return -7;
        }
        if ((int)jpg.size() > jpg_cap) {
            ctx->last_error = "jpeg output buffer too small";
            write_json_error(json_buf, json_cap, ctx->last_error);
            return -8;
        }

        double hazard_age = ctx->last_hazard_t < -900.0 ? -1.0 : now_sec - ctx->last_hazard_t;
        std::string json = build_json(ctx->frame_idx, fps, ctx->pose_ms, ctx->hand_ms, ctx->hazard_ms,
                                      hazard_age, ctx->fall_state, persons, ctx->hands, ctx->hazards,
                                      ctx->relations, events);
        if ((int)json.size() + 1 > json_cap) {
            ctx->last_error = "json output buffer too small";
            write_json_error(json_buf, json_cap, ctx->last_error);
            return -9;
        }
        std::memcpy(jpg_buf, jpg.data(), jpg.size());
        *jpg_size = (int)jpg.size();
        std::memcpy(json_buf, json.c_str(), json.size() + 1);
        ctx->last_error.clear();
        ctx->frame_idx++;
        return 0;
    } catch (const std::exception& e) {
        ctx->last_error = e.what();
        write_json_error(json_buf, json_cap, ctx->last_error);
        return -10;
    } catch (...) {
        ctx->last_error = "unknown process exception";
        write_json_error(json_buf, json_cap, ctx->last_error);
        return -11;
    }
}

extern "C" const char* safety_rknn_last_error(void* handle) {
    if (handle) {
        SafetyRknnContext* ctx = static_cast<SafetyRknnContext*>(handle);
        return ctx->last_error.c_str();
    }
    std::lock_guard<std::mutex> lk(g_error_mtx);
    return g_create_error.c_str();
}
