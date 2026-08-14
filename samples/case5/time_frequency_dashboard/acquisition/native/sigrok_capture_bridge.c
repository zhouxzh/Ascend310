/*
 * Continuous Hantek 6022BE capture bridge for the Case 5 dashboard.
 *
 * libsigrok owns firmware loading, USB access, channel scaling and the capture
 * session.  This process only pairs CH1/CH2 analog packets and writes the
 * existing BridgeFrameV1 wire format to stdout.  Diagnostics stay on stderr.
 *
 * SPDX-License-Identifier: GPL-3.0-only
 */

/* Request the POSIX clock_gettime declaration under strict -std=c11 builds. */
#define _POSIX_C_SOURCE 200809L

#include <errno.h>
#include <inttypes.h>
#include <limits.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include <libsigrok/libsigrok.h>

#if __BYTE_ORDER__ != __ORDER_LITTLE_ENDIAN__
#error "BridgeFrameV1 currently requires a little-endian target"
#endif

#define BRIDGE_VERSION 1
#define BRIDGE_CHANNELS 2
#define BRIDGE_FLAG_CLIPPED (1U << 0)
#define MAX_BRIDGE_FRAME_SAMPLES 1000000U
/* The hantek-6xxx driver caps a Linux USB transfer at 12 MiB and expands it
 * into one analog packet per channel. Keep enough space for one full channel
 * packet while retaining a hard bound if callbacks ever become asymmetric. */
#define MAX_CHANNEL_BUFFER_SAMPLES (12U * 1024U * 1024U / BRIDGE_CHANNELS)
#define HANTEK_6022BE_MAX_SAMPLE_RATE_HZ UINT64_C(48000000)

struct __attribute__((packed)) bridge_header {
    char magic[4];
    uint16_t version;
    uint16_t header_bytes;
    uint64_t sequence;
    uint64_t host_receive_ns;
    double sample_rate_hz;
    uint32_t sample_count;
    uint16_t channels;
    uint16_t flags;
    uint32_t payload_bytes;
};

_Static_assert(sizeof(struct bridge_header) == 44, "BridgeFrameV1 header size changed");

struct bridge_options {
    uint64_t sample_rate_hz;
    uint32_t frame_samples;
    uint64_t callback_msec;
    uint64_t ch1_vdiv_num;
    uint64_t ch1_vdiv_den;
    uint64_t ch2_vdiv_num;
    uint64_t ch2_vdiv_den;
};

struct bridge_state {
    struct sr_dev_inst *device;
    struct sr_session *session;
    struct bridge_options options;
    float *channel_values[BRIDGE_CHANNELS];
    uint32_t channel_counts[BRIDGE_CHANNELS];
    float *frame_values;
    uint32_t frame_fill;
    uint16_t frame_flags;
    uint64_t sequence;
    uint64_t session_start_ns;
    uint64_t frames_written;
    uint64_t analog_pairs;
    int saw_end;
    int write_failed;
};

static volatile sig_atomic_t stop_requested;

static uint64_t monotonic_ns(void)
{
    struct timespec timestamp;

    if (clock_gettime(CLOCK_MONOTONIC, &timestamp) != 0)
        return 0;
    return (uint64_t)timestamp.tv_sec * UINT64_C(1000000000) + timestamp.tv_nsec;
}

static int is_hantek_6022be_sample_rate(uint64_t sample_rate_hz)
{
    switch (sample_rate_hz) {
    case UINT64_C(100000):
    case UINT64_C(200000):
    case UINT64_C(500000):
    case UINT64_C(1000000):
    case UINT64_C(4000000):
    case UINT64_C(8000000):
    case UINT64_C(16000000):
    case UINT64_C(24000000):
    case UINT64_C(30000000):
    case UINT64_C(48000000):
        return 1;
    default:
        return 0;
    }
}

static void handle_signal(int signal_number)
{
    (void)signal_number;
    stop_requested = 1;
}

static int write_all(const void *data, size_t length)
{
    const uint8_t *cursor = data;

    while (length > 0) {
        size_t write_length = length > (size_t)SSIZE_MAX ? (size_t)SSIZE_MAX : length;
        ssize_t written = write(STDOUT_FILENO, cursor, write_length);
        if (written < 0) {
            if (errno == EINTR)
                continue;
            return -1;
        }
        if (written == 0) {
            errno = EIO;
            return -1;
        }
        cursor += written;
        length -= (size_t)written;
    }
    return 0;
}

