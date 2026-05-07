# STM32 固件开发参考 — 多电机数据采集仪

本文档为 STM32 端固件开发提供参考，包括传感器接线、数据帧格式和
示例代码框架。

## 1. 传感器接线

```
STM32F407 (示例)
    │
    ├── I2C1 (PB6=SCL, PB7=SDA)
    │   ├── DS18B20 ×4  (温度传感器, 通过I2C转接模块)
    │   └── 或使用多个 OneWire GPIO (PA0~PA3)
    │
    ├── ADC1 (PA1~PA4)
    │   └── ACS712 ×4  (电流传感器, 模拟输出 0~5V)
    │
    ├── TIM2/TIM3 输入捕获 (PA6~PA9)
    │   └── 霍尔传感器 ×4  (转速, 脉冲计数)
    │
    └── USART1 (PA9=TX, PA10=RX)
        └── 连接 Ascend 310B UART (3.3V 电平)
```

## 2. UART 通信协议

### 物理层

- 接口：UART (3.3V TTL)
- 波特率：115200
- 数据位：8
- 停止位：1
- 校验：无 (8N1)

### 数据帧格式

STM32 每秒发送一行 ASCII 文本，以 `\r\n` 结尾：

```
M0:T=42.5,C=1.25,R=3200|M1:T=38.2,C=0.85,R=2950|M2:T=45.1,C=1.52,R=3100|M3:T=40.3,C=0.92,R=3050
```

字段说明：

| 字段 | 含义 | 单位 | 示例 |
|------|------|------|------|
| M0~M3 | 电机编号 | — | M0 = 电机1 |
| T | 温度 | °C | T=42.5 |
| C | 电流 | A | C=1.25 |
| R | 转速 | RPM | R=3200 |
| \| | 电机分隔符 | — | |
| \\r\\n | 帧结束 | — | |

## 3. Arduino 示例代码 (快速原型)

```cpp
// Multi-Motor Data Acquisition — STM32 / Arduino
// Reads 4 motors: temperature, current, RPM
// Sends data frame over Serial1 every second

#include <OneWire.h>
#include <DallasTemperature.h>

// --- Pin Definitions ---
const int TEMP_PINS[4] = {PA0, PA1, PA2, PA3};  // DS18B20
const int CUR_PINS[4]  = {PA4, PA5, PA6, PA7};  // ACS712 ADC
const int HALL_PINS[4] = {PB0, PB1, PB2, PB3};  // Hall sensor RPM

// --- Globals ---
OneWire oneWire[4] = {
    OneWire(TEMP_PINS[0]), OneWire(TEMP_PINS[1]),
    OneWire(TEMP_PINS[2]), OneWire(TEMP_PINS[3])
};
DallasTemperature sensors[4] = {
    DallasTemperature(&oneWire[0]), DallasTemperature(&oneWire[1]),
    DallasTemperature(&oneWire[2]), DallasTemperature(&oneWire[3])
};

volatile unsigned long pulse_count[4] = {0, 0, 0, 0};
unsigned long last_rpm_time = 0;

// --- RPM interrupt handlers ---
void pulse0() { pulse_count[0]++; }
void pulse1() { pulse_count[1]++; }
void pulse2() { pulse_count[2]++; }
void pulse3() { pulse_count[3]++; }

void setup() {
    Serial1.begin(115200);  // UART to Ascend 310B

    // Init temperature sensors
    for (int i = 0; i < 4; i++) {
        sensors[i].begin();
    }

    // Init RPM interrupts
    attachInterrupt(digitalPinToInterrupt(HALL_PINS[0]), pulse0, RISING);
    attachInterrupt(digitalPinToInterrupt(HALL_PINS[1]), pulse1, RISING);
    attachInterrupt(digitalPinToInterrupt(HALL_PINS[2]), pulse2, RISING);
    attachInterrupt(digitalPinToInterrupt(HALL_PINS[3]), pulse3, RISING);

    last_rpm_time = millis();
}

void loop() {
    // --- Read temperatures ---
    float temps[4] = {0};
    for (int i = 0; i < 4; i++) {
        sensors[i].requestTemperatures();
        temps[i] = sensors[i].getTempCByIndex(0);
    }

    // --- Read currents (ACS712: 185 mV/A, 2.5V offset at 0A) ---
    float currents[4] = {0};
    for (int i = 0; i < 4; i++) {
        int raw = analogRead(CUR_PINS[i]);
        float voltage = raw * (3.3 / 4095.0);  // 12-bit ADC
        currents[i] = (voltage - 1.65) / 0.185;  // ACS712-5A
        if (currents[i] < 0) currents[i] = 0;
    }

    // --- Compute RPM (pulses per second / pulses_per_rev) ---
    unsigned long now = millis();
    float elapsed = (now - last_rpm_time) / 1000.0;
    float rpms[4] = {0};
    for (int i = 0; i < 4; i++) {
        rpms[i] = (pulse_count[i] / elapsed) * 60.0 / 2.0;  // 2 pulses/rev
        pulse_count[i] = 0;
    }
    last_rpm_time = now;

    // --- Build data frame ---
    Serial1.print("M0:T="); Serial1.print(temps[0]);
    Serial1.print(",C="); Serial1.print(currents[0]);
    Serial1.print(",R="); Serial1.print(rpms[0]);

    for (int i = 1; i < 4; i++) {
        Serial1.print("|M"); Serial1.print(i);
        Serial1.print(":T="); Serial1.print(temps[i]);
        Serial1.print(",C="); Serial1.print(currents[i]);
        Serial1.print(",R="); Serial1.print(rpms[i]);
    }
    Serial1.println();

    delay(1000);  // 1 Hz update rate
}
```

