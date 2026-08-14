/* Explicit generic/NEON/dispatcher benchmark for four representative VOLK kernels. */

#define _POSIX_C_SOURCE 200809L

#include <complex.h>
#include <errno.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <volk/volk.h>

static double elapsed_ms(struct timespec start, struct timespec end)
{
    return (double)(end.tv_sec - start.tv_sec) * 1000.0 +
           (double)(end.tv_nsec - start.tv_nsec) / 1000000.0;
}

static int compare_double(const void* left, const void* right)
{
    const double a = *(const double*)left;
    const double b = *(const double*)right;
    return (a > b) - (a < b);
}

static float percentile(const double* timings, int count, double quantile)
{
    double* sorted = malloc((size_t)count * sizeof(*sorted));
    if (sorted == NULL) {
        return NAN;
    }
    memcpy(sorted, timings, (size_t)count * sizeof(*sorted));
    qsort(sorted, (size_t)count, sizeof(*sorted), compare_double);
    const double position = quantile * (double)(count - 1);
    const int lower = (int)floor(position);
    const int upper = (int)ceil(position);
    const double fraction = position - (double)lower;
    const double value = sorted[lower] * (1.0 - fraction) + sorted[upper] * fraction;
    free(sorted);
    return (float)value;
}

static int read_floats(const char* path, float* destination, size_t count)
{
    FILE* input = fopen(path, "rb");
    if (input == NULL) {
        fprintf(stderr, "unable to open %s: %s\n", path, strerror(errno));
        return 0;
    }
    const size_t read_count = fread(destination, sizeof(*destination), count, input);
    const int trailing = fgetc(input);
    fclose(input);
    if (read_count != count || trailing != EOF) {
        fprintf(stderr, "input size mismatch for %s\n", path);
        return 0;
    }
    return 1;
}

static int write_floats(const char* path, const float* values, size_t count)
{
    FILE* output = fopen(path, "wb");
    if (output == NULL) {
        fprintf(stderr, "unable to open %s: %s\n", path, strerror(errno));
        return 0;
    }
    const size_t written = fwrite(values, sizeof(*values), count, output);
    fclose(output);
    return written == count;
}

static volk_func_desc_t kernel_func_desc(const char* kernel)
{
    if (strcmp(kernel, "magnitude_squared") == 0) {
        return volk_32fc_magnitude_squared_32f_get_func_desc();
    }
    if (strcmp(kernel, "multiply_conjugate") == 0) {
        return volk_32fc_x2_multiply_conjugate_32fc_get_func_desc();
    }
    if (strcmp(kernel, "dot_product") == 0) {
        return volk_32f_x2_dot_prod_32f_get_func_desc();
    }
    return volk_32fc_x2_conjugate_dot_prod_32fc_get_func_desc();
}

static int implementation_available(volk_func_desc_t desc, const char* implementation)
{
    for (size_t index = 0; index < desc.n_impls; ++index) {
        if (strcmp(desc.impl_names[index], implementation) == 0) {
            return 1;
        }
    }
    return 0;
}

static void print_json_implementation_names(volk_func_desc_t desc)
{
    printf("[");
    for (size_t index = 0; index < desc.n_impls; ++index) {
        printf(index == 0 ? "\"%s\"" : ",\"%s\"", desc.impl_names[index]);
    }
    printf("]");
}

static void run_kernel(const char* kernel,
                       const char* implementation,
                       unsigned int batch_size,
                       unsigned int length,
                       const float* planar,
                       lv_32fc_t* a,
                       lv_32fc_t* b,
                       float* magnitude,
                       float* dot,
                       lv_32fc_t* complex_output,
                       lv_32fc_t* complex_dot)
{
    const int dispatcher = strcmp(implementation, "dispatcher") == 0;
    for (unsigned int batch = 0; batch < batch_size; ++batch) {
        const size_t base = (size_t)batch * length;
        if (strcmp(kernel, "magnitude_squared") == 0) {
            if (dispatcher) {
                volk_32fc_magnitude_squared_32f(magnitude + base, a + base, length);
            } else {
                volk_32fc_magnitude_squared_32f_manual(
                    magnitude + base, a + base, length, implementation);
            }
        } else if (strcmp(kernel, "multiply_conjugate") == 0) {
            if (dispatcher) {
                volk_32fc_x2_multiply_conjugate_32fc(
                    complex_output + base, a + base, b + base, length);
            } else {
                volk_32fc_x2_multiply_conjugate_32fc_manual(
                    complex_output + base, a + base, b + base, length, implementation);
            }
        } else if (strcmp(kernel, "dot_product") == 0) {
            const float* x = planar + (size_t)batch * 2U * length;
            const float* y = x + length;
            if (dispatcher) {
                volk_32f_x2_dot_prod_32f(dot + batch, x, y, length);
            } else {
                volk_32f_x2_dot_prod_32f_manual(dot + batch, x, y, length, implementation);
            }
        } else if (strcmp(kernel, "conjugate_dot_product") == 0) {
            if (dispatcher) {
                volk_32fc_x2_conjugate_dot_prod_32fc(
                    complex_dot + batch, a + base, b + base, length);
            } else {
                volk_32fc_x2_conjugate_dot_prod_32fc_manual(
                    complex_dot + batch, a + base, b + base, length, implementation);
            }
        } else {
            /* main validates the kernel name before this function is reached. */
            return;
        }
    }
}

