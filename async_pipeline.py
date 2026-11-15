# ====== Module: async_pipeline.py ======
# async_pipeline.py
"""
异步运动分析流水线（双缓冲）。

完整检测由两段组成：
1. GPU：角点 + 两次稀疏光流 + 对齐（约 1.5~2ms，cv2 CUDA 调用会释放 GIL）；
2. CPU：对齐差分、连通域、masked_corr、统计（约 1.5~2ms，numpy/OpenCV CPU）。

串行时这两段相加；本模块把第 1 段放到独立线程，主线程在「提交第 k 帧」
之后立刻去结算「第 k-1 帧」的第 2 段。此时 GPU 线程正在算第 k 帧，
于是 GPU 计算与 CPU 后处理重叠。

约束：
- 结果按提交顺序返回，主线程也按同样顺序结算，统计顺序不会乱；
- 每条记录用帧自己的时间戳，滑动窗口/总张数不受滞后一帧影响；
- 只有主线程做 GPU 之外的 CPU 工作，GPU 只由 worker 线程访问，
  避免同一张卡上的默认流跨线程竞争。
"""
import queue
import threading


class MotionPipeline:
    """
    把运动分析函数放到独立线程，按提交顺序返回结果。

    analyzer 由调用方传入（通常是 detection.unified_motion_analysis）。
    这里不直接 import detection，是为了让 build.py 合并单文件时不会
    因为内部 import 被剥离而丢掉依赖。
    """

    def __init__(self, analyzer, gpu_ctx, lk_cuda=None, use_optical_flow=True):
        self._analyze = analyzer
        self.gpu_ctx = gpu_ctx
        self.lk_cuda = lk_cuda
        self.use_optical_flow = use_optical_flow
        self._in = queue.Queue(maxsize=4)
        self._out = queue.Queue(maxsize=4)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while True:
            item = self._in.get()
            if item is None:
                break
            token, prev, curr = item
            try:
                motion = self._analyze(
                    prev, curr, self.use_optical_flow,
                    use_gpu=self.gpu_ctx is not None,
                    lk_cuda=self.lk_cuda, gpu_ctx=self.gpu_ctx)
                self._out.put((token, motion, None))
            except Exception as exc:  # noqa: BLE001
                self._out.put((token, None, exc))

    def submit(self, token, prev, curr):
        self._in.put((token, prev, curr))

    def collect(self, timeout=None):
        """取最早提交的结果（阻塞直到可用）。"""
        token, motion, err = self._out.get(timeout=timeout)
        if err is not None:
            raise err
        return token, motion

    def close(self):
        try:
            self._in.put_nowait(None)
        except queue.Full:
            pass
        self._thread.join(timeout=2)