## 4. STM32CubeIDE 代码框架 (低功耗优化版)

```c
// main.c — Multi-motor sensor DAQ for STM32F407
// Uses DMA for ADC, TIM for RPM capture, UART DMA for TX

#include "main.h"

#define NUM_MOTORS 4

// --- Peripherals ---
ADC_HandleTypeDef hadc1;
TIM_HandleTypeDef htim2, htim3, htim4, htim5;  // RPM capture
UART_HandleTypeDef huart1;  // TX to Ascend 310B

// --- Buffers ---
volatile uint32_t rpm_captures[NUM_MOTORS] = {0};
float temperatures[NUM_MOTORS] = {0};
float currents[NUM_MOTORS] = {0};
float rpms[NUM_MOTORS] = {0};

// --- UART TX buffer ---
char tx_buffer[256];

void build_and_send_frame(void) {
    int len = 0;
    for (int i = 0; i < NUM_MOTORS; i++) {
        len += snprintf(tx_buffer + len, sizeof(tx_buffer) - len,
                        "%sM%d:T=%.1f,C=%.2f,R=%.0f",
                        i > 0 ? "|" : "", i,
                        temperatures[i], currents[i], rpms[i]);
    }
    len += snprintf(tx_buffer + len, sizeof(tx_buffer) - len, "\r\n");
    HAL_UART_Transmit_DMA(&huart1, (uint8_t*)tx_buffer, len);
}

void main_loop(void) {
    while (1) {
        // 1. Read temperatures (DS18B20 via OneWire)
        read_temperatures(temperatures);

        // 2. Read currents (ADC DMA)
        read_currents(currents);

        // 3. Compute RPM from TIM capture
        compute_rpms(rpms, rpm_captures);

        // 4. Send frame
        build_and_send_frame();

        HAL_Delay(1000);  // 1 Hz
    }
}
```

## 5. 调试建议

1. 先用 USB-TTL 串口工具连接 STM32，在 PC 上用串口助手确认数据帧格式
2. 确认波特率 115200，数据帧以 `\r\n` 结尾
3. 温度传感器如使用 DS18B20，注意 4.7kΩ 上拉电阻
4. ACS712 电流传感器注意零点校准（0A 时输出电压为 VCC/2）
5. 霍尔传感器注意上拉电阻和去抖电容
