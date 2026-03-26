import kagglehub
import shutil
import os

PREDICTION_DIR = os.environ.get("PREDICTION_DIR", "/app/prediction")

# 下载数据集
print("开始下载 Polymarket 预测市场数据集...")
path = kagglehub.dataset_download("ismetsemedov/polymarket-prediction-markets")
print(f"下载完成，缓存路径: {path}")

# 复制到挂载目录
output_dir = PREDICTION_DIR
if os.path.exists(output_dir):
    # 复制所有文件到挂载目录
    for item in os.listdir(path):
        src = os.path.join(path, item)
        dst = os.path.join(output_dir, item)
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)
    print(f"数据已复制到 /data 目录")

# 列出文件
print("\n数据文件列表:")
for item in os.listdir(output_dir):
    size = os.path.getsize(os.path.join(output_dir, item))
    print(f"  {item} ({size / 1024 / 1024:.1f} MB)")

print("\n完成！")
