/*
 * Board-only throughput probe for Hantek 6022BE through libsigrok.
 *
 * It intentionally discards sample values.  Counting samples in the
 * libsigrok callback avoids the GUI, CSV, and text formatting overhead of
 * PulseView and sigrok-cli, so the reported rate is the host-side delivered
 * analog sample rate.
 */

#include <errno.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include <libsigrok/libsigrok.h>

struct measurement {
    uint64_t requested_rate_hz;
    uint64_t duration_ms;
    unsigned int active_channels;
    uint64_t sample_values;
    uint64_t callbacks;
    uint64_t session_start_ns;
    uint64_t first_data_ns;
    uint64_t last_data_ns;
    uint64_t end_ns;
    uint64_t max_callback_gap_ns;
    int saw_end;
};

static uint64_t monotonic_ns(void)
{
    struct timespec timestamp;

    if (clock_gettime(CLOCK_MONOTONIC, &timestamp) != 0) {
        return 0;
    }
    return (uint64_t)timestamp.tv_sec * UINT64_C(1000000000) + timestamp.tv_nsec;
}

static void datafeed_callback(const struct sr_dev_inst *sdi,
                              const struct sr_datafeed_packet *packet,
                              void *cb_data)
{
    const struct sr_datafeed_analog *analog;
    struct measurement *measurement = cb_data;
    uint64_t now_ns;

    (void)sdi;
    now_ns = monotonic_ns();

    if (packet->type == SR_DF_ANALOG) {
        analog = packet->payload;
        measurement->sample_values += analog->num_samples;
        measurement->callbacks++;
        if (measurement->first_data_ns == 0) {
            measurement->first_data_ns = now_ns;
        }
        if (measurement->last_data_ns != 0 && now_ns > measurement->last_data_ns) {
            uint64_t gap_ns = now_ns - measurement->last_data_ns;
            if (gap_ns > measurement->max_callback_gap_ns) {
                measurement->max_callback_gap_ns = gap_ns;
            }
        }
        measurement->last_data_ns = now_ns;
    } else if (packet->type == SR_DF_END) {
        measurement->end_ns = now_ns;
        measurement->saw_end = 1;
    }
}

static struct sr_dev_driver *find_hantek_driver(struct sr_context *context)
{
    struct sr_dev_driver **drivers;

    drivers = sr_driver_list(context);
    for (; *drivers != NULL; drivers++) {
        if (strcmp((*drivers)->name, "hantek-6xxx") == 0) {
            return *drivers;
        }
    }
    return NULL;
}

static int configure_channels(struct sr_dev_inst *device, unsigned int active_channels)
{
    GSList *channels;
    GSList *item;
    unsigned int enabled_count = 0;

    channels = sr_dev_inst_channels_get(device);
    for (item = channels; item != NULL; item = item->next) {
        struct sr_channel *channel = item->data;
        gboolean enabled = enabled_count < active_channels;

        if (sr_dev_channel_enable(channel, enabled) != SR_OK) {
            fprintf(stderr, "cannot set channel %d state\n", channel->index);
            return SR_ERR;
        }
        if (enabled) {
            enabled_count++;
        }
    }
    if (enabled_count != active_channels) {
        fprintf(stderr, "requested %u channels, device exposes %u\n",
                active_channels, enabled_count);
        return SR_ERR_ARG;
    }
    return SR_OK;
}

static void print_usage(const char *program)
{
    fprintf(stderr, "Usage: %s RATE_HZ CHANNELS DURATION_MS\n", program);
    fprintf(stderr, "Example: %s 15000000 2 10000\n", program);
}

