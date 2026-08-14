/*
 * FFTW3 CPU reference for the batched RTL-SDR IQ NPU spectrum benchmark.
 *
 * Input is one or more already de-meaned [16, 2, 1024] float32 batches. Each
 * timing executes all sixteen full complex FFTs, applies the Hann window,
 * computes normalized power, and reorders every output with fftshift.
 */

#define _POSIX_C_SOURCE 200809L

#include <errno.h>
#include <fftw3.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

enum {
    BATCH_SIZE = 16,
    IQ_CHANNELS = 2,
    WINDOW_SAMPLES = 1024,
};

static const double PI = 3.14159265358979323846;

static double monotonic_ms(void) {
    struct timespec timestamp;
    clock_gettime(CLOCK_MONOTONIC_RAW, &timestamp);
    return (double)timestamp.tv_sec * 1000.0 + (double)timestamp.tv_nsec / 1000000.0;
}

static int compare_double(const void *left, const void *right) {
    const double a = *(const double *)left;
    const double b = *(const double *)right;
    return (a > b) - (a < b);
}

static double percentile(double *values, int count, double fraction) {
    qsort(values, (size_t)count, sizeof(*values), compare_double);
    const double position = fraction * (double)(count - 1);
    const int lower = (int)floor(position);
    const int upper = (int)ceil(position);
    const double weight = position - (double)lower;
    return values[lower] * (1.0 - weight) + values[upper] * weight;
}

static void summarize(const char *label, double *values, int count) {
    double total = 0.0;
    double minimum = values[0];
    double maximum = values[0];
    for (int index = 0; index < count; ++index) {
        const double value = values[index];
        total += value;
        if (value < minimum) {
            minimum = value;
        }
        if (value > maximum) {
            maximum = value;
        }
    }
    double *sorted = malloc((size_t)count * sizeof(*sorted));
    if (sorted == NULL) {
        fprintf(stderr, "cannot allocate timing summary\n");
        exit(2);
    }
    memcpy(sorted, values, (size_t)count * sizeof(*sorted));
    const double p50 = percentile(sorted, count, 0.50);
    memcpy(sorted, values, (size_t)count * sizeof(*sorted));
    const double p95 = percentile(sorted, count, 0.95);
    free(sorted);
    printf("\"%s\":{\"mean_ms\":%.9f,\"p50_ms\":%.9f,\"p95_ms\":%.9f,\"min_ms\":%.9f,\"max_ms\":%.9f}",
           label,
           total / (double)count,
           p50,
           p95,
           minimum,
           maximum);
}

static void process_batch(
    const float *source,
    const float *hann,
    float normalization,
    fftwf_complex *work,
    fftwf_complex *spectrum,
    fftwf_plan plan,
    float *power
) {
    for (int window = 0; window < BATCH_SIZE; ++window) {
        const size_t base = (size_t)window * IQ_CHANNELS * WINDOW_SAMPLES;
        for (int sample = 0; sample < WINDOW_SAMPLES; ++sample) {
            work[sample][0] = source[base + (size_t)sample] * hann[sample];
            work[sample][1] = source[base + WINDOW_SAMPLES + (size_t)sample] * hann[sample];
        }
        fftwf_execute(plan);
        for (int bin = 0; bin < WINDOW_SAMPLES; ++bin) {
            const int shifted_bin = (bin + WINDOW_SAMPLES / 2) % WINDOW_SAMPLES;
            const float real = spectrum[shifted_bin][0] / normalization;
            const float imaginary = spectrum[shifted_bin][1] / normalization;
            power[(size_t)window * WINDOW_SAMPLES + (size_t)bin] = real * real + imaginary * imaginary;
        }
    }
}

