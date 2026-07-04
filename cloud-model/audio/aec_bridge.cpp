/* WebRTC AEC 桥接 — 供 Python ctypes 调用 */
#include <stdlib.h>
#include <string.h>
#include <webrtc-audio-processing-1/modules/audio_processing/include/audio_processing.h>

using namespace webrtc;

extern "C" {

void* aec_create(int sample_rate, int channels, int frame_size) {
    auto* apm = AudioProcessingBuilder().Create();
    if (!apm) return nullptr;

    AudioProcessing::Config cfg;
    cfg.echo_canceller.enabled = true;
    cfg.echo_canceller.mobile_mode = false;
    cfg.noise_suppression.enabled = true;
    cfg.noise_suppression.level = AudioProcessing::Config::NoiseSuppression::kHigh;
    cfg.gain_controller1.enabled = true;
    cfg.gain_controller1.mode = AudioProcessing::Config::GainController1::kAdaptiveAnalog;
    cfg.gain_controller1.target_level_dbfs = 3;
    cfg.gain_controller1.compression_gain_db = 9;
    cfg.gain_controller1.enable_limiter = true;

    apm->ApplyConfig(cfg);
    apm->Initialize();

    return apm;
}

void aec_feed_playback(void* handle, const int16_t* data, int samples) {
    auto* apm = static_cast<AudioProcessing*>(handle);
    StreamConfig cfg(16000, 1, false);
    apm->ProcessReverseStream(data, cfg, cfg, const_cast<int16_t*>(data));
}

int aec_process(void* handle, int16_t* in, int16_t* out, int samples) {
    auto* apm = static_cast<AudioProcessing*>(handle);
    StreamConfig cfg(16000, 1, false);
    memcpy(out, in, samples * sizeof(int16_t));
    int ret = apm->ProcessStream(out, cfg, cfg, out);
    return ret;
}

void aec_destroy(void* handle) {
    delete static_cast<AudioProcessing*>(handle);
}

}
