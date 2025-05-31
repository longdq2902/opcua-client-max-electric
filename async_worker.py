# app/async_worker.py
import asyncio
import threading
import logging
from concurrent.futures import Future

logger = logging.getLogger(__name__)

class AsyncWorker:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(AsyncWorker, cls).__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            self.loop = None
            self.thread = None
            self._initialized = True

    def start(self):
        if self.thread is not None and self.thread.is_alive():
            logger.info("Async worker thread is already running.")
            return

        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        logger.info("Async worker thread started.")

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_forever()
        finally:
            self.loop.close()
            logger.info("Async worker event loop closed.")

    def stop(self):
        if self.loop and self.loop.is_running():
            logger.info("Stopping async worker event loop...")
            self.loop.call_soon_threadsafe(self.loop.stop)
        if self.thread and self.thread.is_alive():
            logger.info("Waiting for async worker thread to join...")
            self.thread.join(timeout=5) # Chờ thread kết thúc
            if self.thread.is_alive():
                logger.warning("Async worker thread did not join in time.")
        self.thread = None
        self.loop = None
        logger.info("Async worker stopped.")


    def run_coroutine(self, coro):
        """
        Chạy một coroutine trong event loop của worker và chờ kết quả (blocking).
        Hàm này an toàn để gọi từ một thread khác.
        """
        if not self.loop or not self.loop.is_running():
            logger.error("Async worker loop is not running. Cannot run coroutine.")
            # Có thể raise Exception ở đây hoặc khởi động lại worker nếu cần
            # self.start() # Cân nhắc việc tự động khởi động lại
            raise RuntimeError("Async worker loop is not available.")

        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        try:
            return future.result(timeout=50000)  # Đặt timeout hợp lý (ví dụ 30 giây)
        except asyncio.TimeoutError:
            logger.error(f"Coroutine execution timed out: {coro.__name__}")
            # Hủy future nếu timeout
            future.cancel()
            raise TimeoutError(f"Operation {coro.__name__} timed out.")
        except Exception as e:
            logger.error(f"Exception in coroutine {coro.__name__}: {e}", exc_info=True)
            raise

# Tạo một instance global của AsyncWorker
async_worker = AsyncWorker()

def get_async_worker():
    # Khởi động worker nếu chưa chạy, chỉ khi có yêu cầu đầu tiên
    # if not async_worker.loop or not async_worker.loop.is_running():
    #     async_worker.start() # Bỏ dòng này, sẽ start ở __init__.py của app
    return async_worker