int main(int argc, char **argv)
{
    struct sr_context *context = NULL;
    struct sr_dev_driver *driver;
    struct sr_dev_inst *device;
    struct sr_session *session = NULL;
    struct measurement measurement = {0};
    GSList *devices;
    int status = EXIT_FAILURE;
    int result;
    double elapsed_seconds;
    double delivery_seconds;
    double per_channel_rate_hz;
    double delivered_analog_values_per_second;
    double delivery_analog_values_per_second;

    if (argc != 4) {
        print_usage(argv[0]);
        return EXIT_FAILURE;
    }

    errno = 0;
    measurement.requested_rate_hz = strtoull(argv[1], NULL, 10);
    measurement.active_channels = (unsigned int)strtoul(argv[2], NULL, 10);
    measurement.duration_ms = strtoull(argv[3], NULL, 10);
    if (errno != 0 || measurement.requested_rate_hz == 0 ||
        measurement.duration_ms == 0 || measurement.active_channels < 1 ||
        measurement.active_channels > 2) {
        print_usage(argv[0]);
        return EXIT_FAILURE;
    }

    if ((result = sr_init(&context)) != SR_OK) {
        fprintf(stderr, "sr_init failed: %d\n", result);
        goto cleanup;
    }
    sr_log_loglevel_set(SR_LOG_WARN);

    driver = find_hantek_driver(context);
    if (driver == NULL) {
        fprintf(stderr, "hantek-6xxx driver is unavailable\n");
        goto cleanup;
    }
    if ((result = sr_driver_init(context, driver)) != SR_OK) {
        fprintf(stderr, "driver initialization failed: %d\n", result);
        goto cleanup;
    }
    devices = sr_driver_scan(driver, NULL);
    if (devices == NULL) {
        fprintf(stderr, "no Hantek 6022BE found through sigrok\n");
        goto cleanup_driver;
    }
    device = devices->data;
    if ((result = sr_dev_open(device)) != SR_OK) {
        fprintf(stderr, "cannot open Hantek 6022BE: %d\n", result);
        goto cleanup_driver;
    }
    if ((result = configure_channels(device, measurement.active_channels)) != SR_OK) {
        goto cleanup_device;
    }
    if ((result = sr_config_set(device, NULL, SR_CONF_SAMPLERATE,
                                g_variant_new_uint64(measurement.requested_rate_hz))) != SR_OK ||
        (result = sr_config_set(device, NULL, SR_CONF_LIMIT_MSEC,
                                g_variant_new_uint64(measurement.duration_ms))) != SR_OK ||
        (result = sr_config_commit(device)) != SR_OK) {
        fprintf(stderr, "cannot configure sampling: %d\n", result);
        goto cleanup_device;
    }
    if ((result = sr_session_new(context, &session)) != SR_OK ||
        (result = sr_session_dev_add(session, device)) != SR_OK ||
        (result = sr_session_datafeed_callback_add(session, datafeed_callback, &measurement)) != SR_OK) {
        fprintf(stderr, "cannot start sigrok session: %d\n", result);
        goto cleanup_session;
    }
    measurement.session_start_ns = monotonic_ns();
    if ((result = sr_session_start(session)) != SR_OK) {
        fprintf(stderr, "cannot start sigrok session: %d\n", result);
        goto cleanup_session;
    }
    if ((result = sr_session_run(session)) != SR_OK) {
        fprintf(stderr, "sigrok session failed: %d\n", result);
        goto cleanup_session;
    }
    if (!measurement.saw_end || measurement.first_data_ns == 0 ||
        measurement.end_ns <= measurement.first_data_ns) {
        fprintf(stderr, "sigrok session ended without measurable analog data\n");
        goto cleanup_session;
    }

    elapsed_seconds = (measurement.end_ns - measurement.session_start_ns) / 1e9;
    delivery_seconds = (measurement.end_ns - measurement.first_data_ns) / 1e9;
    per_channel_rate_hz = (measurement.sample_values / measurement.active_channels) / elapsed_seconds;
    delivered_analog_values_per_second = measurement.sample_values / elapsed_seconds;
    delivery_analog_values_per_second = measurement.sample_values / delivery_seconds;
    printf("{\"requested_rate_hz\":%" PRIu64
           ",\"active_channels\":%u,\"duration_requested_ms\":%" PRIu64
           ",\"session_elapsed_s\":%.6f,\"delivery_elapsed_s\":%.6f,\"sample_values\":%" PRIu64
           ",\"per_channel_samples\":%.0f,\"effective_per_channel_rate_hz\":%.3f"
           ",\"delivered_analog_values_per_s\":%.3f,\"delivery_analog_values_per_s\":%.3f,\"callbacks\":%" PRIu64
           ",\"max_callback_gap_ms\":%.3f}\n",
           measurement.requested_rate_hz, measurement.active_channels,
           measurement.duration_ms, elapsed_seconds, delivery_seconds, measurement.sample_values,
           measurement.sample_values / (double)measurement.active_channels,
           per_channel_rate_hz, delivered_analog_values_per_second,
           delivery_analog_values_per_second, measurement.callbacks,
           measurement.max_callback_gap_ns / 1e6);
    status = EXIT_SUCCESS;

cleanup_session:
    if (session != NULL) {
        sr_session_destroy(session);
    }
cleanup_device:
    sr_dev_close(device);
cleanup_driver:
    sr_dev_clear(driver);
cleanup:
    if (context != NULL) {
        sr_exit(context);
    }
    return status;
}
