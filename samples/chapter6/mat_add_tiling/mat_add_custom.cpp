/*
 * Ascend C 矩阵加法算子（带 Tiling）
 * 功能: C = A + B, 矩阵尺寸 M × N
 * 对应章节: chapter6 第5节
 *
 * Tiling 策略说明:
 * - 当矩阵数据量超过 UB 容量时，按行切分 Tile
 * - 每个 AI Core 处理一部分行（由 block_idx 决定）
 * - 行内按 tile_num 进一步切分，每次搬运 tileLength 个元素到 UB
 * - 使用双缓冲队列 (BUFFER_NUM=2) 隐藏数据搬运延迟
 */
#include "kernel_operator.h"
using namespace AscendC;

constexpr int32_t BUFFER_NUM = 2;   // Double Buffer

class KernelMatAdd {
public:
    __aicore__ inline KernelMatAdd() {}
    __aicore__ inline void Init(GM_ADDR a, GM_ADDR b, GM_ADDR c,
                                uint32_t totalLength, uint32_t tileNum) {
        ASSERT(GetBlockNum() != 0 && "block dim can not be zero!");
        this->blockLength = totalLength / GetBlockNum();
        this->tileNum = tileNum;
        ASSERT(tileNum != 0 && "tile num can not be zero!");
        this->tileLength = this->blockLength / tileNum / BUFFER_NUM;

        // 每个 Core 处理 [block_idx * blockLength, (block_idx+1) * blockLength)
        aGm.SetGlobalBuffer((__gm__ float *)a + this->blockLength * GetBlockIdx(),
                            this->blockLength);
        bGm.SetGlobalBuffer((__gm__ float *)b + this->blockLength * GetBlockIdx(),
                            this->blockLength);
        cGm.SetGlobalBuffer((__gm__ float *)c + this->blockLength * GetBlockIdx(),
                            this->blockLength);

        pipe.InitBuffer(inQueueA, BUFFER_NUM, this->tileLength * sizeof(float));
        pipe.InitBuffer(inQueueB, BUFFER_NUM, this->tileLength * sizeof(float));
        pipe.InitBuffer(outQueueC, BUFFER_NUM, this->tileLength * sizeof(float));
    }

    __aicore__ inline void Process() {
        // Tiling 主循环：逐 Tile 执行 CopyIn → Compute → CopyOut
        int32_t loopCount = this->tileNum * BUFFER_NUM;
        for (int32_t i = 0; i < loopCount; i++) {
            CopyIn(i);
            Compute(i);
            CopyOut(i);
        }
    }

private:
    __aicore__ inline void CopyIn(int32_t progress) {
        LocalTensor<float> aLocal = inQueueA.AllocTensor<float>();
        LocalTensor<float> bLocal = inQueueB.AllocTensor<float>();
        DataCopy(aLocal, aGm[progress * this->tileLength], this->tileLength);
        DataCopy(bLocal, bGm[progress * this->tileLength], this->tileLength);
        inQueueA.EnQue(aLocal);
        inQueueB.EnQue(bLocal);
    }

    __aicore__ inline void Compute(int32_t progress) {
        LocalTensor<float> aLocal = inQueueA.DeQue<float>();
        LocalTensor<float> bLocal = inQueueB.DeQue<float>();
        LocalTensor<float> cLocal = outQueueC.AllocTensor<float>();
        Add(cLocal, aLocal, bLocal, this->tileLength);
        outQueueC.EnQue<float>(cLocal);
        inQueueA.FreeTensor(aLocal);
        inQueueB.FreeTensor(bLocal);
    }

    __aicore__ inline void CopyOut(int32_t progress) {
        LocalTensor<float> cLocal = outQueueC.DeQue<float>();
        DataCopy(cGm[progress * this->tileLength], cLocal, this->tileLength);
        outQueueC.FreeTensor(cLocal);
    }

private:
    TPipe pipe;
    TQue<QuePosition::VECIN, BUFFER_NUM> inQueueA, inQueueB;
    TQue<QuePosition::VECOUT, BUFFER_NUM> outQueueC;
    GlobalTensor<float> aGm, bGm, cGm;
    uint32_t blockLength = 0;
    uint32_t tileNum = 0;
    uint32_t tileLength = 0;
};

extern "C" __global__ __aicore__ void mat_add_custom(
    GM_ADDR a, GM_ADDR b, GM_ADDR c, GM_ADDR workspace, GM_ADDR tiling) {
    GET_TILING_DATA(tilingData, tiling);
    KernelMatAdd op;
    op.Init(a, b, c, tilingData.totalLength, tilingData.tileNum);
    if (TILING_KEY_IS(1)) {
        op.Process();
    }
}