static int emit_frame(struct bridge_state *state, uint64_t receive_ns)
{
    struct bridge_header header = {
        .magic = {'C', '5', 'B', 'F'},
        .version = BRIDGE_VERSION,
        .header_bytes = sizeof(struct bridge_header),
        .sequence = state->sequence,
        .host_receive_ns = receive_ns,
        .sample_rate_hz = (double)state->options.sample_rate_hz,
        .sample_count = state->options.frame_samples,
        .channels = BRIDGE_CHANNELS,
        .flags = state->frame_flags,
        .payload_bytes = state->options.frame_samples * BRIDGE_CHANNELS * sizeof(float),
    };

    if (write_all(&header, sizeof(header)) != 0 ||
        write_all(state->frame_values, header.payload_bytes) != 0) {
        state->write_failed = 1;
        return -1;
    }
    state->sequence++;
    state->frames_written++;
    state->frame_fill = 0;
    state->frame_flags = 0;
    return 0;
}

static int append_channel_pair(struct bridge_state *state, uint64_t receive_ns)
{
    uint32_t count = state->channel_counts[0] < state->channel_counts[1]
                         ? state->channel_counts[0]
                         : state->channel_counts[1];
    const double ch1_limit = 5.0 * state->options.ch1_vdiv_num / state->options.ch1_vdiv_den;
    const double ch2_limit = 5.0 * state->options.ch2_vdiv_num / state->options.ch2_vdiv_den;

    while (count > 0) {
        uint32_t available = state->options.frame_samples - state->frame_fill;
        uint32_t take = count < available ? count : available;

        for (uint32_t index = 0; index < take; index++) {
            float ch1 = state->channel_values[0][index];
            float ch2 = state->channel_values[1][index];
            uint32_t target = (state->frame_fill + index) * BRIDGE_CHANNELS;

            state->frame_values[target] = ch1;
            state->frame_values[target + 1] = ch2;
            if (ch1 <= -ch1_limit || ch1 >= ch1_limit ||
                ch2 <= -ch2_limit || ch2 >= ch2_limit)
                state->frame_flags |= BRIDGE_FLAG_CLIPPED;
        }
        state->frame_fill += take;
        state->channel_counts[0] -= take;
        state->channel_counts[1] -= take;
        if (state->channel_counts[0] > 0)
            memmove(state->channel_values[0], state->channel_values[0] + take,
                    (size_t)state->channel_counts[0] * sizeof(float));
        if (state->channel_counts[1] > 0)
            memmove(state->channel_values[1], state->channel_values[1] + take,
                    (size_t)state->channel_counts[1] * sizeof(float));
        count = state->channel_counts[0] < state->channel_counts[1]
                    ? state->channel_counts[0]
                    : state->channel_counts[1];
        if (state->frame_fill == state->options.frame_samples && emit_frame(state, receive_ns) != 0)
            return -1;
    }
    state->analog_pairs++;
    return 0;
}

static int extend_sliding_limit(struct bridge_state *state)
{
    uint64_t now_ns = monotonic_ns();
    uint64_t elapsed_msec;
    uint64_t deadline_msec;

    if (now_ns < state->session_start_ns)
        return SR_ERR;
    elapsed_msec = (now_ns - state->session_start_ns) / UINT64_C(1000000);
    if (elapsed_msec > UINT64_MAX - state->options.callback_msec)
        return SR_ERR;
    deadline_msec = elapsed_msec + state->options.callback_msec;

    return sr_config_set(state->device, NULL, SR_CONF_LIMIT_MSEC,
                         g_variant_new_uint64(deadline_msec));
}

static int channel_index(const struct sr_datafeed_analog *analog)
{
    const GSList *channels;
    const struct sr_channel *channel;

    if (analog == NULL || analog->meaning == NULL)
        return -1;
    channels = analog->meaning->channels;
    if (channels == NULL || channels->next != NULL || channels->data == NULL ||
        analog->meaning->mq != SR_MQ_VOLTAGE || analog->meaning->unit != SR_UNIT_VOLT)
        return -1;
    channel = channels->data;
    if (channel->type != SR_CHANNEL_ANALOG)
        return -1;
    return channel->index;
}