int main(int argc, char** argv)
{
    if (argc != 9) {
        fprintf(stderr,
                "usage: %s KERNEL IMPLEMENTATION INPUT OUTPUT BATCH LENGTH WARMUP ITERATIONS\n",
                argv[0]);
        return 2;
    }
    const char* kernel = argv[1];
    const char* implementation = argv[2];
    const char* input_path = argv[3];
    const char* output_path = argv[4];
    const unsigned int batch_size = (unsigned int)strtoul(argv[5], NULL, 10);
    const unsigned int length = (unsigned int)strtoul(argv[6], NULL, 10);
    const int warmup = atoi(argv[7]);
    const int iterations = atoi(argv[8]);
    const int four_channels = strcmp(kernel, "multiply_conjugate") == 0 ||
                              strcmp(kernel, "conjugate_dot_product") == 0;
    if (batch_size == 0 || length == 0 || warmup < 0 || iterations <= 0) {
        fprintf(stderr, "invalid benchmark dimensions or iteration counts\n");
        return 2;
    }
    if (strcmp(implementation, "generic") != 0 && strcmp(implementation, "neon") != 0 &&
        strcmp(implementation, "dispatcher") != 0) {
        fprintf(stderr, "implementation must be generic, neon, or dispatcher\n");
        return 2;
    }
    if (strcmp(kernel, "magnitude_squared") != 0 &&
        strcmp(kernel, "multiply_conjugate") != 0 &&
        strcmp(kernel, "dot_product") != 0 &&
        strcmp(kernel, "conjugate_dot_product") != 0) {
        fprintf(stderr, "unknown VOLK kernel: %s\n", kernel);
        return 2;
    }
    const volk_func_desc_t descriptor = kernel_func_desc(kernel);
    if (strcmp(implementation, "dispatcher") != 0 &&
        !implementation_available(descriptor, implementation)) {
        fprintf(stderr,
                "VOLK kernel %s does not expose requested implementation %s\n",
                kernel,
                implementation);
        return 2;
    }

    const size_t points = (size_t)batch_size * length;
    const size_t input_count = points * (size_t)(four_channels ? 4 : 2);
    const size_t alignment = volk_get_alignment();
    float* planar = volk_malloc(input_count * sizeof(*planar), alignment);
    lv_32fc_t* a = volk_malloc(points * sizeof(*a), alignment);
    lv_32fc_t* b = volk_malloc(points * sizeof(*b), alignment);
    float* magnitude = volk_malloc(points * sizeof(*magnitude), alignment);
    float* dot = volk_malloc((size_t)batch_size * sizeof(*dot), alignment);
    lv_32fc_t* complex_output = volk_malloc(points * sizeof(*complex_output), alignment);
    lv_32fc_t* complex_dot = volk_malloc((size_t)batch_size * sizeof(*complex_dot), alignment);
    double* timings = calloc((size_t)iterations, sizeof(*timings));
    if (planar == NULL || a == NULL || b == NULL || magnitude == NULL || dot == NULL ||
        complex_output == NULL || complex_dot == NULL || timings == NULL) {
        fprintf(stderr, "allocation failed\n");
        return 3;
    }
    if (!read_floats(input_path, planar, input_count)) {
        return 4;
    }
    for (unsigned int batch = 0; batch < batch_size; ++batch) {
        const size_t planar_base = (size_t)batch * (size_t)(four_channels ? 4 : 2) * length;
        const size_t complex_base = (size_t)batch * length;
        for (unsigned int index = 0; index < length; ++index) {
            a[complex_base + index] =
                planar[planar_base + index] + I * planar[planar_base + length + index];
            if (four_channels) {
                b[complex_base + index] = planar[planar_base + 2U * length + index] +
                                          I * planar[planar_base + 3U * length + index];
            }
        }
    }

    for (int index = 0; index < warmup; ++index) {
        run_kernel(kernel,
                   implementation,
                   batch_size,
                   length,
                   planar,
                   a,
                   b,
                   magnitude,
                   dot,
                   complex_output,
                   complex_dot);
    }
    struct timespec process_start;
    struct timespec process_end;
    clock_gettime(CLOCK_PROCESS_CPUTIME_ID, &process_start);
    for (int index = 0; index < iterations; ++index) {
        struct timespec start;
        struct timespec end;
        clock_gettime(CLOCK_MONOTONIC_RAW, &start);
        run_kernel(kernel,
                   implementation,
                   batch_size,
                   length,
                   planar,
                   a,
                   b,
                   magnitude,
                   dot,
                   complex_output,
                   complex_dot);
        clock_gettime(CLOCK_MONOTONIC_RAW, &end);
        timings[index] = elapsed_ms(start, end);
    }
    clock_gettime(CLOCK_PROCESS_CPUTIME_ID, &process_end);

    float* output_values = NULL;
    size_t output_count = 0;
    if (strcmp(kernel, "magnitude_squared") == 0) {
        output_values = magnitude;
        output_count = points;
    } else if (strcmp(kernel, "dot_product") == 0) {
        output_values = dot;
        output_count = batch_size;
    } else {
        output_count = strcmp(kernel, "multiply_conjugate") == 0 ? points * 2U : batch_size * 2U;
        output_values = volk_malloc(output_count * sizeof(*output_values), alignment);
        const lv_32fc_t* source =
            strcmp(kernel, "multiply_conjugate") == 0 ? complex_output : complex_dot;
        const size_t per_batch = strcmp(kernel, "multiply_conjugate") == 0 ? length : 1U;
        for (unsigned int batch = 0; batch < batch_size; ++batch) {
            for (size_t index = 0; index < per_batch; ++index) {
                const size_t source_index = (size_t)batch * per_batch + index;
                const size_t target_base = (size_t)batch * 2U * per_batch;
                output_values[target_base + index] = crealf(source[source_index]);
                output_values[target_base + per_batch + index] = cimagf(source[source_index]);
            }
        }
    }
    if (!write_floats(output_path, output_values, output_count)) {
        fprintf(stderr, "unable to write output\n");
        return 5;
    }

    double mean = 0.0;
    double minimum = timings[0];
    double maximum = timings[0];
    for (int index = 0; index < iterations; ++index) {
        mean += timings[index];
        if (timings[index] < minimum) minimum = timings[index];
        if (timings[index] > maximum) maximum = timings[index];
    }
    mean /= (double)iterations;
    printf("{\"kernel\":\"%s\",\"implementation\":\"%s\",", kernel, implementation);
    printf("\"batch_size\":%u,\"vector_length\":%u,\"warmup\":%d,\"iterations\":%d,",
           batch_size,
           length,
           warmup,
           iterations);
    printf("\"mean_ms\":%.9f,\"p50_ms\":%.9f,\"p95_ms\":%.9f,",
           mean,
           percentile(timings, iterations, 0.50),
           percentile(timings, iterations, 0.95));
    printf("\"min_ms\":%.9f,\"max_ms\":%.9f,\"process_cpu_ms\":%.9f,\"dispatcher_machine\":\"%s\",\"available_implementations\":",
           minimum,
           maximum,
           elapsed_ms(process_start, process_end),
           volk_get_machine());
    print_json_implementation_names(descriptor);
    printf(",\"timings_ms\":[");
    for (int index = 0; index < iterations; ++index) {
        printf(index == 0 ? "%.9f" : ",%.9f", timings[index]);
    }
    printf("]}\n");

    if (output_values != magnitude && output_values != dot) volk_free(output_values);
    free(timings);
    volk_free(complex_dot);
    volk_free(complex_output);
    volk_free(dot);
    volk_free(magnitude);
    volk_free(b);
    volk_free(a);
    volk_free(planar);
    return 0;
}
