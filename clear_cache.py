import os
import diskcache
import shutil

# 配置与 batch_crawler_v3_multilayer.py 保持一致
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache_db")

def clear_cache():
    print(f"🚀 准备清理缓存目录: {CACHE_DIR}")
    
    if os.path.exists(CACHE_DIR):
        try:
            # 方式 1: 使用 diskcache 自带的 clear 方法 (更优雅)
            cache = diskcache.Cache(CACHE_DIR)
            num_items = len(cache)
            cache.clear()
            cache.close()
            print(f"✅ 已清空 diskcache 中的 {num_items} 条记录")
            
            # 方式 2: 彻底删除目录 (更彻底，防止 SQLite 文件碎片)
            shutil.rmtree(CACHE_DIR)
            print(f"✅ 已彻底删除缓存目录")
        except Exception as e:
            print(f"❌ 清理失败: {e}")
    else:
        print("ℹ️ 缓存目录不存在，无需清理")

if __name__ == "__main__":
    confirm = input("确定要清空所有 Amazon 和 SIF 抓取缓存吗？(y/n): ")
    if confirm.lower() == 'y':
        clear_cache()
    else:
        print("操作已取消")