static void datafeed_callback(const struct sr_dev_inst *device,
                              const struct sr_datafeed_packet *packet,
                              void *callback_data)
{
    struct bridge_state *state = callback_data;
    const struct sr_datafeed_analog *analog;
    int index;

    (void)device;
    if (packet == NULL)
        return;
    if (packet->type == SR_DF_ANALOG) {
        if (state->saw_end || state->write_failed)
            return;
        if (stop_requested) {
            sr_session_stop(state->session);
            return;
        }
        analog = packet->payload;
        index = channel_index(analog);
        if (index < 0 || index >= BRIDGE_CHANNELS) {
            fprintf(stderr, "sigrok returned an unexpected analog channel index: %d\n", index);
            state->write_failed = 1;
            sr_session_stop(state->session);
            return;
        }
        if (analog->num_samples == 0 || analog->num_samples > MAX_CHANNEL_BUFFER_SAMPLES ||
            (size_t)analog->num_samples > SIZE_MAX / sizeof(float)) {
            fprintf(stderr, "sigrok returned an invalid analog packet size\n");
            state->write_failed = 1;
            sr_session_stop(state->session);
            return;
        }
        if (state->channel_counts[index] > MAX_CHANNEL_BUFFER_SAMPLES - analog->num_samples) {
            fprintf(stderr, "sigrok channel buffer exceeded safety limit\n");
            state->write_failed = 1;
            sr_session_stop(state->session);
            return;
        }
        uint32_t required = state->channel_counts[index] + (uint32_t)analog->num_samples;
        size_t allocation = (size_t)required * sizeof(float);
        float *resized = realloc(state->channel_values[index], allocation);
        if (resized == NULL) {
            fprintf(stderr, "cannot allocate sigrok channel buffer\n");
            state->write_failed = 1;
            sr_session_stop(state->session);
            return;
        }
        state->channel_values[index] = resized;
        if (sr_analog_to_float(
                analog, state->channel_values[index] + state->channel_counts[index]) != SR_OK) {
            fprintf(stderr, "cannot convert sigrok analog packet to float32\n");
            state->write_failed = 1;
            sr_session_stop(state->session);
            return;
        }
        state->channel_counts[index] = required;
        /* libsigrok may deliver channels in either order and with different
         * packet boundaries.  Pair the common prefix and retain any remainder
         * until the other channel catches up. */
        if (state->channel_counts[0] > 0 && state->channel_counts[1] > 0) {
            uint64_t receive_ns = monotonic_ns();
            if (append_channel_pair(state, receive_ns) != 0) {
                if (!state->write_failed)
                    fprintf(stderr, "cannot append paired sigrok samples\n");
                state->write_failed = 1;
                sr_session_stop(state->session);
                return;
            }
            if (extend_sliding_limit(state) != SR_OK) {
                fprintf(stderr, "cannot extend the continuous sigrok session limit\n");
                state->write_failed = 1;
                sr_session_stop(state->session);
                return;
            }
        }
        if (stop_requested)
            sr_session_stop(state->session);
    } else if (packet->type == SR_DF_END) {
        state->saw_end = 1;
    } else if (stop_requested) {
        sr_session_stop(state->session);
    }
}

static struct sr_dev_driver *find_hantek_driver(struct sr_context *context)
{
    struct sr_dev_driver **drivers = sr_driver_list(context);

    for (; *drivers != NULL; drivers++) {
        if (strcmp((*drivers)->name, "hantek-6xxx") == 0)
            return *drivers;
    }
    return NULL;
}

static int enable_two_channels(struct sr_dev_inst *device)
{
    GSList *channels = sr_dev_inst_channels_get(device);
    unsigned int enabled = 0;

    for (GSList *item = channels; item != NULL; item = item->next) {
        struct sr_channel *channel = item->data;
        gboolean should_enable = channel->index >= 0 && channel->index < BRIDGE_CHANNELS;
        if (sr_dev_channel_enable(channel, should_enable) != SR_OK)
            return SR_ERR;
        if (should_enable)
            enabled++;
    }
    return enabled == BRIDGE_CHANNELS ? SR_OK : SR_ERR;
}

