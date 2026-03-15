# -*- coding:utf-8 -*-
import os
import queue
import numpy as np
import acl
import cv2

# 内存拷贝类型常量
MEMCPY_KIND = {
    "HOST_TO_HOST": 0,
    "HOST_TO_DEVICE": 1,
    "DEVICE_TO_HOST": 2,
    "DEVICE_TO_DEVICE": 3
}


class VideoEncoder:
    """
    昇腾硬件视频编码器封装类
    支持 H.264/H.265 编码，输入 NV12 格式图像，输出裸流包
    """

    def __init__(self, width, height, fps=30, codec='h264', bitrate=None, gop=16, device_id=0):
        """
        初始化编码器
        :param width: 图像宽度（需为偶数）
        :param height: 图像高度（需为偶数）
        :param fps: 帧率
        :param codec: 编码格式，'h264' 或 'h265'
        :param bitrate: 码率（bps），可选
        :param gop: 关键帧间隔
        :param device_id: 昇腾设备ID
        """
        self.width = width
        self.height = height
        self.fps = fps
        self.gop = gop
        self.bitrate = bitrate
        self.device_id = device_id
        self.running = False
        self.frame_count = 0
        self.output_queue = queue.Queue(maxsize=60)  # 编码输出包队列
        self.callback_run_flag = False
        self.cb_thread_id = None
        self.frame_config = None
        self.venc_channel_desc = None
        self.context = None

        # 编码类型映射
        if codec == 'h265':
            self.entype = 0  # H.265 Main
        elif codec == 'h264':
            self.entype = 2  # H.264 Main (也可选 1:baseline, 3:high)
        else:
            raise ValueError("codec must be 'h264' or 'h265'")

        # 像素格式：NV12 = 1, NV21 = 2
        self.pix_format = 1  # NV12

        # 初始化ACL（全局只需一次）
        self._init_acl()

        # 创建Context
        self.context, ret = acl.rt.create_context(self.device_id)
        if ret != 0:
            raise RuntimeError(f"acl.rt.create_context failed: {ret}")

        # 创建编码通道描述符
        self.venc_channel_desc = acl.media.venc_create_channel_desc()
        if self.venc_channel_desc is None:
            raise RuntimeError("acl.media.venc_create_channel_desc failed")

        # 启动回调处理线程
        self.callback_run_flag = True
        self.cb_thread_id, ret = acl.util.start_thread(
            self._cb_thread_func, [self.context, 1000]  # timeout=1000ms
        )
        if ret != 0:
            raise RuntimeError(f"acl.util.start_thread failed: {ret}")

        # 将线程ID设置到通道描述符
        acl.media.venc_set_channel_desc_thread_id(self.venc_channel_desc, self.cb_thread_id)

        # 设置通道参数
        self._set_channel_params()

        # 创建编码通道
        ret = acl.media.venc_create_channel(self.venc_channel_desc)
        if ret != 0:
            raise RuntimeError(f"acl.media.venc_create_channel failed: {ret}")

        # 创建帧配置对象
        self.frame_config = acl.media.venc_create_frame_config()
        if self.frame_config is None:
            raise RuntimeError("acl.media.venc_create_frame_config failed")

        self.running = True
        print(f"VideoEncoder initialized: {width}x{height} {codec}, gop={gop}")

    def _init_acl(self):
        """初始化ACL（单例模式）"""
        if not hasattr(VideoEncoder, '_acl_inited'):
            ret = acl.init()
            if ret != 0:
                raise RuntimeError(f"acl.init failed: {ret}")
            ret = acl.rt.set_device(self.device_id)
            if ret != 0:
                raise RuntimeError(f"acl.rt.set_device failed: {ret}")
            VideoEncoder._acl_inited = True
            # 获取运行模式（可选）
            run_mode, ret = acl.rt.get_run_mode()
            if ret == 0:
                print(f"ACL run mode: {run_mode}")

    def _set_channel_params(self):
        """设置编码通道参数"""
        # 回调函数
        acl.media.venc_set_channel_desc_callback(self.venc_channel_desc, self._venc_callback)
        # 编码类型
        acl.media.venc_set_channel_desc_entype(self.venc_channel_desc, self.entype)
        # 像素格式
        acl.media.venc_set_channel_desc_pic_format(self.venc_channel_desc, self.pix_format)
        # 关键帧间隔
        acl.media.venc_set_channel_desc_key_frame_interval(self.venc_channel_desc, self.gop)
        # 图像宽高
        acl.media.venc_set_channel_desc_pic_width(self.venc_channel_desc, self.width)
        acl.media.venc_set_channel_desc_pic_height(self.venc_channel_desc, self.height)
        # 可选的码率设置（如果接口存在）
        if self.bitrate is not None and hasattr(acl.media, 'venc_set_channel_desc_bit_rate'):
            acl.media.venc_set_channel_desc_bit_rate(self.venc_channel_desc, self.bitrate)

    def _venc_callback(self, input_pic_desc, output_stream_desc, user_data):
        """
        编码完成回调函数（在ACL内部线程中调用）
        :param input_pic_desc: 输入图像描述符（本实现未使用）
        :param output_stream_desc: 输出码流描述符
        :param user_data: 用户数据，即输入内存地址
        """
        if output_stream_desc == 0:
            return

        # 获取输出数据
        stream_data = acl.media.dvpp_get_stream_desc_data(output_stream_desc)
        if stream_data is None:
            if user_data != 0:
                acl.media.dvpp_free(user_data)
            return

        ret_code = acl.media.dvpp_get_stream_desc_ret_code(output_stream_desc)
        if ret_code != 0:
            if user_data != 0:
                acl.media.dvpp_free(user_data)
            return  # 编码失败

        stream_size = acl.media.dvpp_get_stream_desc_size(output_stream_desc)
        if stream_size == 0:
            if user_data != 0:
                acl.media.dvpp_free(user_data)
            return

        # 将编码后的数据从Device拷贝到Host
        if hasattr(acl.util, 'bytes_to_ptr'):
            data = bytes(stream_size)
            data_ptr = acl.util.bytes_to_ptr(data)
        else:
            data = np.empty(stream_size, dtype=np.uint8)
            data_ptr = acl.util.numpy_to_ptr(data)
        ret = acl.rt.memcpy(
            data_ptr, stream_size,
            stream_data, stream_size,
            MEMCPY_KIND["DEVICE_TO_HOST"]
        )
        if ret != 0:
            if user_data != 0:
                acl.media.dvpp_free(user_data)
            return

        # 放入输出队列
        try:
            self.output_queue.put(data if isinstance(data, bytes) else data.tobytes(), block=False)
        except queue.Full:
            print("Warning: output queue full, dropping packet")

        # 释放输入内存（user_data 指向输入内存地址）
        if user_data != 0:
            acl.media.dvpp_free(user_data)

    def _cb_thread_func(self, args_list):
        """
        回调处理线程函数，不断调用 acl.rt.process_report 处理异步事件
        """
        context = args_list[0]  # 这里未使用，线程内创建自己的context
        timeout = args_list[1]

        # 线程内创建context
        ctx, ret = acl.rt.create_context(self.device_id)
        if ret != 0:
            print(f"cb_thread_func create_context failed: {ret}")
            return

        while self.callback_run_flag:
            ret = acl.rt.process_report(timeout)
            # 如果返回非0，可能是超时或错误，继续循环

        acl.rt.destroy_context(ctx)

    def encode(self, frame):
        """
        提交一帧图像进行编码
        :param frame: bytes 或 numpy.ndarray，NV12格式图像数据
                      大小应为 width * height * 3 // 2
        """
        if not self.running:
            raise RuntimeError("Encoder is not running")

        # 转换为bytes
        if isinstance(frame, np.ndarray):
            frame = frame.tobytes()
        elif not isinstance(frame, (bytes, bytearray)):
            raise TypeError("frame must be bytes or numpy array")

        input_size = len(frame)
        expected_size = self.width * self.height * 3 // 2
        if input_size != expected_size:
            raise ValueError(f"Invalid frame size: {input_size}, expected {expected_size} for NV12 {self.width}x{self.height}")

        # 分配Device内存
        input_mem, ret = acl.media.dvpp_malloc(input_size)
        if ret != 0:
            raise RuntimeError(f"acl.media.dvpp_malloc failed: {ret}")

        # 拷贝数据到Device
        host_ptr = acl.util.bytes_to_ptr(frame)
        ret = acl.rt.memcpy(
            input_mem, input_size,
            host_ptr, input_size,
            MEMCPY_KIND["HOST_TO_DEVICE"]
        )
        if ret != 0:
            acl.media.dvpp_free(input_mem)
            raise RuntimeError(f"acl.rt.memcpy failed: {ret}")

        # 创建图像描述符
        pic_desc = acl.media.dvpp_create_pic_desc()
        if pic_desc is None:
            acl.media.dvpp_free(input_mem)
            raise RuntimeError("acl.media.dvpp_create_pic_desc failed")

        # 设置图像描述符
        acl.media.dvpp_set_pic_desc_data(pic_desc, input_mem)
        acl.media.dvpp_set_pic_desc_size(pic_desc, input_size)
        acl.media.dvpp_set_pic_desc_width(pic_desc, self.width)
        acl.media.dvpp_set_pic_desc_height(pic_desc, self.height)
        acl.media.dvpp_set_pic_desc_format(pic_desc, self.pix_format)
        acl.media.dvpp_set_pic_desc_width_stride(pic_desc, self.width)  # 步长通常等于宽度
        if hasattr(acl.media, 'dvpp_set_pic_desc_height_stride'):
            acl.media.dvpp_set_pic_desc_height_stride(pic_desc, self.height)

        # 设置帧配置（强制关键帧间隔）
        force_iframe = 1 if (self.frame_count % self.gop == 0) else 0
        acl.media.venc_set_frame_config_eos(self.frame_config, 0)
        acl.media.venc_set_frame_config_force_i_frame(self.frame_config, force_iframe)

        # 发送帧，user_data传入input_mem以便回调释放
        ret = acl.media.venc_send_frame(
            self.venc_channel_desc,
            pic_desc,
            0,  # 预留参数，传0
            self.frame_config,
            input_mem   # user_data
        )
        if ret != 0:
            acl.media.dvpp_destroy_pic_desc(pic_desc)
            acl.media.dvpp_free(input_mem)
            raise RuntimeError(f"acl.media.venc_send_frame failed: {ret}")

        # 销毁图像描述符（发送后不再需要）
        acl.media.dvpp_destroy_pic_desc(pic_desc)

        self.frame_count += 1

    def get_packet(self, block=True, timeout=None):
        """
        获取编码后的数据包
        :param block: 是否阻塞
        :param timeout: 超时时间（秒）
        :return: bytes 或 None（队列空且block=False）
        """
        try:
            return self.output_queue.get(block=block, timeout=timeout)
        except queue.Empty:
            return None

    def release(self):
        """释放资源"""
        if not getattr(self, 'running', False) and not getattr(self, 'venc_channel_desc', None):
            return

        self.running = False

        # 发送EOS帧通知编码器结束
        if hasattr(self, 'frame_config') and self.frame_config:
            acl.media.venc_set_frame_config_eos(self.frame_config, 1)
            acl.media.venc_set_frame_config_force_i_frame(self.frame_config, 0)
            # 发送空帧作为EOS
            ret = acl.media.venc_send_frame(self.venc_channel_desc, 0, 0, self.frame_config, 0)
            if ret != 0:
                print(f"Warning: venc_send_frame EOS failed: {ret}")

        # 停止回调线程
        self.callback_run_flag = False
        if getattr(self, 'cb_thread_id', None):
            acl.util.stop_thread(self.cb_thread_id)
            self.cb_thread_id = None

        # 销毁通道
        if getattr(self, 'venc_channel_desc', None):
            acl.media.venc_destroy_channel(self.venc_channel_desc)
            acl.media.venc_destroy_channel_desc(self.venc_channel_desc)
            self.venc_channel_desc = None

        if getattr(self, 'frame_config', None):
            acl.media.venc_destroy_frame_config(self.frame_config)
            self.frame_config = None

        # 销毁Context
        if getattr(self, 'context', None):
            acl.rt.destroy_context(self.context)
            self.context = None

        print("VideoEncoder released")

    def __del__(self):
        self.release()
        


