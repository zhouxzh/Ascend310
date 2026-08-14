/*
 * CPU reference for scripts/benchmark_spectrum_efficiency.py.
 *
 * This small C program measures the ARM FFTW3 single-precision implementation
 * without Python FFT-wrapper overhead. It uses the same two-channel,
 * de-meaned, Hann-windowed one-sided power convention as the NPU DFT OM.
 */

#define _POSIX_C_SOURCE 200809L

#include <errno.h>
#include <fftw3.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

enum {
    CHANNELS = 2,
    SAMPLES = 10000,
    BINS = 201,
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

static void prepare_channel(const float *source, float *work, const float *hann) {
    double sum = 0.0;
    for (int sample = 0; sample < SAMPLES; ++sample) {
        sum += (double)source[sample];
    }
    const float mean = (float)(sum / (double)SAMPLES);
    for (int sample = 0; sample < SAMPLES; ++sample) {
        work[sample] = (source[sample] - mean) * hann[sample];
    }
}

static void selected_power(const fftwf_complex *fft, float *power, float scale) {
    for (int bin = 0; bin < BINS; ++bin) {
        const float real = fft[bin][0];
        const float imaginary = fft[bin][1];
        const float one_sided = bin == 0 ? 1.0f : 2.0f;
        power[bin] = one_sided * (real * real + imaginary * imaginary) * scale;
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

    float *source = fftwf_malloc((size_t)CHANNELS * SAMPLES * sizeof(*source));
    float *work = fftwf_malloc((size_t)SAMPLES * sizeof(*work));
    fftwf_complex *fft = fftwf_malloc((size_t)(SAMPLES / 2 + 1) * sizeof(*fft));
    float *hann = malloc((size_t)SAMPLES * sizeof(*hann));
    float *power = malloc((size_t)CHANNELS * BINS * sizeof(*power));
    double *execute_times = malloc((size_t)iterations * sizeof(*execute_times));
    double *pipeline_times = malloc((size_t)iterations * sizeof(*pipeline_times));
    if (source == NULL || work == NULL || fft == NULL || hann == NULL || power == NULL || execute_times == NULL || pipeline_times == NULL) {
        fprintf(stderr, "cannot allocate benchmark buffers\n");
        return 2;
    }

    FILE *input = fopen(input_path, "rb");
    if (input == NULL) {
        fprintf(stderr, "cannot open %s: %s\n", input_path, strerror(errno));
        return 2;
    }
    const size_t expected = (size_t)CHANNELS * SAMPLES;
    if (fread(source, sizeof(*source), expected, input) != expected || fgetc(input) != EOF) {
        fprintf(stderr, "input must contain exactly %zu float32 values\n", expected);
        fclose(input);
        return 2;
    }
    fclose(input);

    double window_power = 0.0;
    for (int sample = 0; sample < SAMPLES; ++sample) {
        const float value = 0.5f - 0.5f * cosf((float)(2.0 * PI * (double)sample / (double)(SAMPLES - 1)));
        hann[sample] = value;
        window_power += (double)value * value;
    }
    const float scale = 1.0f / ((float)SAMPLES * (float)SAMPLES * (float)(window_power / (double)SAMPLES));

    const double plan_started = monotonic_ms();
    fftwf_plan plan = fftwf_plan_dft_r2c_1d(SAMPLES, work, fft, FFTW_MEASURE);
    const double plan_ms = monotonic_ms() - plan_started;
    if (plan == NULL) {
        fprintf(stderr, "FFTW plan creation failed\n");
        return 2;
    }

    for (int iteration = 0; iteration < warmup; ++iteration) {
        for (int channel = 0; channel < CHANNELS; ++channel) {
            prepare_channel(source + (size_t)channel * SAMPLES, work, hann);
            fftwf_execute(plan);
            selected_power(fft, power + (size_t)channel * BINS, scale);
        }
    }

    prepare_channel(source, work, hann);
    for (int iteration = 0; iteration < iterations; ++iteration) {
        const double started = monotonic_ms();
        fftwf_execute(plan);
        fftwf_execute(plan);
        execute_times[iteration] = monotonic_ms() - started;
    }

    for (int iteration = 0; iteration < iterations; ++iteration) {
        const double started = monotonic_ms();
        for (int channel = 0; channel < CHANNELS; ++channel) {
            prepare_channel(source + (size_t)channel * SAMPLES, work, hann);
            fftwf_execute(plan);
            selected_power(fft, power + (size_t)channel * BINS, scale);
        }
        pipeline_times[iteration] = monotonic_ms() - started;
    }

    FILE *output = fopen(output_path, "wb");
    if (output == NULL || fwrite(power, sizeof(*power), (size_t)CHANNELS * BINS, output) != (size_t)CHANNELS * BINS) {
        fprintf(stderr, "cannot write %s\n", output_path);
        if (output != NULL) {
            fclose(output);
        }
        return 2;
    }
    fclose(output);

    printf("{\"samples\":%d,\"channels\":%d,\"bins\":%d,\"iterations\":%d,\"fftw_plan_ms\":%.9f,",
           SAMPLES,
           CHANNELS,
           BINS,
           iterations,
           plan_ms);
    summarize("fftw_execute_two_channel", execute_times, iterations);
    printf(",");
    summarize("fftw_full_pipeline", pipeline_times, iterations);
    printf("}\n");

    fftwf_destroy_plan(plan);
    fftwf_free(source);
    fftwf_free(work);
    fftwf_free(fft);
    free(hann);
    free(power);
    free(execute_times);
    free(pipeline_times);
    return 0;
}