static int set_vdiv(struct sr_dev_inst *device, int channel_index_value,
                    uint64_t numerator, uint64_t denominator)
{
    GSList *groups = sr_dev_inst_channel_groups_get(device);

    for (GSList *item = groups; item != NULL; item = item->next) {
        struct sr_channel_group *group = item->data;
        if (group->channels != NULL) {
            struct sr_channel *channel = group->channels->data;
            if (channel != NULL && channel->index == channel_index_value) {
                return sr_config_set(device, group, SR_CONF_VDIV,
                                     g_variant_new("(tt)", numerator, denominator));
            }
        }
    }
    return SR_ERR_ARG;
}

static uint64_t parse_positive(const char *text, const char *name)
{
    char *end = NULL;
    unsigned long long value;

    errno = 0;
    if (text[0] == '-') {
        fprintf(stderr, "invalid %s: %s\n", name, text);
        exit(EXIT_FAILURE);
    }
    value = strtoull(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0' || value == 0
#if ULLONG_MAX > UINT64_MAX
        || value > UINT64_MAX
#endif
    ) {
        fprintf(stderr, "invalid %s: %s\n", name, text);
        exit(EXIT_FAILURE);
    }
    return (uint64_t)value;
}

static void print_usage(const char *program)
{
    fprintf(stderr,
            "Usage: %s RATE_HZ FRAME_SAMPLES CALLBACK_MS CH1_NUM CH1_DEN CH2_NUM CH2_DEN\n",
            program);
}