def bgr_to_nv12(bgr_img):
    """
    将BGR图像转换为NV12格式
    :param bgr_img: numpy array, shape (H, W, 3), dtype=np.uint8
    :return: bytes 类型的NV12数据
    """
    h, w = bgr_img.shape[:2]
    # 确保宽高为偶数
    if h % 2 != 0 or w % 2 != 0:
        raise ValueError("Image dimensions must be even")

    # BGR -> YUV I420
    yuv_i420 = cv2.cvtColor(bgr_img, cv2.COLOR_BGR2YUV_I420).reshape(-1)

    # 分离Y、U、V平面
    y_size = h * w
    uv_plane_size = (h // 2) * (w // 2)
    y = yuv_i420[:y_size]
    u = yuv_i420[y_size:y_size + uv_plane_size]
    v = yuv_i420[y_size + uv_plane_size:y_size + 2 * uv_plane_size]

    # 将U、V交错为UV平面 (NV12格式：Y平面后跟UV交错，UV排列为 [U V U V ...])
    uv = np.zeros((h//2, w), dtype=np.uint8)
    uv[:, 0::2] = u.reshape(h//2, w//2)  # 偶数列为U
    uv[:, 1::2] = v.reshape(h//2, w//2)  # 奇数列为V

    # 合并Y和UV
    nv12 = np.concatenate([y, uv.reshape(-1)])
    return nv12.tobytes()