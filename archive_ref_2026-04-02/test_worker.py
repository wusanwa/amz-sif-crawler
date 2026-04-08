import os
import asyncio
from crawler_worker import main_worker

# 临时测试 URL
test_url = "https://www.amazon.com/dp/B0CDX5XGLK"

if __name__ == "__main__":
    print(f"Testing with URL: {test_url}")
    try:
        # 指定输出文件为 batch_results_v3.json
        # main_worker 内部自带锁机制，可以直接传入参数
        asyncio.run(main_worker(manual_urls=[test_url], debug_mode=False, outfile="test_worker_results.json"))
        print(f"Success! Results saved to test_worker_results.json")
    except Exception as e:
        print(f"Test failed with error: {e}")