int main(int argc, char **argv)
{
    struct bridge_state state = {0};
    struct sr_context *context = NULL;
    struct sr_dev_driver *driver = NULL;
    struct sr_dev_inst *device = NULL;
    struct sr_session *session = NULL;
    GSList *devices = NULL;
    GVariant *actual_rate = NULL;
    int result;
    int status = EXIT_FAILURE;

    if (argc != 8) {
        print_usage(argv[0]);
        return EXIT_FAILURE;
    }
    state.options.sample_rate_hz = parse_positive(argv[1], "sample rate");
    if (state.options.sample_rate_hz > HANTEK_6022BE_MAX_SAMPLE_RATE_HZ ||
        !is_hantek_6022be_sample_rate(state.options.sample_rate_hz)) {
        fprintf(stderr, "sample rate is not supported by the Hantek 6022BE driver\n");
        goto cleanup;
    }
    uint64_t frame_samples = parse_positive(argv[2], "frame samples");
    if (frame_samples > MAX_BRIDGE_FRAME_SAMPLES ||
        frame_samples > UINT32_MAX / (BRIDGE_CHANNELS * sizeof(float))) {
        fprintf(stderr, "frame samples exceed the BridgeFrameV1 payload limit\n");
        goto cleanup;
    }
    state.options.frame_samples = (uint32_t)frame_samples;
    state.options.callback_msec = parse_positive(argv[3], "callback interval");
    if (state.options.callback_msec < 10 || state.options.callback_msec > 1000) {
        fprintf(stderr, "callback interval must be between 10 and 1000 ms\n");
        goto cleanup;
    }
    state.options.ch1_vdiv_num = parse_positive(argv[4], "CH1 volts/div numerator");
    state.options.ch1_vdiv_den = parse_positive(argv[5], "CH1 volts/div denominator");
    state.options.ch2_vdiv_num = parse_positive(argv[6], "CH2 volts/div numerator");
    state.options.ch2_vdiv_den = parse_positive(argv[7], "CH2 volts/div denominator");
    state.frame_values = malloc(
        state.options.frame_samples * BRIDGE_CHANNELS * sizeof(float));
    if (state.frame_values == NULL) {
        fprintf(stderr, "cannot allocate output frame buffer\n");
        goto cleanup;
    }
    signal(SIGINT, handle_signal);
    signal(SIGTERM, handle_signal);
    /* Make a closed Python stdout pipe a diagnosable EPIPE from write_all(). */
    signal(SIGPIPE, SIG_IGN);

    if ((result = sr_init(&context)) != SR_OK) {
        fprintf(stderr, "sr_init failed: %d\n", result);
        goto cleanup;
    }
    sr_log_loglevel_set(SR_LOG_WARN);
    driver = find_hantek_driver(context);
    if (driver == NULL || sr_driver_init(context, driver) != SR_OK) {
        fprintf(stderr, "libsigrok hantek-6xxx driver is unavailable\n");
        goto cleanup;
    }
    devices = sr_driver_scan(driver, NULL);
    if (devices == NULL) {
        fprintf(stderr, "no Hantek 6022BE found through libsigrok\n");
        goto cleanup_driver;
    }
    device = devices->data;
    state.device = device;
    if ((result = sr_dev_open(device)) != SR_OK) {
        fprintf(stderr, "cannot open Hantek 6022BE through libsigrok: %d\n", result);
        goto cleanup_driver;
    }
    result = enable_two_channels(device);
    if (result != SR_OK) {
        fprintf(stderr, "cannot enable two sigrok channels: %d\n", result);
        goto cleanup_device;
    }
    result = sr_config_set(device, NULL, SR_CONF_SAMPLERATE,
                           g_variant_new_uint64(state.options.sample_rate_hz));
    if (result != SR_OK) {
        fprintf(stderr, "cannot set sigrok sample rate: %d\n", result);
        goto cleanup_device;
    }
    result = set_vdiv(device, 0, state.options.ch1_vdiv_num, state.options.ch1_vdiv_den);
    if (result != SR_OK) {
        fprintf(stderr, "cannot set CH1 volts/div through sigrok: %d\n", result);
        goto cleanup_device;
    }
    result = set_vdiv(device, 1, state.options.ch2_vdiv_num, state.options.ch2_vdiv_den);
    if (result != SR_OK) {
        fprintf(stderr, "cannot set CH2 volts/div through sigrok: %d\n", result);
        goto cleanup_device;
    }
    result = sr_config_set(device, NULL, SR_CONF_LIMIT_MSEC,
                           g_variant_new_uint64(state.options.callback_msec));
    if (result != SR_OK) {
        fprintf(stderr, "cannot set the sigrok sliding time limit: %d\n", result);
        goto cleanup_device;
    }
    result = sr_config_commit(device);
    if (result != SR_OK) {
        fprintf(stderr, "cannot commit sigrok capture settings: %d\n", result);
        goto cleanup_device;
    }
    if (sr_config_get(driver, device, NULL, SR_CONF_SAMPLERATE, &actual_rate) != SR_OK) {
        fprintf(stderr, "cannot read back the actual sigrok sample rate\n");
        goto cleanup_device;
    }
    state.options.sample_rate_hz = g_variant_get_uint64(actual_rate);
    g_variant_unref(actual_rate);
    actual_rate = NULL;

    if (sr_session_new(context, &session) != SR_OK ||
        sr_session_dev_add(session, device) != SR_OK ||
        sr_session_datafeed_callback_add(session, datafeed_callback, &state) != SR_OK) {
        fprintf(stderr, "cannot create the sigrok capture session\n");
        goto cleanup_session;
    }
    state.session = session;
    state.session_start_ns = monotonic_ns();
    if (state.session_start_ns == 0) {
        fprintf(stderr, "cannot read a monotonic host timestamp\n");
        goto cleanup_session;
    }
    fprintf(stderr,
            "sigrok bridge ready: rate=%" PRIu64 " frame=%u callback=%" PRIu64 "ms\n",
            state.options.sample_rate_hz, state.options.frame_samples,
            state.options.callback_msec);
    if (sr_session_start(session) != SR_OK || sr_session_run(session) != SR_OK) {
        fprintf(stderr, "sigrok capture session failed\n");
        goto cleanup_session;
    }
    if (state.write_failed || (!stop_requested && !state.saw_end))
        goto cleanup_session;
    status = EXIT_SUCCESS;

cleanup_session:
    if (session != NULL)
        sr_session_destroy(session);
cleanup_device:
    if (device != NULL)
        sr_dev_close(device);
cleanup_driver:
    if (driver != NULL)
        sr_dev_clear(driver);
cleanup:
    if (actual_rate != NULL)
        g_variant_unref(actual_rate);
    if (context != NULL)
        sr_exit(context);
    fprintf(stderr,
            "sigrok bridge stopped: frames=%" PRIu64 " analog_pairs=%" PRIu64 " status=%d\n",
            state.frames_written, state.analog_pairs, status);
    free(state.channel_values[0]);
    free(state.channel_values[1]);
    free(state.frame_values);
    return status;
}