int main(int argc, char **argv) {
    if (argc != 5) {
        fprintf(stderr, "usage: %s INPUT.raw OUTPUT.raw WARMUP ITERATIONS\n", argv[0]);
        return 2;
    }
    const char *input_path = argv[1];
    const char *output_path = argv[2];
    const int warmup = atoi(argv[3]);
    const int iterations = atoi(argv[4]);
    if (warmup < 0 || iterations <= 0) {
        fprintf(stderr, "warmup must be non-negative and iterations positive\n");
        return 2;
    }

    FILE *input = fopen(input_path, "rb");
    if (input == NULL) {
        fprintf(stderr, "cannot open %s: %s\n", input_path, strerror(errno));
        return 2;
    }
    if (fseek(input, 0, SEEK_END) != 0) {
        fprintf(stderr, "cannot seek %s\n", input_path);
        fclose(input);
        return 2;
    }
    const long input_bytes = ftell(input);
    const size_t floats_per_batch = (size_t)BATCH_SIZE * IQ_CHANNELS * WINDOW_SAMPLES;
    if (input_bytes <= 0 || input_bytes % (long)(floats_per_batch * sizeof(float)) != 0) {
        fprintf(stderr, "input must contain an exact number of [16, 2, 1024] float32 batches\n");
        fclose(input);
        return 2;
    }
    const int input_batches = (int)(input_bytes / (long)(floats_per_batch * sizeof(float)));
    if (fseek(input, 0, SEEK_SET) != 0) {
        fprintf(stderr, "cannot rewind %s\n", input_path);
        fclose(input);
        return 2;
    }
    float *source = fftwf_malloc((size_t)input_batches * floats_per_batch * sizeof(*source));
    if (source == NULL || fread(source, sizeof(*source), (size_t)input_batches * floats_per_batch, input) != (size_t)input_batches * floats_per_batch) {
        fprintf(stderr, "cannot read %s\n", input_path);
        fclose(input);
        fftwf_free(source);
        return 2;
    }
    fclose(input);

    float *hann = malloc((size_t)WINDOW_SAMPLES * sizeof(*hann));
    fftwf_complex *work = fftwf_malloc((size_t)WINDOW_SAMPLES * sizeof(*work));
    fftwf_complex *spectrum = fftwf_malloc((size_t)WINDOW_SAMPLES * sizeof(*spectrum));
    float *power = malloc((size_t)BATCH_SIZE * WINDOW_SAMPLES * sizeof(*power));
    double *timings = malloc((size_t)iterations * sizeof(*timings));
    if (hann == NULL || work == NULL || spectrum == NULL || power == NULL || timings == NULL) {
        fprintf(stderr, "cannot allocate benchmark buffers\n");
        return 2;
    }
    double window_power = 0.0;
    for (int sample = 0; sample < WINDOW_SAMPLES; ++sample) {
        const float value = 0.5f - 0.5f * cosf((float)(2.0 * PI * (double)sample / (double)(WINDOW_SAMPLES - 1)));
        hann[sample] = value;
        window_power += (double)value * value;
    }
    const float normalization = (float)WINDOW_SAMPLES * sqrtf((float)(window_power / (double)WINDOW_SAMPLES));
    const double plan_started = monotonic_ms();
    fftwf_plan plan = fftwf_plan_dft_1d(WINDOW_SAMPLES, work, spectrum, FFTW_FORWARD, FFTW_MEASURE);
    const double plan_ms = monotonic_ms() - plan_started;
    if (plan == NULL) {
        fprintf(stderr, "FFTW plan creation failed\n");
        return 2;
    }

    for (int iteration = 0; iteration < warmup; ++iteration) {
        process_batch(
            source + (size_t)(iteration % input_batches) * floats_per_batch,
            hann,
            normalization,
            work,
            spectrum,
            plan,
            power
        );
    }
    for (int iteration = 0; iteration < iterations; ++iteration) {
        const double started = monotonic_ms();
        process_batch(
            source + (size_t)(iteration % input_batches) * floats_per_batch,
            hann,
            normalization,
            work,
            spectrum,
            plan,
            power
        );
        timings[iteration] = monotonic_ms() - started;
    }
    process_batch(source, hann, normalization, work, spectrum, plan, power);
    FILE *output = fopen(output_path, "wb");
    if (output == NULL || fwrite(power, sizeof(*power), (size_t)BATCH_SIZE * WINDOW_SAMPLES, output) != (size_t)BATCH_SIZE * WINDOW_SAMPLES) {
        fprintf(stderr, "cannot write %s\n", output_path);
        if (output != NULL) {
            fclose(output);
        }
        return 2;
    }
    fclose(output);

    printf("{\"input_batches\":%d,\"batch_size\":%d,\"window_samples\":%d,\"fftw_threads\":1,\"iterations\":%d,\"fftw_plan_ms\":%.9f,",
           input_batches,
           BATCH_SIZE,
           WINDOW_SAMPLES,
           iterations,
           plan_ms);
    summarize("fftw_full_pipeline", timings, iterations);
    printf("}\n");

    fftwf_destroy_plan(plan);
    fftwf_free(source);
    fftwf_free(work);
    fftwf_free(spectrum);
    free(hann);
    free(power);
    free(timings);
    return 0;
}
